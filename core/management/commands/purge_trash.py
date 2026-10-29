# -*- coding: utf-8 -*-
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core.trash import TRASHABLE_MODELS, purge_expired_trash


def positive_integer(value):
    number = int(value)
    if number <= 0:
        raise ValueError(value)
    return number


def positive_seconds(value):
    seconds = float(value)
    if seconds <= 0:
        raise ValueError(value)
    return seconds


class Command(BaseCommand):
    help = (
        "Permanently delete trashed feedings, sleep entries and notes whose "
        "retention period has passed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=positive_integer,
            default=None,
            help="Override the retention period (TRASH_RETENTION_DAYS).",
        )
        parser.add_argument(
            "--every",
            type=positive_seconds,
            default=None,
            metavar="SECONDS",
            help=(
                "Keep running and purge every SECONDS seconds instead of "
                "once. Checking an empty trash is one small query."
            ),
        )

    def handle(self, *args, **options):
        if options["every"] is None:
            total, counts = purge_expired_trash(retention_days=options["days"])
            self.stdout.write(self.report(total, counts))
            return
        try:
            self.run_every(options["every"], options["days"])
        except KeyboardInterrupt:
            pass

    def report(self, total, counts):
        if not total:
            return self.style.SUCCESS("Nothing to purge.")
        detail = ", ".join(
            "{}: {}".format(
                model._meta.verbose_name_plural.title(), counts[model.model_name]
            )
            for model in TRASHABLE_MODELS
        )
        return self.style.SUCCESS("Purged {} entry(s) ({}).".format(total, detail))

    def run_every(self, seconds, days):
        """
        Purge on a loop in one process, like deliver_webhooks. A run that
        fails is reported and the loop goes on. Only a run that removed
        something is written to the log.
        """
        while True:
            close_old_connections()
            try:
                total, counts = purge_expired_trash(retention_days=days)
            except Exception as error:
                self.stderr.write("Purge run failed: {}".format(error))
                self.stderr.flush()
            else:
                if total:
                    self.stdout.write(self.report(total, counts))
                    self.stdout.flush()
            time.sleep(seconds)
