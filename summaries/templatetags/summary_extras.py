# -*- coding: utf-8 -*-
import datetime

from django import template
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.translation import gettext as _

from core.utils import duration_string
from summaries.diff import METRIC_LABELS
from summaries.snapshot import SPECS_BY_KEY

register = template.Library()


# Columns shown in the per-type record tables, as (field name, value kind).
SECTION_COLUMNS = {
    "feeding": [
        ("start", "datetime"),
        ("end", "datetime"),
        ("duration", "duration"),
        ("type", "choice"),
        ("method", "choice"),
        ("amount", "value"),
        ("notes", "text"),
    ],
    "sleep": [
        ("start", "datetime"),
        ("end", "datetime"),
        ("duration", "duration"),
        ("nap", "bool"),
        ("notes", "text"),
    ],
    "diaperchange": [
        ("time", "datetime"),
        ("wet", "bool"),
        ("solid", "bool"),
        ("color", "choice"),
        ("amount", "value"),
        ("notes", "text"),
    ],
    "tummytime": [
        ("start", "datetime"),
        ("end", "datetime"),
        ("duration", "duration"),
        ("milestone", "text"),
        ("notes", "text"),
    ],
    "pumping": [
        ("start", "datetime"),
        ("end", "datetime"),
        ("duration", "duration"),
        ("amount", "value"),
        ("notes", "text"),
    ],
    "medication": [
        ("time", "datetime"),
        ("name", "value"),
        ("dosage", "value"),
        ("dosage_unit", "choice"),
        ("next_dose_interval", "duration"),
        ("notes", "text"),
    ],
    "temperature": [
        ("time", "datetime"),
        ("temperature", "value"),
        ("notes", "text"),
    ],
    "note": [("time", "datetime"), ("note", "text")],
    "weight": [("date", "date"), ("weight", "value"), ("notes", "text")],
    "height": [("date", "date"), ("height", "value"), ("notes", "text")],
    "head_circumference": [
        ("date", "date"),
        ("head_circumference", "value"),
        ("notes", "text"),
    ],
    "bmi": [("date", "date"), ("bmi", "value"), ("notes", "text")],
}

# Statistics shown per type, as (metric key, value kind).
SECTION_METRICS = {
    "diaperchange": [
        ("count", "value"),
        ("wet_count", "value"),
        ("solid_count", "value"),
        ("empty_count", "value"),
        ("avg_interval_seconds", "duration"),
        ("amount_sum", "value"),
        ("amount_count", "value"),
    ],
    "feeding": [
        ("count", "value"),
        ("duration_total_seconds", "duration"),
        ("duration_avg_seconds", "duration"),
        ("avg_interval_seconds", "duration"),
        ("amount_sum", "value"),
        ("amount_count", "value"),
        ("count_by_type", "dict"),
        ("count_by_method", "dict"),
    ],
    "sleep": [
        ("count", "value"),
        ("duration_total_seconds", "duration"),
        ("duration_avg_seconds", "duration"),
        ("nap_count", "value"),
        ("nap_total_seconds", "duration"),
        ("avg_awake_interval_seconds", "duration"),
    ],
    "tummytime": [
        ("count", "value"),
        ("duration_total_seconds", "duration"),
        ("duration_avg_seconds", "duration"),
    ],
    "pumping": [
        ("count", "value"),
        ("duration_total_seconds", "duration"),
        ("amount_sum", "value"),
        ("amount_count", "value"),
        ("amount_avg", "value"),
    ],
    "medication": [("count", "value"), ("count_by_name", "dict")],
    "temperature": [
        ("count", "value"),
        ("min", "value"),
        ("max", "value"),
        ("avg", "value"),
        ("latest", "value"),
    ],
    "note": [("count", "value")],
    "weight": [
        ("count", "value"),
        ("first", "value"),
        ("last", "value"),
        ("change", "value"),
    ],
    "height": [
        ("count", "value"),
        ("first", "value"),
        ("last", "value"),
        ("change", "value"),
    ],
    "head_circumference": [
        ("count", "value"),
        ("first", "value"),
        ("last", "value"),
        ("change", "value"),
    ],
    "bmi": [
        ("count", "value"),
        ("first", "value"),
        ("last", "value"),
        ("change", "value"),
    ],
}

# Update URL names per snapshot key (head_circumference differs from the
# dotted model name).
UPDATE_URL_NAMES = {
    "feeding": "core:feeding-update",
    "sleep": "core:sleep-update",
    "diaperchange": "core:diaperchange-update",
    "tummytime": "core:tummytime-update",
    "pumping": "core:pumping-update",
    "medication": "core:medication-update",
    "temperature": "core:temperature-update",
    "note": "core:note-update",
    "weight": "core:weight-update",
    "height": "core:height-update",
    "head_circumference": "core:head-circumference-update",
    "bmi": "core:bmi-update",
}


@register.filter
def section_label(key):
    """Plural model name for one snapshot section."""
    return str(SPECS_BY_KEY[key].model._meta.verbose_name_plural)


@register.filter
def section_columns(key):
    return SECTION_COLUMNS.get(key, [])


@register.filter
def section_metrics(key):
    return SECTION_METRICS.get(key, [])


@register.filter
def metric_label(metric):
    return str(METRIC_LABELS.get(metric, metric))


@register.filter
def get_item(mapping, key):
    return mapping.get(key) if mapping else None


@register.filter
def col_label(field_name, key):
    """Verbose field label as ``field|col_label:key``."""
    if field_name == "tags":
        return str(_("Tags"))
    return str(SPECS_BY_KEY[key].model._meta.get_field(field_name).verbose_name)


@register.filter
def choice_label(value, key_field):
    """
    Localized choice label as ``value|choice_label:"key:field"``; raw values
    without a matching choice (e.g. free-form medication names) pass through.
    """
    key, field_name = key_field.split(":", 1)
    field = SPECS_BY_KEY[key].model._meta.get_field(field_name)
    choices = dict(field.flatchoices)
    return str(choices.get(value, value))


@register.filter
def can_change(user, key):
    """Whether the user may edit current records of one snapshot section."""
    return user.has_perm(f"core.change_{SPECS_BY_KEY[key].perm}")


@register.filter
def col_kind(field_name, key):
    """Rendering kind (datetime/date/duration/...) of a section column."""
    for column, kind in SECTION_COLUMNS.get(key, []):
        if column == field_name:
            return kind
    if field_name == "tags":
        return "tags"
    return "value"


@register.filter
def from_iso(value):
    """Parse a frozen ISO datetime/date string back for formatting."""
    if not value:
        return ""
    if "T" in value:
        return parse_datetime(value)
    return parse_date(value)


@register.filter
def seconds_duration(seconds):
    if seconds is None:
        return ""
    return duration_string(datetime.timedelta(seconds=seconds), precision="m")


@register.filter
def choice_of(entry, field_name):
    """Frozen choice display as ``entry|choice_of:field``; fall back to raw."""
    return (
        entry.get("displays", {}).get(field_name)
        or entry["fields"].get(field_name)
        or ""
    )


@register.filter
def edit_url_name(key):
    return UPDATE_URL_NAMES.get(key)
