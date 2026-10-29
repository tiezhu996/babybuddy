# -*- coding: utf-8 -*-
"""
Emptying the trash.

Entries are hard deleted one at a time rather than with a bulk ``QuerySet``
delete on purpose: a Note has to take its uploaded image with it, and each
real deletion fires the usual ``post_delete`` signal so the webhook queue
records ``deleted`` exactly when the row is actually gone.
"""

from django.conf import settings
from django.utils import timezone

from core.models import Feeding, Note, Sleep

#: Order is also the order of the counts reported by the purge command.
TRASHABLE_MODELS = (Feeding, Sleep, Note)


def purge_expired_trash(now=None, retention_days=None):
    """
    Hard delete trashed entries older than the retention period.

    :param now: the current time, defaulting to the real one (injected by
        tests to pin the cutoff).
    :param retention_days: override ``BABY_BUDDY["TRASH_RETENTION_DAYS"]``.
    :return: ``(total, counts_by_model_name)``.
    """
    now = now or timezone.now()
    retention_days = (
        retention_days
        if retention_days is not None
        else settings.BABY_BUDDY["TRASH_RETENTION_DAYS"]
    )
    cutoff = now - timezone.timedelta(days=retention_days)

    counts = {}
    total = 0
    for model in TRASHABLE_MODELS:
        instances = list(
            model.all_objects.trashed().filter(deleted_at__lt=cutoff).order_by("pk")
        )
        for instance in instances:
            instance.hard_delete()
        counts[model.model_name] = len(instances)
        total += len(instances)
    return total, counts
