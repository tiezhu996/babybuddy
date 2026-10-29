# -*- coding: utf-8 -*-
import io
import tempfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from babybuddy.settings.base import positive_int
from core import models
from webhooks.models import WebhookEndpoint, WebhookEvent


def image_upload(name="test.png"):
    file = io.BytesIO()
    Image.new("RGB", (10, 10), "blue").save(file, format="PNG")
    return SimpleUploadedFile(name, file.getvalue(), content_type="image/png")


class SoftDeleteModelTestCase(TestCase):
    def setUp(self):
        self.child = models.Child.objects.create(
            first_name="First", last_name="Last", birth_date=timezone.localdate()
        )
        self.now = timezone.localtime()
        self.feeding = models.Feeding.objects.create(
            child=self.child,
            start=self.now - timezone.timedelta(minutes=30),
            end=self.now,
            type="formula",
            method="bottle",
            amount=90,
            notes="keep me",
        )
        self.feeding.tags.add("morning", "bottle")
        self.sleep = models.Sleep.objects.create(
            child=self.child,
            start=self.now - timezone.timedelta(hours=2),
            end=self.now - timezone.timedelta(hours=1),
            notes="long nap",
        )
        self.sleep.tags.add("nap")

    def test_instance_delete_is_a_soft_delete(self):
        pk = self.feeding.pk
        self.feeding.delete()

        self.assertFalse(models.Feeding.objects.filter(pk=pk).exists())
        trashed = models.Feeding.all_objects.get(pk=pk)
        self.assertIsNotNone(trashed.deleted_at)
        self.assertEqual(trashed.child_id, self.child.id)
        self.assertEqual(trashed.start, self.feeding.start)
        self.assertEqual(trashed.end, self.feeding.end)
        self.assertEqual(trashed.type, "formula")
        self.assertEqual(trashed.method, "bottle")
        self.assertEqual(trashed.amount, 90)
        self.assertEqual(trashed.notes, "keep me")
        self.assertEqual(set(trashed.tags.names()), {"morning", "bottle"})

    def test_queryset_delete_is_a_soft_delete(self):
        self.assertEqual(models.Sleep.objects.count(), 1)
        models.Sleep.objects.filter(pk=self.sleep.pk).delete()

        self.assertEqual(models.Sleep.objects.count(), 0)
        trashed = models.Sleep.all_objects.get(pk=self.sleep.pk)
        self.assertIsNotNone(trashed.deleted_at)
        self.assertEqual(set(trashed.tags.names()), {"nap"})

    def test_trashed_rows_leave_related_managers(self):
        self.feeding.delete()
        self.assertNotIn(self.feeding, self.child.feeding.all())

    def test_active_and_trashed_helpers(self):
        self.feeding.delete()
        self.assertEqual(models.Feeding.all_objects.active().count(), 0)
        self.assertEqual(models.Feeding.all_objects.trashed().count(), 1)

    def test_restore_brings_back_the_same_row(self):
        self.feeding.delete()
        self.sleep.delete()

        trashed = models.Feeding.all_objects.get(pk=self.feeding.pk)
        trashed.restore()

        restored = models.Feeding.objects.get(pk=self.feeding.pk)
        self.assertEqual(restored.pk, self.feeding.pk)
        self.assertIsNone(restored.deleted_at)
        self.assertEqual(restored.notes, "keep me")
        self.assertEqual(set(restored.tags.names()), {"morning", "bottle"})
        self.assertIn(restored, self.child.feeding.all())

        trashed_sleep = models.Sleep.all_objects.get(pk=self.sleep.pk)
        trashed_sleep.restore()
        self.assertEqual(models.Sleep.objects.get(pk=self.sleep.pk).notes, "long nap")

    def test_a_failing_receiver_does_not_block_restore(self):
        from core.signals import entry_restored

        def bad_receiver(sender, **kwargs):
            raise RuntimeError("boom")

        entry_restored.connect(
            bad_receiver, sender=models.Feeding, dispatch_uid="test-bad-receiver"
        )
        try:
            self.feeding.delete()
            with self.assertLogs("core.models", level="ERROR"):
                models.Feeding.all_objects.get(pk=self.feeding.pk).restore()
            # The restore itself still committed.
            self.assertTrue(
                models.Feeding.objects.filter(pk=self.feeding.pk).exists()
            )
        finally:
            entry_restored.disconnect(
                bad_receiver,
                sender=models.Feeding,
                dispatch_uid="test-bad-receiver",
            )

    def test_restore_is_idempotent(self):
        self.feeding.restore()
        # The row was never in the trash, so nothing happened.
        self.assertEqual(models.Feeding.objects.count(), 1)
        self.assertIsNone(models.Feeding.all_objects.get().deleted_at)

        self.feeding.delete()
        trashed = models.Feeding.all_objects.get(pk=self.feeding.pk)
        trashed.restore()
        trashed.restore()
        self.assertEqual(models.Feeding.objects.count(), 1)
        self.assertEqual(models.Feeding.all_objects.count(), 1)

    def test_hard_delete_removes_the_row(self):
        self.feeding.delete()
        self.feeding.hard_delete()
        self.assertFalse(models.Feeding.all_objects.filter(pk=self.feeding.pk).exists())

        self.sleep.hard_delete()
        self.assertFalse(models.Sleep.all_objects.filter(pk=self.sleep.pk).exists())


