# -*- coding: utf-8 -*-
from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SummariesConfig(AppConfig):
    name = "summaries"
    verbose_name = _("Visit summaries")
