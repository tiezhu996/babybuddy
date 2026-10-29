# -*- coding: utf-8 -*-
from django.contrib import admin

from summaries.models import VisitSummary


@admin.register(VisitSummary)
class VisitSummaryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "child",
        "title",
        "start_date",
        "end_date",
        "generated_at",
        "generated_by",
    )
    list_filter = ("child",)
    search_fields = ("title", "child__first_name", "child__last_name")
    readonly_fields = (
        "child",
        "title",
        "start_date",
        "end_date",
        "generated_at",
        "generated_by",
        "data",
    )
    ordering = ("-generated_at",)

    def has_add_permission(self, request):
        # Summaries are built by the in-app generation flow, which snapshots
        # the data inside a transaction. A hand-added row could not contain a
        # coherent snapshot.
        return False
