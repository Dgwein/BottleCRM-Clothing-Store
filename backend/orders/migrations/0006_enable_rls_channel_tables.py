# Enable Row-Level Security on the tables 0005 creates. Both carry org_id and
# are registered in ORG_SCOPED_TABLES. get_enable_policy_sql() opens with DROP
# POLICY IF EXISTS, so this is idempotent.

from django.db import connection, migrations

from common.rls import get_disable_policy_sql, get_enable_policy_sql

TABLES = (
    "order_channel_listing",
    "order_work_order",
    "order_import_run",
    "order_import_run_entry",
)


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
        ("orders", "0005_sales_channels"),
    ]

    operations = [
        migrations.RunPython(enable_rls, disable_rls),
    ]
