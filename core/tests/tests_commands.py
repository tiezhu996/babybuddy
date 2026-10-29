# -*- coding: utf-8 -*-
import io
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from core import models
from core.trash import purge_expired_trash
from webhooks.models import WebhookEndpoint, WebhookEvent


def image_upload(name="test.png"):
    file = io.BytesIO()
    Image.new("RGB", (10, 10), "blue").save(file, format="PNG")
    return SimpleUploadedFile(name, file.getvalue(), content_type="image/png")


class PurgeTrashCommandTestCase(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.child = models.Child.objects.create(
            first_name="First", last_name="Last", birth_date=timezone.localdate()
        )

    def stamp_deleted(self, instance, days_ago):
        instance.delete()
        instance.__class__.all_objects.filter(pk=instance.pk).update(
            deleted_at=self.now - timezone.timedelta(days=days_ago)
        )

    def make_entries(self):
        recent = models.Feeding.objects.create(
            child=self.child,
            start=self.now - timezone.timedelta(minutes=30),
            end=self.now,
            type="formula",
            method="bottle",
        )
        old_sleep = models.Sleep.objects.create(
            child=self.child,
            start=self.now - timezone.timedelta(days=31, hours=2),
            end=self.now - timezone.timedelta(days=31, hours=1),
        )
        self.stamp_deleted(recent, 1)
        self.stamp_deleted(old_sleep, 31)
        return recent, old_sleep

    def test_purge_removes_only_entries_past_retention(self):
        recent, old_sleep = self.make_entries()
        # A live entry must never be touched.
        active = models.Sleep.objects.create(
            child=self.child,
            start=self.now - timezone.timedelta(hours=2),
            end=self.now - timezone.timedelta(hours=1),
        )

        total, counts = purge_expired_trash(now=self.now, retention_days=30)

        self.assertEqual(total, 1)
        self.assertEqual(counts, {"feeding": 0, "sleep": 1, "note": 0})
        self.assertTrue(models.Feeding.all_objects.filter(pk=recent.pk).exists())
        self.assertFalse(models.Sleep.all_objects.filter(pk=old_sleep.pk).exists())
        self.assertTrue(models.Sleep.objects.filter(pk=active.pk).exists())

    def test_boundary_uses_thirty_full_days(self):
        # Exactly 30 days old is still inside the period; 30 days and one
        # minute is past it.
        on_the_line = models.Note.objects.create(
            child=self.child, note="on the line", time=self.now
        )
        on_the_line.delete()
        models.Note.all_objects.filter(pk=on_the_line.pk).update(
            deleted_at=self.now - timezone.timedelta(days=30)
        )
        past = models.Note.objects.create(child=self.child, note="past", time=self.now)
        past.delete()
        models.Note.all_objects.filter(pk=past.pk).update(
            deleted_at=self.now - timezone.timedelta(days=30, minutes=1)
        )

        total, _ = purge_expired_trash(now=self.now, retention_days=30)

        self.assertEqual(total, 1)
        self.assertTrue(models.Note.all_objects.filter(pk=on_the_line.pk).exists())
        self.assertFalse(models.Note.all_objects.filter(pk=past.pk).exists())

    def test_command_days_overrides_retention(self):
        recent, old_sleep = self.make_entries()
        # Old enough to survive the 30-day default but not a one-week purge.
        middle = models.Note.objects.create(
            child=self.child, note="middle", time=self.now
        )
        self.stamp_deleted(middle, 10)

        call_command("purge_trash", days=7)

        # The one-day-old entry stays; the ten-day-old one only goes with
        # the override.
        self.assertTrue(models.Feeding.all_objects.filter(pk=recent.pk).exists())
        self.assertFalse(models.Note.all_objects.filter(pk=middle.pk).exists())
        self.assertFalse(models.Sleep.all_objects.filter(pk=old_sleep.pk).exists())

    def test_command_rejects_non_positive_days(self):
        # Django's CommandParser raises on the invalid type argument.
        from core.management.commands.purge_trash import Command

        parser = Command().create_parser("manage.py", "purge_trash")
        with self.assertRaises(CommandError):
            parser.parse_args(["--days", "0"])

    def test_empty_trash_is_a_successful_noop(self):
        output = io.StringIO()
        call_command("purge_trash", days=7, stdout=output)
        self.assertIn("Nothing to purge.", output.getvalue())

    def test_purge_announces_real_deletion(self):
        WebhookEndpoint.objects.create(
            name="Home", url="http://home.test/hook", secret="secret"
        )
        feeding = models.Feeding.objects.create(
            child=self.child,
            start=self.now - timezone.timedelta(minutes=30),
            end=self.now,
            type="formula",
            method="bottle",
        )
        self.stamp_deleted(feeding, 31)
        before = WebhookEvent.objects.count()

        call_command("purge_trash")

        self.assertEqual(WebhookEvent.objects.count(), before + 1)
        event = WebhookEvent.objects.latest("id")
        self.assertEqual(event.type, "feeding.deleted")
        self.assertEqual(event.object_id, str(feeding.pk))

    def test_purge_removes_old_note_image_but_keeps_young_ones(self):
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                old_note = models.Note.objects.create(
                    child=self.child,
                    note="old picture",
                    time=self.now,
                    image=image_upload("old.png"),
                )
                young_note = models.Note.objects.create(
                    child=self.child,
                    note="young picture",
                    time=self.now,
                    image=image_upload("young.png"),
                )
                old_name = old_note.image.name
                young_name = young_note.image.name
                self.stamp_deleted(old_note, 31)
                self.stamp_deleted(young_note, 1)

                call_command("purge_trash")

                self.assertFalse(
                    models.Note.all_objects.filter(pk=old_note.pk).exists()
                )
                self.assertFalse(old_note.image.storage.exists(old_name))
                self.assertTrue(
                    models.Note.all_objects.filter(pk=young_note.pk).exists()
                )
                self.assertTrue(young_note.image.storage.exists(young_name))
