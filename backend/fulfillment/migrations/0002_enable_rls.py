from django.db import connection, migrations

from common.rls import get_disable_policy_sql, get_enable_policy_sql

# Registered in common/rls/__init__.py:ORG_SCOPED_TABLES.
TABLES = (
    "sales_channel",
    "delivery_option",
    "delivery_option_day",
    "sale",
    "preparation_order",
    "packaging_order",
    "dispatch_order",
    "dispatch_order_item",
    "queue_assignment",
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
    # RLS policy creation cannot run inside an atomic block.
    atomic = False

    dependencies = [
        ("fulfillment", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(enable_rls, reverse_code=disable_rls),
    ]
