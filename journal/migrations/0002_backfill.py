from django.db import migrations


def forwards(apps, schema_editor):
    from journal.backfill import backfill
    backfill(apps)


class Migration(migrations.Migration):
    """The 🕘 Verlauf starts with what happened before (from the change history)."""

    dependencies = [
        ("journal", "0001_initial"),
        ("planning", "0009_notice_printed_by"),
        ("buildings", "0004_prices"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
