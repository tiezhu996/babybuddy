# -*- coding: utf-8 -*-
import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from core import models
from summaries.models import VisitSummary
from summaries import snapshot
from summaries.snapshot import REGISTRY, build_payload, generate_summary


def local_dt(date, hour=0, minute=0, tz=None):
    tz = tz or timezone.get_current_timezone()
    return timezone.make_aware(
        datetime.datetime.combine(date, datetime.time(hour, minute)), tz
    )


class SnapshotTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.child = models.Child.objects.create(
            first_name="Sue", birth_date=datetime.date(2025, 1, 1)
        )
        cls.user = get_user_model().objects.create_superuser(
            username="snapshot-admin",
            password="password",
            email="a@example.com",
        )
        cls.start = datetime.date(2026, 10, 1)
        cls.end = datetime.date(2026, 10, 7)

    def _create_records(self):
        d1, d2, d3 = self.start, datetime.date(2026, 10, 2), datetime.date(2026, 10, 3)
        d7 = self.end
        # Feedings: 1200s + 1800s + 600s = 3600s; amounts 100 + 50.
        models.Feeding.objects.create(
            child=self.child,
            start=local_dt(d1, 8),
            end=local_dt(d1, 8, 20),
            type="formula",
            method="bottle",
            amount=100,
        )
        models.Feeding.objects.create(
            child=self.child,
            start=local_dt(d1, 11),
            end=local_dt(d1, 11, 30),
            type="breast milk",
            method="left breast",
            amount=50,
        )
        models.Feeding.objects.create(
            child=self.child,
            start=local_dt(d3, 9),
            end=local_dt(d3, 9, 10),
            type="breast milk",
            method="right breast",
        )
        # Sleep: one 1h nap and one 10h night sleep.
        models.Sleep.objects.create(
            child=self.child,
            start=local_dt(d1, 12),
            end=local_dt(d1, 13),
            nap=True,
        )
        models.Sleep.objects.create(
            child=self.child,
            start=local_dt(d2, 20),
            end=local_dt(d3, 6),
            nap=False,
        )
        # Diaper changes: wet only, wet+solid, empty.
        models.DiaperChange.objects.create(
            child=self.child, time=local_dt(d1, 7), wet=True, solid=False, amount=30
        )
        models.DiaperChange.objects.create(
            child=self.child, time=local_dt(d2, 7), wet=True, solid=True
        )
        models.DiaperChange.objects.create(
            child=self.child, time=local_dt(d3, 7), wet=False, solid=False
        )
        # Tummy time: 900s + 300s.
        models.TummyTime.objects.create(
            child=self.child, start=local_dt(d1, 9), end=local_dt(d1, 9, 15)
        )
        models.TummyTime.objects.create(
            child=self.child, start=local_dt(d2, 10), end=local_dt(d2, 10, 5)
        )
        # Pumping: 1200s/80 + 600s/40.
        models.Pumping.objects.create(
            child=self.child,
            start=local_dt(d1, 6),
            end=local_dt(d1, 6, 20),
            amount=80,
        )
        models.Pumping.objects.create(
            child=self.child,
            start=local_dt(d2, 6),
            end=local_dt(d2, 6, 10),
            amount=40,
        )
        # Temperatures.
        models.Temperature.objects.create(
            child=self.child, time=local_dt(d1, 7, 30), temperature=36.5
        )
        models.Temperature.objects.create(
            child=self.child, time=local_dt(d2, 7, 30), temperature=37.1
        )
        models.Temperature.objects.create(
            child=self.child, time=local_dt(d3, 7, 30), temperature=36.8
        )
        # Notes.
        models.Note.objects.create(child=self.child, note="first", time=local_dt(d1))
        models.Note.objects.create(child=self.child, note="second", time=local_dt(d2))
        # Medication.
        models.Medication.objects.create(
            child=self.child, name="Amoxicillin", time=local_dt(d1, 8)
        )
        models.Medication.objects.create(
            child=self.child, name="Amoxicillin", time=local_dt(d2, 8)
        )
        models.Medication.objects.create(
            child=self.child, name="Tylenol", time=local_dt(d3, 8)
        )
        # Measurements.
        models.Weight.objects.create(child=self.child, date=d1, weight=5.0)
        models.Weight.objects.create(child=self.child, date=d7, weight=5.5)
        models.Height.objects.create(child=self.child, date=d1, height=60.0)
        models.Height.objects.create(child=self.child, date=d7, height=61.5)

    def test_payload_structure_and_counts(self):
        self._create_records()
        payload = build_payload(self.child, self.start, self.end, self.user)
        for key in (
            "version",
            "generated_at",
            "timezone",
            "child",
            "range",
            "included_models",
            "counts",
            "stats",
            "entries",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["included_models"], [spec.key for spec in REGISTRY])
        self.assertEqual(payload["child"]["name"], "Sue")
        self.assertEqual(payload["range"], {"start": "2026-10-01", "end": "2026-10-07"})

        # Counts match independently filtered live querysets.
        self.assertEqual(payload["counts"]["feeding"], 3)
        self.assertEqual(
            payload["counts"]["feeding"],
            models.Feeding.objects.filter(
                child=self.child,
                start__range=snapshot.range_bounds(self.start, self.end),
            ).count(),
        )
        self.assertEqual(payload["counts"]["diaperchange"], 3)
        self.assertEqual(payload["counts"]["temperature"], 3)
        self.assertEqual(payload["counts"]["note"], 2)
        self.assertEqual(payload["counts"]["medication"], 3)
        self.assertEqual(payload["counts"]["weight"], 2)
        # Entries carry full fields, displays and tags.
        feeding = payload["entries"]["feeding"][0]
        self.assertIn("amount", feeding["fields"])
        self.assertIn("type", feeding["displays"])
        self.assertEqual(feeding["fields"]["duration"], feeding["fields"]["duration"])
        self.assertIsInstance(feeding["tags"], list)

    def test_statistics(self):
        self._create_records()
        payload = build_payload(self.child, self.start, self.end, self.user)
        stats = payload["stats"]

        feeding = stats["feeding"]
        self.assertEqual(feeding["count"], 3)
        self.assertEqual(feeding["duration_total_seconds"], 3600)
        self.assertEqual(feeding["duration_avg_seconds"], 1200)
        self.assertEqual(feeding["amount_sum"], 150)
        self.assertEqual(feeding["amount_count"], 2)
        # 9600s (08:20->11:00) and 1d21h30m (10-01 11:30 -> 10-03 09:00).
        self.assertEqual(feeding["avg_interval_seconds"], (9600 + 163800) / 2)
        self.assertEqual(feeding["count_by_type"]["breast milk"], 2)
        self.assertEqual(feeding["count_by_method"]["bottle"], 1)

        sleep = stats["sleep"]
        self.assertEqual(sleep["count"], 2)
        self.assertEqual(sleep["duration_total_seconds"], 39600)
        self.assertEqual(sleep["duration_avg_seconds"], 19800)
        self.assertEqual(sleep["nap_count"], 1)
        self.assertEqual(sleep["nap_total_seconds"], 3600)

        changes = stats["diaperchange"]
        self.assertEqual(changes["count"], 3)
        self.assertEqual(changes["wet_count"], 2)
        self.assertEqual(changes["solid_count"], 1)
        self.assertEqual(changes["empty_count"], 1)
        self.assertEqual(changes["amount_sum"], 30)
        self.assertEqual(changes["amount_count"], 1)
        self.assertEqual(changes["avg_interval_seconds"], 86400)

        tummy = stats["tummytime"]
        self.assertEqual(tummy["count"], 2)
        self.assertEqual(tummy["duration_total_seconds"], 1200)
        self.assertEqual(tummy["duration_avg_seconds"], 600)

        pumping = stats["pumping"]
        self.assertEqual(pumping["count"], 2)
        self.assertEqual(pumping["duration_total_seconds"], 1800)
        self.assertEqual(pumping["amount_sum"], 120)
        self.assertEqual(pumping["amount_avg"], 60)

        temperature = stats["temperature"]
        self.assertEqual(temperature["count"], 3)
        self.assertEqual(temperature["min"], 36.5)
        self.assertEqual(temperature["max"], 37.1)
        self.assertAlmostEqual(temperature["avg"], 36.8, places=9)
        self.assertEqual(temperature["latest"], 36.8)

        self.assertEqual(stats["note"]["count"], 2)
        self.assertEqual(stats["medication"]["count"], 3)
        self.assertEqual(stats["medication"]["count_by_name"]["Amoxicillin"], 2)

        weight = stats["weight"]
        self.assertEqual(weight["count"], 2)
        self.assertEqual(weight["first"], 5.0)
        self.assertEqual(weight["last"], 5.5)
        self.assertEqual(weight["change"], 0.5)

        height = stats["height"]
        self.assertEqual(height["change"], 1.5)

    def test_empty_range(self):
        payload = build_payload(self.child, self.start, self.end, self.user)
        self.assertEqual(payload["counts"]["feeding"], 0)
        self.assertEqual(payload["entries"]["feeding"], [])
        feeding_stats = payload["stats"]["feeding"]
        self.assertEqual(feeding_stats["count"], 0)
        self.assertIsNone(feeding_stats["duration_avg_seconds"])
        weight_stats = payload["stats"]["weight"]
        self.assertIsNone(weight_stats["first"])
        self.assertIsNone(weight_stats["change"])

    def test_range_boundaries(self):
        # Interval record starting inside the range but ending past its end is
        # fully included; one starting before the range is excluded.
        models.Sleep.objects.create(
            child=self.child,
            start=local_dt(self.end, 22),
            end=local_dt(self.end + datetime.timedelta(days=1), 8),
            nap=False,
        )
        models.Sleep.objects.create(
            child=self.child,
            start=local_dt(self.start - datetime.timedelta(days=1), 22),
            end=local_dt(self.start, 6),
            nap=False,
        )
        # Instant records on each boundary day are included; outside excluded.
        models.DiaperChange.objects.create(
            child=self.child, time=local_dt(self.start, 0, 1), wet=True, solid=False
        )
        models.DiaperChange.objects.create(
            child=self.child,
            time=local_dt(self.end, 23, 59),
            wet=True,
            solid=False,
        )
        models.DiaperChange.objects.create(
            child=self.child,
            time=local_dt(self.end + datetime.timedelta(days=1)),
            wet=True,
            solid=False,
        )
        # Date-field model uses calendar dates only.
        models.Weight.objects.create(child=self.child, date=self.start, weight=4.0)
        models.Weight.objects.create(child=self.child, date=self.end, weight=4.1)
        models.Weight.objects.create(
            child=self.child,
            date=self.end + datetime.timedelta(days=1),
            weight=4.2,
        )
        payload = build_payload(self.child, self.start, self.end, self.user)
        self.assertEqual(payload["counts"]["sleep"], 1)
        self.assertEqual(payload["stats"]["sleep"]["duration_total_seconds"], 10 * 3600)
        self.assertEqual(payload["counts"]["diaperchange"], 2)
        self.assertEqual(payload["counts"]["weight"], 2)

    def test_timezone_is_recorded_and_drives_bounds(self):
        late_utc = timezone.make_aware(
            datetime.datetime(2026, 10, 7, 16, 30), datetime.timezone.utc
        )
        with timezone.override("Asia/Shanghai"):
            # 16:30 UTC on 10-07 is 00:30 local on 10-08: outside the range.
            models.DiaperChange.objects.create(
                child=self.child, time=late_utc, wet=True, solid=False
            )
            payload = build_payload(self.child, self.start, self.end, self.user)
            self.assertEqual(payload["timezone"], "Asia/Shanghai")
            self.assertEqual(payload["counts"]["diaperchange"], 0)
        with timezone.override("UTC"):
            payload = build_payload(self.child, self.start, self.end, self.user)
            self.assertEqual(payload["counts"]["diaperchange"], 1)

    def test_permissions_filter_sections(self):
        self._create_records()
        plain_user = get_user_model().objects.create_user(
            username="limited", password="password"
        )
        payload = build_payload(self.child, self.start, self.end, plain_user)
        self.assertNotIn("feeding", payload["included_models"])
        self.assertNotIn("feeding", payload["entries"])
        self.assertNotIn("feeding", payload["stats"])

        content_type = ContentType.objects.get_for_model(models.Feeding)
        perm = Permission.objects.get(
            content_type=content_type, codename="view_feeding"
        )
        plain_user.user_permissions.add(perm)
        plain_user = get_user_model().objects.get(pk=plain_user.pk)
        payload = build_payload(self.child, self.start, self.end, plain_user)
        self.assertIn("feeding", payload["included_models"])
        self.assertEqual(payload["counts"]["feeding"], 3)

    def test_generate_summary_persists_payload(self):
        self._create_records()
        summary = generate_summary(
            self.child, self.start, self.end, self.user, title="Checkup"
        )
        self.assertEqual(VisitSummary.objects.count(), 1)
        self.assertEqual(summary.title, "Checkup")
        self.assertEqual(summary.data["version"], 1)
        self.assertEqual(summary.data["counts"]["feeding"], 3)
        self.assertEqual(summary.generated_by, self.user)

    def test_generate_summary_is_atomic_on_failure(self):
        self._create_records()
        with patch(
            "summaries.snapshot.compute_stats", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                generate_summary(self.child, self.start, self.end, self.user)
        self.assertEqual(VisitSummary.objects.count(), 0)
