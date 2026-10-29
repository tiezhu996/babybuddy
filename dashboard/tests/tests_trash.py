# -*- coding: utf-8 -*-
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core import models
from dashboard.templatetags import cards


class TrashCardsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="cards-user")
        cls.child = models.Child.objects.create(
            first_name="Cards", last_name="Child", birth_date=timezone.localdate()
        )
        cls.context = {"request": type("R", (), {"user": cls.user})()}
        now = timezone.localtime()
        cls.feeding = models.Feeding.objects.create(
            child=cls.child,
            start=now - timezone.timedelta(minutes=30),
            end=now,
            type="formula",
            method="bottle",
        )
        cls.sleep = models.Sleep.objects.create(
            child=cls.child,
            start=now - timezone.timedelta(hours=2),
            end=now - timezone.timedelta(hours=1),
        )
        cls.note = models.Note.objects.create(
            child=cls.child, note="card note", time=now
        )

    def test_cards_hide_trashed_entries(self):
        feeding = cards.card_feeding_last(self.context, self.child)
        self.assertFalse(feeding["empty"])
        sleep = cards.card_sleep_last(self.context, self.child)
        self.assertFalse(sleep["empty"])
        notes = cards.card_notes_recent(self.context, self.child)
        self.assertFalse(notes["empty"])

        self.feeding.delete()
        self.sleep.delete()
        self.note.delete()

        self.assertTrue(cards.card_feeding_last(self.context, self.child)["empty"])
        self.assertTrue(cards.card_sleep_last(self.context, self.child)["empty"])
        self.assertTrue(cards.card_notes_recent(self.context, self.child)["empty"])

    def test_cards_show_restored_entries(self):
        self.feeding.delete()
        self.sleep.delete()
        self.note.delete()

        models.Feeding.all_objects.get(pk=self.feeding.pk).restore()
        models.Sleep.all_objects.get(pk=self.sleep.pk).restore()
        models.Note.all_objects.get(pk=self.note.pk).restore()

        self.assertEqual(
            cards.card_feeding_last(self.context, self.child)["feeding"].pk,
            self.feeding.pk,
        )
        self.assertEqual(
            cards.card_sleep_last(self.context, self.child)["sleep"].pk,
            self.sleep.pk,
        )
        recent = cards.card_notes_recent(self.context, self.child)["notes"]
        self.assertEqual([note.pk for note in recent], [self.note.pk])