class TrashedNoteImageTestCase(TestCase):
    def setUp(self):
        self.child = models.Child.objects.create(
            first_name="First", last_name="Last", birth_date=timezone.localdate()
        )

    def test_image_survives_trash_and_restore_but_not_hard_delete(self):
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                note = models.Note.objects.create(
                    child=self.child,
                    note="with picture",
                    time=timezone.localtime(),
                    image=image_upload(),
                )
                name = note.image.name
                self.assertTrue(note.image.storage.exists(name))

                note.delete()
                trashed = models.Note.all_objects.get(pk=note.pk)
                self.assertEqual(trashed.image.name, name)
                self.assertTrue(trashed.image.storage.exists(name))

                trashed.restore()
                restored = models.Note.objects.get(pk=note.pk)
                self.assertEqual(restored.image.name, name)
                self.assertTrue(restored.image.storage.exists(name))

                restored.delete()
                models.Note.all_objects.get(pk=note.pk).hard_delete()
                self.assertFalse(models.Note.all_objects.filter(pk=note.pk).exists())
                self.assertFalse(restored.image.storage.exists(name))


class TrashRetentionSettingTestCase(TestCase):
    def test_default_retention_is_30_days(self):
        from django.conf import settings

        self.assertEqual(settings.BABY_BUDDY["TRASH_RETENTION_DAYS"], 30)

    def test_positive_int_parser(self):
        self.assertEqual(positive_int("7"), 7)
        for invalid in ("0", "-1", "abc"):
            with self.assertRaises(ValueError):
                positive_int(invalid)


