# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Feedings, sleep and notes become soft-deletable: a deleted row is kept
    with ``deleted_at`` stamped and can be restored until the trash is
    purged. Existing rows are NULL, i.e. live entries.
    """

    dependencies = [
        ("core", "0039_child_due_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="feeding",
            name="deleted_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                editable=False,
                null=True,
                verbose_name="Deleted at",
            ),
        ),
        migrations.AddField(
            model_name="note",
            name="deleted_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                editable=False,
                null=True,
                verbose_name="Deleted at",
            ),
        ),
        migrations.AddField(
            model_name="sleep",
            name="deleted_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                editable=False,
                null=True,
                verbose_name="Deleted at",
            ),
        ),
    ]
