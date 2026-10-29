# -*- coding: utf-8 -*-
"""
Building the frozen payload of a visit summary.

The payload is assembled entirely in memory from the live care records and
then stored verbatim in ``VisitSummary.data``. Nothing here reads or writes
``VisitSummary`` rows except :func:`generate_summary`, which creates exactly
one row inside a transaction. Every value in the payload is JSON-native so a
saved summary renders without touching the ``core`` tables.

Selection semantics (statistics and details always use the same set):
- instant models (one ``time`` field): the entry's local date is in range;
- interval models (``start``/``end``): the entry's *start* is in range, so a
  record crossing the end boundary is counted with its full duration;
- date models (``date`` field): the date itself is in range.
"""

import datetime
from collections import OrderedDict, Counter
from dataclasses import dataclass

from django.db import transaction
from django.db.models import FileField
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core import models

SNAPSHOT_VERSION = 1


@dataclass(frozen=True)
class ModelSpec:
    key: str
    model: type
    # "instant" (time field), "interval" (start/end) or "date" (date field).
    kind: str
    anchor: str
    # Suffix of the core.view_<perm> permission guarding the model.
    perm: str
    # Value field for the measurement-style models, else None.
    value_field: str = None


# Order is the display order of the summary sections.
REGISTRY = (
    ModelSpec("feeding", models.Feeding, "interval", "start", "feeding"),
    ModelSpec("sleep", models.Sleep, "interval", "start", "sleep"),
    ModelSpec("diaperchange", models.DiaperChange, "instant", "time", "diaperchange"),
    ModelSpec("tummytime", models.TummyTime, "interval", "start", "tummytime"),
    ModelSpec("pumping", models.Pumping, "interval", "start", "pumping"),
    ModelSpec("medication", models.Medication, "instant", "time", "medication"),
    ModelSpec("temperature", models.Temperature, "instant", "time", "temperature"),
    ModelSpec("note", models.Note, "instant", "time", "note"),
    ModelSpec("weight", models.Weight, "date", "date", "weight", "weight"),
    ModelSpec("height", models.Height, "date", "date", "height", "height"),
    ModelSpec(
        "head_circumference",
        models.HeadCircumference,
        "date",
        "date",
        "headcircumference",
        "head_circumference",
    ),
    ModelSpec("bmi", models.BMI, "date", "date", "bmi", "bmi"),
)

SPECS_BY_KEY = OrderedDict((spec.key, spec) for spec in REGISTRY)


def range_bounds(start_date, end_date):
    """
    Turn two local calendar dates into aware datetimes covering every local
    instant of the range (start day 00:00:00 through end day 23:59:59.999999).
    """
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(
        datetime.datetime.combine(start_date, datetime.time.min), tz
    )
    end_dt = timezone.make_aware(
        datetime.datetime.combine(end_date, datetime.time.max), tz
    )
    return start_dt, end_dt


def select_instances(spec, child, start_date, end_date):
    """QuerySet of in-range instances for one model, in default (desc) order."""
    queryset = spec.model.objects.filter(child=child)
    if spec.kind == "date":
        queryset = queryset.filter(
            **{
                f"{spec.anchor}__gte": start_date,
                f"{spec.anchor}__lte": end_date,
            }
        )
    else:
        start_dt, end_dt = range_bounds(start_date, end_date)
        queryset = queryset.filter(**{f"{spec.anchor}__range": (start_dt, end_dt)})
    return queryset.prefetch_related("tags")


def _serialize_value(field, value):
    """Convert one model field value to a JSON-native, display-stable form."""
    if value is None:
        return None
    if isinstance(field, FileField):
        # Only the reference is frozen; see Non-Goals regarding binaries.
        return value.name
    if isinstance(value, datetime.datetime):
        # Local wall clock with its UTC offset: unambiguous and what the
        # generator (and doctor) actually saw.
        return timezone.localtime(value).isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return value.total_seconds()
    if isinstance(value, datetime.time):
        return value.isoformat()
    return value


def serialize_entry(instance):
    """
    Serialize one record: all concrete business fields (besides the child
    link), choice display values frozen in the generator's language, and the
    tag names.
    """
    fields = {}
    displays = {}
    for field in instance._meta.concrete_fields:
        if field.name in ("id", "child"):
            continue
        fields[field.name] = _serialize_value(field, getattr(instance, field.name))
        if field.choices:
            displays[field.name] = getattr(instance, f"get_{field.name}_display")()

    return {
        "id": instance.pk,
        "fields": fields,
        "displays": displays,
        "tags": sorted(tag.name for tag in instance.tags.all()),
    }


def _duration_seconds(value):
    return value.total_seconds() if value is not None else 0


def _average_interval(instances, start_attr, end_attr):
    """Mean gap (seconds) between chronologically ordered interval records."""
    total = 0.0
    gaps = 0
    previous = None
    for instance in sorted(instances, key=lambda item: getattr(item, start_attr)):
        if previous is not None:
            total += (
                getattr(instance, start_attr) - getattr(previous, end_attr)
            ).total_seconds()
            gaps += 1
        previous = instance
    return total / gaps if gaps else None


def _measurement_stats(instances, value_field):
    instances = list(instances)
    stats = {"count": len(instances), "first": None, "last": None, "change": None}
    if not instances:
        return stats
    # Default ordering is "-date", "-id", so the first row is the newest.
    newest = instances[0]
    oldest = instances[-1]
    stats["first"] = getattr(oldest, value_field)
    stats["last"] = getattr(newest, value_field)
    if newest.pk != oldest.pk:
        stats["change"] = stats["last"] - stats["first"]
    return stats