class TrashViewsTestCase(TestCase):
    fixtures = ["tests.json"]

    def setUp(self):
        self.child = models.Child.objects.first()
        now = timezone.localtime()
        self.feeding = models.Feeding.objects.create(
            child=self.child,
            start=now - timezone.timedelta(minutes=30),
            end=now,
            type="formula",
            method="bottle",
            notes="undo me",
        )
        self.sleep = models.Sleep.objects.create(
            child=self.child,
            start=now - timezone.timedelta(hours=2),
            end=now - timezone.timedelta(hours=1),
            notes="restorable nap",
        )
        self.note = models.Note.objects.create(
            child=self.child, note="remember this", time=now
        )
        self.admin = get_user_model().objects.create_superuser(
            username="trash-admin", password="password", email="trash@example.com"
        )
        self.client.force_login(self.admin)

    def test_web_delete_moves_to_trash_and_undo_restores(self):
        cases = (
            (
                self.feeding,
                "/feedings/{}/delete/",
                "/feedings/{}/restore/",
                models.Feeding,
            ),
            (self.sleep, "/sleep/{}/delete/", "/sleep/{}/restore/", models.Sleep),
            (self.note, "/notes/{}/delete/", "/notes/{}/restore/", models.Note),
        )
        for instance, delete_path, restore_path, model in cases:
            with self.subTest(model=model.__name__):
                pk = instance.pk
                page = self.client.post(delete_path.format(pk), follow=True)
                self.assertEqual(page.status_code, 200)
                self.assertFalse(model.objects.filter(pk=pk).exists())
                self.assertTrue(
                    model.all_objects.filter(pk=pk, deleted_at__isnull=False).exists()
                )
                # The flash message carries the undo link.
                self.assertIn(restore_path.format(pk), page.content.decode())

                response = self.client.get(restore_path.format(pk))
                self.assertEqual(response.status_code, 302)
                self.assertTrue(model.objects.filter(pk=pk).exists())
                self.assertFalse(
                    model.all_objects.filter(pk=pk, deleted_at__isnull=False).exists()
                )

    def test_trash_page_lists_only_trashed_entries(self):
        self.feeding.delete()
        self.note.delete()
        page = self.client.get("/trash/")
        self.assertEqual(page.status_code, 200)
        content = page.content.decode()
        self.assertIn("undo me", content)
        self.assertIn("remember this", content)
        self.assertIn(
            "/feedings/{}/restore/?next=/trash/".format(self.feeding.pk), content
        )
        self.assertIn("/notes/{}/delete-now/".format(self.note.pk), content)
        # Sleep was not deleted, so it is not shown in the trash.
        self.assertNotIn("restorable nap", content)

    def test_restore_is_idempotent(self):
        self.feeding.delete()
        active_count = models.Feeding.objects.count()
        total_count = models.Feeding.all_objects.count()
        restore_url = "/feedings/{}/restore/".format(self.feeding.pk)
        self.assertEqual(self.client.get(restore_url).status_code, 302)
        self.assertEqual(models.Feeding.objects.count(), active_count + 1)
        # The row is no longer in the trash, so a second click finds nothing
        # to restore and cannot create a duplicate.
        self.assertEqual(self.client.get(restore_url).status_code, 404)
        self.assertEqual(models.Feeding.objects.count(), active_count + 1)
        self.assertEqual(models.Feeding.all_objects.count(), total_count)

    def test_restore_rejects_unknown_next_target(self):
        self.feeding.delete()
        response = self.client.get(
            "/feedings/{}/restore/?next=https://example.example/".format(
                self.feeding.pk
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/feedings/")

    def test_permanent_delete_removes_the_row_and_announces_it(self):
        WebhookEndpoint.objects.create(
            name="Home", url="http://home.test/hook", secret="secret"
        )
        self.feeding.delete()
        url = "/feedings/{}/delete-now/".format(self.feeding.pk)

        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(models.Feeding.all_objects.filter(pk=self.feeding.pk).exists())
        event = WebhookEvent.objects.latest("id")
        self.assertEqual(event.type, "feeding.deleted")
        self.assertEqual(event.object_id, str(self.feeding.pk))

    def test_soft_delete_leaves_no_delete_event(self):
        WebhookEndpoint.objects.create(
            name="Home", url="http://home.test/hook", secret="secret"
        )
        before = WebhookEvent.objects.count()
        self.client.post("/notes/{}/delete/".format(self.note.pk))
        self.assertEqual(WebhookEvent.objects.count(), before)


class TrashPermissionTestCase(TestCase):
    fixtures = ["tests.json"]

    def setUp(self):
        self.child = models.Child.objects.first()
        self.feeding = models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime() - timezone.timedelta(minutes=30),
            end=timezone.localtime(),
            type="formula",
            method="bottle",
        )
        self.feeding.delete()
        # Caregivers never have delete permission, so the trash is closed to
        # them even though they can view and edit entries.
        self.user = get_user_model().objects.create_user(username="trash-caregiver")
        self.user.groups.add(
            Group.objects.get(name=settings.BABY_BUDDY["CAREGIVER_GROUP_NAME"])
        )
        self.client.force_login(self.user)

    def test_caregiver_cannot_open_trash(self):
        self.assertEqual(self.client.get("/trash/").status_code, 403)

    def test_caregiver_cannot_restore(self):
        response = self.client.get("/feedings/{}/restore/".format(self.feeding.pk))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            models.Feeding.all_objects.filter(
                pk=self.feeding.pk, deleted_at__isnull=False
            ).exists()
        )

    def test_caregiver_cannot_delete_permanently(self):
        response = self.client.get("/feedings/{}/delete-now/".format(self.feeding.pk))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(models.Feeding.all_objects.filter(pk=self.feeding.pk).exists())
