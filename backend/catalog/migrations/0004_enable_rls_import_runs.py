# Row-level security on the import tables 0003 creates. Both carry org_id and
# are listed in ORG_SCOPED_TABLES.

from django.db import connection, migrations

from common.rls import get_disable_policy_sql, get_enable_policy_sql

TABLES = ("catalog_import_run", "catalog_import_file")


def enable_rls(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(get_enable_policy_sql(table))


def disable_rls(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(get_disable_policy_sql(table))


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("catalog", "0003_import_runs"),
    ]

    operations = [
        migrations.RunPython(enable_rls, disable_rls),
    ]
