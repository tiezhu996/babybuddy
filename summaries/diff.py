# -*- coding: utf-8 -*-
"""
Comparing a saved summary with the live data.

The comparison is computed on demand and never stored. Live data is re-read
with the same child, date range, selection semantics and (frozen) timezone as
the snapshot, then matched by record primary key per model: ids only in the
current set were added, ids only in the snapshot were deleted, and shared ids
whose serialized fields differ were changed. Only models present in the
snapshot are compared; current data for those sections is re-extracted
regardless of the viewing user's per-model permissions (a summary is one
artifact; see spec NFR-2).
"""

import datetime
from contextlib import nullcontext

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from summaries.snapshot import SPECS_BY_KEY, build_payload

# Human labels for the metric keys produced by snapshot.compute_stats.
METRIC_LABELS = {
    "count": _("Count"),
    "wet_count": _("Wet"),
    "solid_count": _("Solid"),
    "empty_count": _("Dry/empty"),
    "avg_interval_seconds": _("Average interval"),
    "avg_awake_interval_seconds": _("Average awake interval"),
    "amount_sum": _("Amount total"),
    "amount_count": _("Amount entries"),
    "amount_avg": _("Average amount"),
    "duration_total_seconds": _("Total duration"),
    "duration_avg_seconds": _("Average duration"),
    "nap_count": _("Nap count"),
    "nap_total_seconds": _("Total nap duration"),
    "count_by_type": _("By type"),
    "count_by_method": _("By method"),
    "count_by_name": _("By medication"),
    "min": _("Minimum"),
    "max": _("Maximum"),
    "avg": _("Average"),
    "latest": _("Latest"),
    "first": _("First in range"),
    "last": _("Latest in range"),
    "change": _("Change"),
}

# Metrics whose value is a duration in seconds (rendered as such).
DURATION_METRICS = {
    "avg_interval_seconds",
    "avg_awake_interval_seconds",
    "duration_total_seconds",
    "duration_avg_seconds",
    "nap_total_seconds",
}

# Metrics whose value is a dictionary (rendered compactly).
DICT_METRICS = {"count_by_type", "count_by_method", "count_by_name"}

TAGS_FIELD = "tags"


def _field_label(spec, field_name):
    if field_name == TAGS_FIELD:
        return str(_("Tags"))
    return str(spec.model._meta.get_field(field_name).verbose_name)


def _entry_changes(spec, old_entry, new_entry):
    """Field-level differences between two serialized entries."""
    changes = []
    field_names = sorted(set(old_entry["fields"]) | set(new_entry["fields"]))
    for field_name in field_names:
        old_value = old_entry["fields"].get(field_name)
        new_value = new_entry["fields"].get(field_name)
        if old_value != new_value:
            changes.append(
                {
                    "field": field_name,
                    "label": _field_label(spec, field_name),
                    "old": old_value,
                    "new": new_value,
                    "old_display": old_entry.get("displays", {}).get(field_name),
                    "new_display": new_entry.get("displays", {}).get(field_name),
                }
            )
    if old_entry.get(TAGS_FIELD, []) != new_entry.get(TAGS_FIELD, []):
        changes.append(
            {
                "field": TAGS_FIELD,
                "label": _field_label(spec, TAGS_FIELD),
                "old": old_entry.get(TAGS_FIELD, []),
                "new": new_entry.get(TAGS_FIELD, []),
                "old_display": None,
                "new_display": None,
            }
        )
    return changes


def _canonical(value):
    """Order-insensitive form so dict counters only differ on content."""
    if isinstance(value, dict):
        return dict(sorted(value.items()))
    return value


def _stats_pairs(key, old_stats, new_stats):
    """Snapshot/current pairs for every statistic of one model."""
    pairs = []
    for metric, old_value in old_stats.items():
        new_value = new_stats.get(metric)
        pairs.append(
            {
                "metric": metric,
                "label": str(METRIC_LABELS.get(metric, metric)),
                "old": old_value,
                "new": new_value,
                "changed": _canonical(old_value) != _canonical(new_value),
                "is_duration": metric in DURATION_METRICS,
                "is_dict": metric in DICT_METRICS,
            }
        )
    return pairs


def _index(entries):
    return {entry["id"]: entry for entry in entries}


def diff_summary(summary):
    """
    Build the comparison between one saved summary and the current records.

    Current data is re-extracted for every model the snapshot contains,
    regardless of the viewing user's per-model permissions. A summary is one
    artifact (see NFR-2): anyone allowed to open it may see both its frozen
    contents and what changed in those same sections afterwards. Filtering by
    the viewer's permissions here would make still-existing records show up as
    "deleted" for restricted users.

    :returns: dict with ``generated_at`` (of the current extraction),
        ``sections`` per snapshot model and an overall ``has_changes`` flag.
    """
    data = summary.data
    start = datetime.date.fromisoformat(data["range"]["start"])
    end = datetime.date.fromisoformat(data["range"]["end"])
    tz_name = data.get("timezone")

    tz_context = timezone.override(tz_name) if tz_name else nullcontext()
    with tz_context:
        current = build_payload(summary.child, start, end, None)

    sections = []
    for key in data["included_models"]:
        spec = SPECS_BY_KEY[key]
        old_entries = _index(data["entries"].get(key, []))
        new_entries = _index(current["entries"].get(key, []))

        added_ids = sorted(set(new_entries) - set(old_entries))
        deleted_ids = sorted(set(old_entries) - set(new_entries))
        shared_ids = sorted(set(old_entries) & set(new_entries))

        changed = []
        for entry_id in shared_ids:
            field_changes = _entry_changes(
                spec, old_entries[entry_id], new_entries[entry_id]
            )
            if field_changes:
                changed.append(
                    {
                        "id": entry_id,
                        "changes": field_changes,
                        "old_entry": old_entries[entry_id],
                        "new_entry": new_entries[entry_id],
                    }
                )

        stats_pairs = _stats_pairs(
            key, data["stats"].get(key, {}), current["stats"].get(key, {})
        )
        stats_changed = sum(1 for pair in stats_pairs if pair["changed"])

        section = {
            "key": key,
            "added": [new_entries[entry_id] for entry_id in added_ids],
            "deleted": [old_entries[entry_id] for entry_id in deleted_ids],
            "changed": changed,
            "stats": stats_pairs,
            "stats_changed_count": stats_changed,
            "has_changes": bool(added_ids or deleted_ids or changed)
            or stats_changed > 0,
        }
        sections.append(section)

    return {
        "generated_at": current["generated_at"],
        "sections": sections,
        "has_changes": any(section["has_changes"] for section in sections),
    }
