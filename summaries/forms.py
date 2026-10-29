# -*- coding: utf-8 -*-
from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from babybuddy.widgets import DateInput


class VisitSummaryForm(forms.Form):
    title = forms.CharField(
        label=_("Title"),
        required=False,
        max_length=255,
        help_text=_("Optional label, e.g. the reason for the visit."),
    )
    start_date = forms.DateField(label=_("Start date"), widget=DateInput())
    end_date = forms.DateField(label=_("End date"), widget=DateInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            today = timezone.localdate()
            self.fields["start_date"].initial = today - timezone.timedelta(days=6)
            self.fields["end_date"].initial = today

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date:
            if start_date > end_date:
                self.add_error(
                    "start_date",
                    _("The start date can not be later than the end date."),
                )
            if end_date > timezone.localdate():
                self.add_error("end_date", _("The end date can not be in the future."))
        return cleaned_data