def compute_stats(key, instances):
    """
    Range statistics for one model. Durations are seconds; ``None`` means the
    metric cannot be computed from the available records.
    """
    instances = list(instances)
    count = len(instances)

    if key == "diaperchange":
        wet = sum(1 for i in instances if i.wet)
        solid = sum(1 for i in instances if i.solid)
        empty = sum(1 for i in instances if not i.wet and not i.solid)
        amounts = [i.amount for i in instances if i.amount is not None]
        return {
            "count": count,
            "wet_count": wet,
            "solid_count": solid,
            "empty_count": empty,
            "avg_interval_seconds": _average_interval(instances, "time", "time"),
            "amount_sum": sum(amounts),
            "amount_count": len(amounts),
        }

    if key == "feeding":
        durations = [_duration_seconds(i.duration) for i in instances]
        amounts = [i.amount for i in instances if i.amount is not None]
        return {
            "count": count,
            "duration_total_seconds": sum(durations),
            "duration_avg_seconds": sum(durations) / count if count else None,
            "avg_interval_seconds": _average_interval(instances, "start", "end"),
            "amount_sum": sum(amounts),
            "amount_count": len(amounts),
            "count_by_type": dict(Counter(i.type for i in instances)),
            "count_by_method": dict(Counter(i.method for i in instances)),
        }

    if key == "sleep":
        durations = [_duration_seconds(i.duration) for i in instances]
        naps = [i for i in instances if i.nap]
        nap_durations = [_duration_seconds(i.duration) for i in naps]
        return {
            "count": count,
            "duration_total_seconds": sum(durations),
            "duration_avg_seconds": sum(durations) / count if count else None,
            "nap_count": len(naps),
            "nap_total_seconds": sum(nap_durations),
            "avg_awake_interval_seconds": _average_interval(instances, "start", "end"),
        }

    if key == "tummytime":
        durations = [_duration_seconds(i.duration) for i in instances]
        return {
            "count": count,
            "duration_total_seconds": sum(durations),
            "duration_avg_seconds": sum(durations) / count if count else None,
        }

    if key == "pumping":
        durations = [_duration_seconds(i.duration) for i in instances]
        amounts = [i.amount for i in instances if i.amount is not None]
        return {
            "count": count,
            "duration_total_seconds": sum(durations),
            "amount_sum": sum(amounts),
            "amount_count": len(amounts),
            "amount_avg": sum(amounts) / len(amounts) if amounts else None,
        }

    if key == "medication":
        return {
            "count": count,
            "count_by_name": dict(Counter(i.name for i in instances)),
        }

    if key == "temperature":
        values = [i.temperature for i in instances]
        latest = instances[0].temperature if instances else None
        return {
            "count": count,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "avg": sum(values) / count if count else None,
            "latest": latest,
        }

    if key == "note":
        return {"count": count}

    spec = SPECS_BY_KEY[key]
    if spec.kind == "date":
        return _measurement_stats(instances, spec.value_field)

    return {"count": count}


def _user_label(user):
    if user is None or not user.is_authenticated:
        return None
    return user.get_full_name() or user.get_username()


def build_payload(child, start_date, end_date, user=None):
    """
    Assemble the complete, JSON-native snapshot payload in memory. Sections
    the user lacks ``core.view_*`` permission for are omitted entirely.
    """
    included_models = []
    entries = OrderedDict()
    counts = OrderedDict()
    stats = OrderedDict()

    for spec in REGISTRY:
        if (
            user is not None
            and user.is_authenticated
            and not user.has_perm(f"core.view_{spec.perm}")
        ):
            continue
        instances = list(select_instances(spec, child, start_date, end_date))
        included_models.append(spec.key)
        entries[spec.key] = [serialize_entry(instance) for instance in instances]
        counts[spec.key] = len(instances)
        stats[spec.key] = compute_stats(spec.key, instances)

    birth_time = child.birth_time.isoformat() if child.birth_time else None
    due_date = child.due_date.isoformat() if child.due_date else None
    return {
        "version": SNAPSHOT_VERSION,
        "generated_at": timezone.now().isoformat(),
        "generated_by": _user_label(user),
        "timezone": timezone.get_current_timezone_name(),
        "child": {
            "name": child.name(),
            "first_name": child.first_name,
            "last_name": child.last_name,
            "birth_date": child.birth_date.isoformat(),
            "birth_time": birth_time,
            "due_date": due_date,
        },
        "range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "included_models": included_models,
        "counts": counts,
        "stats": stats,
        "entries": entries,
    }


def generate_summary(child, start_date, end_date, user=None, title=""):
    """
    Create one VisitSummary from a fully built payload.

    Everything runs in a single transaction: the queries only read, and the
    single write happens after the whole payload has been built successfully,
    so any failure during construction rolls back and leaves no half row.
    """
    from summaries.models import VisitSummary

    with transaction.atomic():
        payload = build_payload(child, start_date, end_date, user)
        generator = user if user is not None and user.is_authenticated else None
        return VisitSummary.objects.create(
            child=child,
            title=title.strip() if title else "",
            start_date=start_date,
            end_date=end_date,
            generated_by=generator,
            data=payload,
        )
