import django.db.models.deletion
from django.db import connection, migrations, models

from common.rls import get_disable_policy_sql, get_enable_policy_sql


def remove_sales_channel_policy(apps, schema_editor):
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(get_disable_policy_sql("sales_channel"))


def restore_sales_channel_policy(apps, schema_editor):
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(get_enable_policy_sql("sales_channel"))


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0005_unify_fulfillment_channels"),
        ("fulfillment", "0002_enable_rls"),
    ]

    operations = [
        migrations.RunPython(
            remove_sales_channel_policy, restore_sales_channel_policy
        ),
        migrations.AlterField(
            model_name="deliveryoption",
            name="channel",
            field=models.ForeignKey(
                blank=True,
                help_text="Only this channel's sales may use it. Blank serves every channel.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="delivery_options",
                to="catalog.saleschannel",
            ),
        ),
        migrations.AlterField(
            model_name="sale",
            name="channel",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sales",
                to="catalog.saleschannel",
            ),
        ),
        migrations.DeleteModel(name="SalesChannel"),
    ]
