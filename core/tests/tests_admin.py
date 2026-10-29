# -*- coding: utf-8 -*-
from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from core import models


class TrashAdminTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="db-admin", password="password", email="db-admin@example.com"
        )
        cls.child = models.Child.objects.create(
            first_name="Admin", last_name="Child", birth_date=timezone.localdate()
        )
        cls.factory = RequestFactory()

    def _request(self):
        request = self.factory.get("/admin/core/feeding/")
        request.user = self.user
        return request

    def test_changelist_hides_trashed_entries(self):
        feeding = models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime() - timezone.timedelta(minutes=30),
            end=timezone.localtime(),
            type="formula",
            method="bottle",
        )
        model_admin = django_admin.site._registry[models.Feeding]

        self.assertIn(feeding, model_admin.get_queryset(self._request()))

        feeding.delete()

        self.assertNotIn(feeding, model_admin.get_queryset(self._request()))
        self.assertTrue(models.Feeding.all_objects.filter(pk=feeding.pk).exists())

    def test_admin_delete_model_moves_to_trash(self):
        feeding = models.Feeding.objects.create(
            child=self.child,
            start=timezone.localtime() - timezone.timedelta(minutes=30),
            end=timezone.localtime(),
            type="formula",
            method="bottle",
        )
        model_admin = django_admin.site._registry[models.Feeding]

        model_admin.delete_model(self._request(), feeding)

        self.assertFalse(models.Feeding.objects.filter(pk=feeding.pk).exists())
        self.assertTrue(
            models.Feeding.all_objects.filter(
                pk=feeding.pk, deleted_at__isnull=False
            ).exists()
        )
