# -*- coding: utf-8 -*-
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core import models
from summaries.diff import diff_summary
from summaries.snapshot import generate_summary


def local_dt(date, hour=0, minute=0):
    return timezone.make_aware(
        datetime.datetime.combine(date, datetime.time(hour, minute)),
        timezone.get_current_timezone(),
    )


class DiffTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.child = models.Child.objects.create(
            first_name="Sue", birth_date=datetime.date(2025, 1, 1)
        )
        cls.user = get_user_model().objects.create_superuser(
            username="diff-admin", password="password", email="d@example.com"
        )
        cls.start = datetime.date(2026, 10, 1)
        cls.end = datetime.date(2026, 10, 7)

        cls.feeding = models.Feeding.objects.create(
            child=cls.child,
            start=local_dt(cls.start, 8),
            end=local_dt(cls.start, 8, 20),
            type="formula",
            method="bottle",
            amount=100,
        )
        cls.change = models.DiaperChange.objects.create(
            child=cls.child, time=local_dt(cls.start, 7), wet=True, solid=False
        )
        cls.summary = generate_summary(
            cls.child, cls.start, cls.end, cls.user, title="Visit"
        )

    def test_no_changes_right_after_generation(self):
        result = diff_summary(self.summary)
        self.assertFalse(result["has_changes"])
        for section in result["sections"]:
            self.assertEqual(section["added"], [])
            self.assertEqual(section["deleted"], [])
            self.assertEqual(section["changed"], [])
            self.assertEqual(section["stats_changed_count"], 0)
            for pair in section["stats"]:
                self.assertFalse(pair["changed"])
                self.assertEqual(pair["old"], pair["new"])

    def test_added_changed_deleted_and_stats(self):
        # Added: a new feeding after the snapshot.
        added = models.Feeding.objects.create(
            child=self.child,
            start=local_dt(datetime.date(2026, 10, 2), 9),
            end=local_dt(datetime.date(2026, 10, 2), 9, 10),
            type="breast milk",
            method="left breast",
        )
        # Changed: the snapshotted feeding's amount and a tag.
        self.feeding.amount = 120
        self.feeding.save()
        self.feeding.tags.set(["sick"])
        # Deleted: the diaper change. Delete nulls the in-memory instance's
        # pk, so capture it for the assertion beforehand.
        change_pk = self.change.pk
        self.change.delete()

        result = diff_summary(self.summary)
        self.assertTrue(result["has_changes"])
        sections = {section["key"]: section for section in result["sections"]}

        feeding = sections["feeding"]
        self.assertEqual([entry["id"] for entry in feeding["added"]], [added.pk])
        self.assertEqual(len(feeding["changed"]), 1)
        changed = feeding["changed"][0]
        self.assertEqual(changed["id"], self.feeding.pk)
        field_names = {change["field"] for change in changed["changes"]}
        self.assertIn("amount", field_names)
        self.assertIn("tags", field_names)
        amount_change = next(
            change for change in changed["changes"] if change["field"] == "amount"
        )
        self.assertEqual(amount_change["old"], 100)
        self.assertEqual(amount_change["new"], 120)
        self.assertEqual(amount_change["label"], "Amount")
        tags_change = next(
            change for change in changed["changes"] if change["field"] == "tags"
        )
        self.assertEqual(tags_change["old"], [])
        self.assertEqual(tags_change["new"], ["sick"])

        changes = sections["diaperchange"]
        self.assertEqual([entry["id"] for entry in changes["deleted"]], [change_pk])

        # Statistics moved with the data.
        feeding_count = next(
            pair for pair in feeding["stats"] if pair["metric"] == "count"
        )
        self.assertTrue(feeding_count["changed"])
        self.assertEqual(feeding_count["old"], 1)
        self.assertEqual(feeding_count["new"], 2)
        change_count = next(
            pair for pair in changes["stats"] if pair["metric"] == "count"
        )
        self.assertEqual(change_count["old"], 1)
        self.assertEqual(change_count["new"], 0)

        # Unrelated sections remain unchanged.
        self.assertFalse(sections["sleep"]["has_changes"])

    def test_comparison_does_not_write(self):
        before_rows = {
            model: model.objects.count()
            for model in (models.Feeding, models.DiaperChange)
        }
        from summaries.models import VisitSummary

        summaries_before = VisitSummary.objects.count()
        result = diff_summary(self.summary)
        self.assertFalse(result["has_changes"])
        self.assertEqual(VisitSummary.objects.count(), summaries_before)
        for model, count in before_rows.items():
            self.assertEqual(model.objects.count(), count)
