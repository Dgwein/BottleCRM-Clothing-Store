from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("invoices", "0018_catalog_variants_and_sales_channel"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="availability_days",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Days to have the product ready after a sale. Empty is read as 0.",
                null=True,
                verbose_name="Availability Time (days)",
            ),
        ),
    ]
