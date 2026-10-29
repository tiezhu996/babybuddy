# -*- coding: utf-8 -*-
from django.apps import apps
from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.test import TestCase
from django.utils import timezone

from core import models

from webhooks import signals
from webhooks.models import WebhookEndpoint, WebhookEvent


class WebhookSignalTestCase(TestCase):
    def setUp(self):
        call_command("migrate", verbosity=0)
        self.endpoint = WebhookEndpoint.objects.create(
            name="Home", url="http://home.test/hook", secret="secret"
        )
        self.child = models.Child.objects.create(
            first_name="First", last_name="Last", birth_date=timezone.localdate()
        )
        self.event_count = WebhookEvent.objects.count()

    def test_create_is_announced(self):
        models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime(),
            end=timezone.localtime(),
            type="formula",
        )
        event = WebhookEvent.objects.latest("id")
        self.assertEqual(event.type, "feeding.created")
        self.assertEqual(event.object_id, str(models.Feeding.objects.latest("id").pk))
        self.assertEqual(event.endpoint, self.endpoint)
        self.assertEqual(event.attempts, 0)
        self.assertEqual(WebhookEvent.objects.count(), self.event_count + 1)

    def test_update_is_announced(self):
        feeding = models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime(),
            end=timezone.localtime(),
            type="formula",
        )
        feeding.type = "breast milk"
        feeding.save()
        self.assertEqual(WebhookEvent.objects.latest("id").type, "feeding.updated")

    def test_a_soft_delete_is_not_announced(self):
        # A move to the trash can still be undone, so nobody is told the
        # record is gone. This holds for every trashable kind.
        now = timezone.localtime()
        instances = (
            models.Feeding.objects.create(
                child=self.child,
                start=now,
                end=now,
                type="formula",
                method="bottle",
            ),
            models.Sleep.objects.create(child=self.child, start=now, end=now),
            models.Note.objects.create(child=self.child, note="hello", time=now),
        )
        for instance in instances:
            before = WebhookEvent.objects.count()
            instance.delete()
            self.assertEqual(WebhookEvent.objects.count(), before)
            # A bulk soft delete is quiet for the same reason.
            instance.__class__.all_objects.filter(pk=instance.pk).delete()
            self.assertEqual(WebhookEvent.objects.count(), before)

    def test_restore_is_announced(self):
        feeding = models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime(),
            end=timezone.localtime(),
            type="formula",
        )
        primary_key = str(feeding.pk)
        feeding.delete()
        trashed = models.Feeding.all_objects.get(pk=feeding.pk)
        trashed.restore()
        event = WebhookEvent.objects.latest("id")
        self.assertEqual(event.type, "feeding.restored")
        self.assertEqual(event.object_id, primary_key)

    def test_restoring_an_active_row_announces_nothing(self):
        # Restore only changes rows actually in the trash, so a repeated
        # restore neither changes data nor queues a second event.
        feeding = models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime(),
            end=timezone.localtime(),
            type="formula",
        )
        before = WebhookEvent.objects.count()
        feeding.restore()
        self.assertEqual(WebhookEvent.objects.count(), before)

    def test_hard_delete_is_announced(self):
        feeding = models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime(),
            end=timezone.localtime(),
            type="formula",
        )
        primary_key = str(feeding.pk)
        feeding.delete()
        feeding.hard_delete()
        event = WebhookEvent.objects.latest("id")
        self.assertEqual(event.type, "feeding.deleted")
        self.assertEqual(event.object_id, primary_key)

    def test_restore_skips_inactive_endpoints(self):
        self.endpoint.active = False
        self.endpoint.save()
        feeding = models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime(),
            end=timezone.localtime(),
            type="formula",
        )
        feeding.delete()
        before = WebhookEvent.objects.count()
        models.Feeding.all_objects.get(pk=feeding.pk).restore()
        self.assertEqual(WebhookEvent.objects.count(), before)

    def test_a_recording_failure_does_not_break_a_restore(self):
        # As with a save, a webhook that cannot be queued must not undo the
        # restore itself.
        feeding = models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime(),
            end=timezone.localtime(),
            type="formula",
        )
        feeding.delete()
        original = WebhookEndpoint.objects.filter

        def broken(*args, **kwargs):
            raise RuntimeError("no")

        WebhookEndpoint.objects.filter = broken
        self.addCleanup(setattr, WebhookEndpoint.objects, "filter", original)
        with self.assertLogs("webhooks.signals", level="ERROR"):
            models.Feeding.all_objects.get(pk=feeding.pk).restore()
        self.assertTrue(models.Feeding.objects.filter(pk=feeding.pk).exists())

    def test_every_record_model_is_watched(self):
        # A new kind of record is a new kind of change to announce. This fails
        # until it is added on purpose, rather than quietly announcing nothing
        # about it forever.
        from django.db.models import Model

        watched = set(signals.WATCHED)
        excluded = {
            models.Tag,
            models.Tagged,
            models.HeightPercentile,
            models.WeightPercentile,
        }
        for candidate in apps.get_app_config("core").get_models():
            if not issubclass(candidate, Model):
                continue
            if candidate in excluded:
                continue
            self.assertIn(
                candidate,
                watched,
                "{} is not announced by webhooks.signals.WATCHED".format(
                    candidate.__name__
                ),
            )
            # And the name in the event has to come from somewhere, so a new
            # record kind cannot arrive with a type nothing can match.
            self.assertTrue(
                hasattr(candidate, "model_name"),
                "{} has no model_name".format(candidate.__name__),
            )
        self.assertEqual(
            watched,
            set(apps.get_app_config("core").get_models()) & watched,
        )

    def test_the_type_uses_the_name_the_rest_of_baby_buddy_uses(self):
        # Not `Meta.model_name`, which runs words together. The record kinds
        # have a name of their own and the rest of the code speaks it.
        models.HeadCircumference.objects.create(
            child=self.child, date=timezone.localdate(), head_circumference=34.0
        )
        self.assertEqual(
            WebhookEvent.objects.latest("id").type, "head_circumference.created"
        )

    def test_a_measurement_announces_the_measurement_only(self):
        # The percentiles are computed rather than written, so a change to a
        # measurement must not announce twice: once for the measurement and
        # once for the curve it moved.
        models.Weight.objects.create(
            child=self.child, date=timezone.localdate(), weight=3.5
        )
        types = set(WebhookEvent.objects.values_list("type", flat=True))
        self.assertIn("weight.created", types)
        self.assertNotIn("weightpercentile.created", types)

    def test_a_bulk_update_is_not_announced(self):
        # The one hole worth naming: `QuerySet.update()` writes rows without
        # calling `save()`, so no signal fires and nothing is announced. It is
        # used in migrations and tests only today. If that ever changes, this
        # test is the reminder that the announcement went with it.
        feeding = models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime(),
            end=timezone.localtime(),
            type="formula",
        )
        before = WebhookEvent.objects.count()
        models.Feeding.objects.filter(pk=feeding.pk).update(type="breast milk")
        self.assertEqual(WebhookEvent.objects.count(), before)

        # A save of the same record still is.
        feeding.refresh_from_db()
        feeding.save()
        self.assertEqual(WebhookEvent.objects.count(), before + 1)

    def test_saving_an_event_is_not_an_event(self):
        WebhookEndpoint.objects.create(
            name="Second", url="http://second.test/hook", secret="secret"
        )
        before = WebhookEvent.objects.count()
        WebhookEvent.objects.create(
            endpoint=self.endpoint, type="feeding.created", object_id="1"
        )
        # Saving an event is not itself an event, or every delivery would queue
        # the next one.
        self.assertEqual(WebhookEvent.objects.count(), before + 1)

    def test_inactive_endpoints_are_skipped(self):
        self.endpoint.active = False
        self.endpoint.save()
        models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime(),
            end=timezone.localtime(),
            type="formula",
        )
        self.assertEqual(WebhookEvent.objects.count(), self.event_count)

    def test_every_active_endpoint_gets_its_own_event(self):
        WebhookEndpoint.objects.create(
            name="Second", url="http://second.test/hook", secret="secret"
        )
        models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime(),
            end=timezone.localtime(),
            type="formula",
        )
        self.assertEqual(WebhookEvent.objects.count(), self.event_count + 2)
        self.assertEqual(
            set(WebhookEvent.objects.values_list("endpoint", flat=True)),
            set(WebhookEndpoint.objects.values_list("id", flat=True)),
        )

    def test_no_endpoints_does_not_break_a_save(self):
        WebhookEvent.objects.all().delete()
        WebhookEndpoint.objects.all().delete()
        feeding = models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime(),
            end=timezone.localtime(),
            type="formula",
        )
        self.assertIsNotNone(feeding.pk)

    def test_a_recording_failure_does_not_break_a_save(self):
        # A webhook is a notification about the entry. If it cannot be queued,
        # the parent must still get the entry.
        original = WebhookEndpoint.objects.filter

        def broken(*args, **kwargs):
            raise RuntimeError("no")

        WebhookEndpoint.objects.filter = broken
        self.addCleanup(setattr, WebhookEndpoint.objects, "filter", original)
        with self.assertLogs("webhooks.signals", level="ERROR"):
            feeding = models.Feeding.objects.create(
                child=self.child,
                start=timezone.localtime(),
                end=timezone.localtime(),
                type="formula",
            )
        self.assertIsNotNone(feeding.pk)
        self.assertEqual(models.Feeding.objects.count(), 1)

    def test_a_failed_lookup_leaves_the_callers_transaction_usable(self):
        # On PostgreSQL a failed statement aborts the transaction it ran in.
        # Catching the error is not enough: without a savepoint of its own the
        # caller's transaction is left broken and its save rolls back.
        original = WebhookEndpoint.objects.filter

        def broken(*args, **kwargs):
            transaction.set_rollback(True)
            raise DatabaseError("no")

        WebhookEndpoint.objects.filter = broken
        self.addCleanup(setattr, WebhookEndpoint.objects, "filter", original)
        with self.assertLogs("webhooks.signals", level="ERROR"):
            with transaction.atomic():
                models.Feeding.objects.create(
                    child=self.child,
                    start=timezone.localtime(),
                    end=timezone.localtime(),
                    type="formula",
                )
                self.assertEqual(models.Feeding.objects.count(), 1)
