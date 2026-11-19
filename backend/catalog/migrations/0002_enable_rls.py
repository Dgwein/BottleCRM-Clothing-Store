# Row-level security on every catalog table. All ten carry org_id and are
# listed in ORG_SCOPED_TABLES; 0001 creates them, so a hard dependency is
# enough and no existence check is needed.

from django.db import connection, migrations

from common.rls import get_disable_policy_sql, get_enable_policy_sql

TABLES = (
    "catalog_sales_channel",
    "catalog_category",
    "catalog_attribute",
    "catalog_attribute_option",
    "catalog_category_attribute",
    "catalog_channel_attribute",
    "catalog_product",
    "catalog_attribute_value",
    "catalog_listing",
    "catalog_listing_variant",
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
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(enable_rls, disable_rls),
    ]
