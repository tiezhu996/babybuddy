# -*- coding: utf-8 -*-
import datetime
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from core import models
from summaries.models import VisitSummary
from summaries.snapshot import generate_summary


def local_dt(date, hour=0, minute=0):
    return timezone.make_aware(
        datetime.datetime.combine(date, datetime.time(hour, minute)),
        timezone.get_current_timezone(),
    )


class SummaryViewsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.child = models.Child.objects.create(
            first_name="Sue", birth_date=datetime.date(2025, 1, 1)
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
        cls.admin = get_user_model().objects.create_superuser(
            username="view-admin", password="password", email="v@example.com"
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def _create_summary(self, title="Checkup"):
        return generate_summary(
            self.child, self.start, self.end, self.admin, title=title
        )

    def test_summary_list_add_and_report_entry(self):
        self._create_summary()
        url = reverse("summaries:summary-list", args=[self.child.slug])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Checkup")

        reports_url = reverse("reports:report-list", args=[self.child.slug])
        response = self.client.get(reports_url)
        self.assertContains(response, url)
        self.assertContains(response, "Visit summaries")

    def test_generate_flow_get_and_post(self):
        url = reverse("summaries:summary-add", args=[self.child.slug])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="start_date"')
        self.assertContains(response, 'name="end_date"')

        response = self.client.post(
            url,
            {
                "title": "Six month visit",
                "start_date": "2026-10-01",
                "end_date": "2026-10-07",
            },
        )
        self.assertEqual(response.status_code, 302)
        summary = VisitSummary.objects.get(title="Six month visit")
        self.assertEqual(
            response.url, reverse("summaries:summary-detail", args=[summary.pk])
        )

    def test_detail_and_compare_views(self):
        summary = self._create_summary()
        detail_url = reverse("summaries:summary-detail", args=[summary.pk])
        compare_url = reverse("summaries:summary-compare", args=[summary.pk])
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        # Frozen content is rendered.
        self.assertContains(response, "Checkup")
        self.assertContains(response, "100")

        # Add a current record after the snapshot.
        models.Note.objects.create(
            child=self.child,
            note="New note after the summary",
            time=local_dt(self.start, 10),
        )
        response = self.client.get(compare_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "New note after the summary")
        # The detail view stays frozen.
        response = self.client.get(detail_url)
        self.assertNotContains(response, "New note after the summary")

    @staticmethod
    def _normalize(content):
        # The navigation contains a logout form with a per-request CSRF
        # token; strip it so only the summary body is compared.
        return re.sub(
            rb'name="csrfmiddlewaretoken" value="[^"]*"',
            b'name="csrfmiddlewaretoken"',
            content,
        )

    def test_detail_is_frozen_after_edits_and_deletes(self):
        summary = self._create_summary()
        detail_url = reverse("summaries:summary-detail", args=[summary.pk])
        before = self._normalize(self.client.get(detail_url).content)
        self.assertIn(b"100", before)

        # Edit, add and delete records within the range.
        self.feeding.amount = 999
        self.feeding.save()
        models.Note.objects.create(
            child=self.child, note="later note", time=local_dt(self.start, 12)
        )
        models.Feeding.objects.filter(child=self.child).delete()

        after = self._normalize(self.client.get(detail_url).content)
        self.assertEqual(before, after)
        self.assertNotIn(b"999", after)
        self.assertNotIn(b"later note", after)

    def test_invalid_form_creates_nothing(self):
        url = reverse("summaries:summary-add", args=[self.child.slug])
        response = self.client.post(
            url,
            {"start_date": "2026-10-07", "end_date": "2026-10-01"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(VisitSummary.objects.count(), 0)

        future = (timezone.localdate() + datetime.timedelta(days=1)).isoformat()
        response = self.client.post(
            url,
            {"start_date": future, "end_date": future},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(VisitSummary.objects.count(), 0)

    def test_failed_generation_is_atomic_in_view(self):
        url = reverse("summaries:summary-add", args=[self.child.slug])
        with patch(
            "summaries.views.generate_summary",
            side_effect=RuntimeError("boom"),
        ):
            response = self.client.post(
                url,
                {"start_date": "2026-10-01", "end_date": "2026-10-07"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(VisitSummary.objects.count(), 0)

    def test_delete_flow(self):
        summary = self._create_summary()
        delete_url = reverse("summaries:summary-delete", args=[summary.pk])
        response = self.client.get(delete_url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(delete_url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(VisitSummary.objects.filter(pk=summary.pk).exists())
        # Care records are untouched by deleting the summary.
        self.assertTrue(models.Feeding.objects.filter(pk=self.feeding.pk).exists())

    def test_anonymous_redirected_to_login(self):
        summary = self._create_summary()
        client = Client()
        for url in (
            reverse("summaries:summary-list", args=[self.child.slug]),
            reverse("summaries:summary-add", args=[self.child.slug]),
            reverse("summaries:summary-detail", args=[summary.pk]),
            reverse("summaries:summary-compare", args=[summary.pk]),
            reverse("summaries:summary-delete", args=[summary.pk]),
        ):
            response = client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn("/login", response.url, url)
        # An unknown slug on the add view redirects to login rather than 404,
        # so anonymous users cannot learn whether a child slug exists.
        response = client.get(reverse("summaries:summary-add", args=["no-such-child"]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_permissions_enforced(self):
        summary = self._create_summary()
        plain = get_user_model().objects.create_user(
            username="plain", password="password"
        )
        client = Client()
        client.force_login(plain)

        # No summary permissions at all.
        self.assertEqual(
            client.get(
                reverse("summaries:summary-list", args=[self.child.slug])
            ).status_code,
            403,
        )
        self.assertEqual(
            client.get(
                reverse("summaries:summary-add", args=[self.child.slug])
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                reverse("summaries:summary-add", args=[self.child.slug]),
                {"start_date": "2026-10-01", "end_date": "2026-10-07"},
            ).status_code,
            403,
        )
        self.assertEqual(
            client.get(
                reverse("summaries:summary-detail", args=[summary.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                reverse("summaries:summary-delete", args=[summary.pk])
            ).status_code,
            403,
        )

        # Grant view permissions (and the underlying child view permission):
        # list/detail/compare become visible, add does not.
        content_type = ContentType.objects.get_for_model(VisitSummary)
        for codename in ("view_visitsummary",):
            plain.user_permissions.add(
                Permission.objects.get(content_type=content_type, codename=codename)
            )
        plain.user_permissions.add(
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(models.Child),
                codename="view_child",
            )
        )
        plain = get_user_model().objects.get(pk=plain.pk)
        client.force_login(plain)
        self.assertEqual(
            client.get(
                reverse("summaries:summary-list", args=[self.child.slug])
            ).status_code,
            200,
        )
        self.assertEqual(
            client.get(
                reverse("summaries:summary-detail", args=[summary.pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            client.get(
                reverse("summaries:summary-compare", args=[summary.pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            client.get(
                reverse("summaries:summary-add", args=[self.child.slug])
            ).status_code,
            403,
        )

    def test_print_stylesheet_present(self):
        summary = self._create_summary()
        response = self.client.get(
            reverse("summaries:summary-detail", args=[summary.pk])
        )
        self.assertContains(response, "@media print")
        self.assertContains(response, "nav.navbar")
        # Light paper palette so default print settings give black-on-white.
        self.assertContains(response, "color-scheme: light")
        self.assertContains(response, "#ffffff")
        self.assertContains(response, "window.print()")

    def test_compare_boolean_change_uses_marks(self):
        # A changed boolean field renders ✓/– rather than raw True/False.
        summary = self._create_summary()
        self.feeding.delete()
        change = models.DiaperChange.objects.create(
            child=self.child,
            time=local_dt(self.start, 7),
            wet=True,
            solid=False,
        )
        generate_summary(self.child, self.start, self.end, self.admin)
        change.wet = False
        change.solid = True
        change.save()

        summary = VisitSummary.objects.first()
        response = self.client.get(
            reverse("summaries:summary-compare", args=[summary.pk])
        )
        self.assertEqual(response.status_code, 200)
        # Scope the check to the changed-records table: booleans render as
        # marks there, never as raw Python literals.
        region = response.content.split(b"Changed records")[1].split(b"Statistics")[0]
        self.assertNotIn(b"True", region)
        self.assertNotIn(b"False", region)
        self.assertIn("✓".encode(), region)

    def test_compare_not_permission_filtered_for_summary_viewer(self):
        # A user who may open the summary but lacks core.view_* perms must not
        # see still-existing records falsely reported as deleted (Review I-1).
        summary = self._create_summary()
        added = models.Feeding.objects.create(
            child=self.child,
            start=local_dt(datetime.date(2026, 10, 2), 9),
            end=local_dt(datetime.date(2026, 10, 2), 9, 10),
            type="breast milk",
            method="left breast",
        )

        limited = get_user_model().objects.create_user(
            username="summary-only", password="password"
        )
        summary_ct = ContentType.objects.get_for_model(VisitSummary)
        limited.user_permissions.add(
            Permission.objects.get(
                content_type=summary_ct, codename="view_visitsummary"
            ),
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(models.Child),
                codename="view_child",
            ),
        )
        client = Client()
        client.force_login(get_user_model().objects.get(pk=limited.pk))

        response = client.get(reverse("summaries:summary-compare", args=[summary.pk]))
        self.assertEqual(response.status_code, 200)
        comparison = response.context["comparison"]
        feeding = {section["key"]: section for section in comparison["sections"]}[
            "feeding"
        ]
        # The pre-existing feeding is not "deleted" for this viewer; the new
        # feeding is detected as added and statistics move 1 -> 2.
        self.assertEqual([entry["id"] for entry in feeding["deleted"]], [])
        self.assertEqual([entry["id"] for entry in feeding["added"]], [added.pk])
        count_pair = next(
            pair for pair in feeding["stats"] if pair["metric"] == "count"
        )
        self.assertEqual(count_pair["old"], 1)
        self.assertEqual(count_pair["new"], 2)
        # No edit affordance for a user without core.change_feeding (I-3/F7).
        self.assertNotContains(
            response, reverse("core:feeding-update", args=[added.pk])
        )
