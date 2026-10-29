# -*- coding: utf-8 -*-
import datetime as dt

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.test import TestCase

from core import models
from reports.graphs import feeding_duration, sleep_totals


def daily_counts(queryset, field):
    return list(
        queryset.annotate(date=TruncDate(field))
        .values("date")
        .annotate(count=Count("id"))
        .values_list("count", flat=True)
    )


class TrashReportsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.child = models.Child.objects.create(
            first_name="Reports", last_name="Child", birth_date=dt.date(2024, 1, 1)
        )
        cls.day_one = dt.datetime(2024, 2, 1, 8, 0, tzinfo=dt.timezone.utc)
        cls.day_two = dt.datetime(2024, 2, 2, 8, 0, tzinfo=dt.timezone.utc)
        cls.feeding = models.Feeding.objects.create(
            child=cls.child,
            start=cls.day_one,
            end=cls.day_one + dt.timedelta(minutes=10),
            type="formula",
            method="bottle",
        )
        models.Feeding.objects.create(
            child=cls.child,
            start=cls.day_two,
            end=cls.day_two + dt.timedelta(minutes=20),
            type="formula",
            method="bottle",
        )
        cls.sleep = models.Sleep.objects.create(
            child=cls.child,
            start=cls.day_one,
            end=cls.day_one + dt.timedelta(hours=1),
        )
        models.Sleep.objects.create(
            child=cls.child,
            start=cls.day_two,
            end=cls.day_two + dt.timedelta(hours=2),
        )

    def test_report_querysets_exclude_trashed_and_readd_on_restore(self):
        feeding_qs = models.Feeding.objects.filter(child=self.child)
        sleep_qs = models.Sleep.objects.filter(child=self.child)
        self.assertEqual(daily_counts(feeding_qs, "start"), [1, 1])
        self.assertEqual(daily_counts(sleep_qs, "start"), [1, 1])

        self.feeding.delete()
        self.sleep.delete()

        self.assertEqual(daily_counts(feeding_qs, "start"), [1])
        self.assertEqual(daily_counts(sleep_qs, "start"), [1])
        # The graphs render on the same filtered querysets without error.
        feeding_duration(feeding_qs)
        sleep_totals(sleep_qs)

        models.Feeding.all_objects.get(pk=self.feeding.pk).restore()
        models.Sleep.all_objects.get(pk=self.sleep.pk).restore()
        self.assertEqual(daily_counts(feeding_qs, "start"), [1, 1])
        self.assertEqual(daily_counts(sleep_qs, "start"), [1, 1])
