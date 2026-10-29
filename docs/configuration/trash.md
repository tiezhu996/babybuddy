# Trash

Deleting a feeding, a sleep entry or a note does not remove it immediately.
The entry moves to a trash: it disappears from the timeline, the dashboard,
reports and the API, but its date and time, child, tags, notes and any uploaded
image are all kept. It can be restored as the very same record from the
**Trash** page (or with the **Undo** link shown right after a deletion), and an
entry can also be restored through the [API](../api.md) with a `POST` to its
`restore` endpoint. Other kinds of records are still deleted outright.

## Retention period

Entries stay in the trash for 30 days by default. Override this with the
`TRASH_RETENTION_DAYS` environment variable, for example for one week:

```bash
TRASH_RETENTION_DAYS=7
```

The Trash page shows how many days each entry has left. Restoring an entry and
deleting it again starts the period over from the new deletion time.

## Emptying the trash

Entries that have outlived the retention period are removed by a command:

```bash
python manage.py purge_trash
```

Run it from cron or a systemd timer, and each run removes what is due and
exits. The period can be overridden for a run with `--days`. Only when this
runs is an entry really deleted -- together with its uploaded image -- and
only then is a `deleted` webhook event sent, as described under
[Webhooks](webhooks.md).

For a Docker install, which has no cron to add to, the command can keep running
and check the trash on a loop, in a second container that shares the
configuration and database with the app:

```bash
python manage.py purge_trash --every 3600
```

A run that fails -- the database briefly unreachable, or not migrated yet
while the app starts -- is reported and the next one goes ahead. Empty runs
write nothing to the log.
