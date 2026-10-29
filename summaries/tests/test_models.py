# -*- coding: utf-8 -*-
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Child
from summaries.models import VisitSummary


class VisitSummaryModelTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.child = Child.objects.create(
            first_name="Test",
            last_name="Child",
            birth_date=datetime.date(2024, 1, 1),
        )
        cls.user = get_user_model().objects.create_user(
            username="summary-generator", password="password"
        )

    def test_create_and_read(self):
        summary = VisitSummary.objects.create(
            child=self.child,
            start_date=datetime.date(2026, 10, 1),
            end_date=datetime.date(2026, 10, 7),
            generated_by=self.user,
            data={"version": 1, "counts": {"feeding": 2, "sleep": 3}},
        )
        self.assertEqual(VisitSummary.objects.count(), 1)
        summary.refresh_from_db()
        self.assertEqual(summary.data["version"], 1)
        self.assertEqual(summary.total_entries, 5)
        self.assertIn("2026-10-01", str(summary))
        self.assertTrue(summary.generated_at)

    def test_title_is_used_as_string(self):
        summary = VisitSummary.objects.create(
            child=self.child,
            title="Six month checkup",
            start_date=datetime.date(2026, 10, 1),
            end_date=datetime.date(2026, 10, 7),
            data={},
        )
        self.assertEqual(str(summary), "Six month checkup")

    def test_cascade_delete_with_child(self):
        VisitSummary.objects.create(
            child=self.child,
            start_date=datetime.date(2026, 10, 1),
            end_date=datetime.date(2026, 10, 7),
            data={},
        )
        self.assertEqual(VisitSummary.objects.count(), 1)
        self.child.delete()
        self.assertEqual(VisitSummary.objects.count(), 0)

    def test_generator_delete_keeps_summary(self):
        summary = VisitSummary.objects.create(
            child=self.child,
            start_date=datetime.date(2026, 10, 1),
            end_date=datetime.date(2026, 10, 7),
            generated_by=self.user,
            data={},
        )
        self.user.delete()
        summary.refresh_from_db()
        self.assertIsNone(summary.generated_by)
        self.assertEqual(VisitSummary.objects.count(), 1)
