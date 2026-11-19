import django.core.validators
from django.db import migrations, models
from django.utils.text import slugify


def _code(name, org_id, channels):
    base = (
        slugify(name).replace("mercado-libre", "mercadolibre")[:50] or "channel"
    )
    candidate = base
    suffix = 2
    while channels.objects.filter(org_id=org_id, code=candidate).exists():
        tail = f"-{suffix}"
        candidate = f"{base[: 50 - len(tail)]}{tail}"
        suffix += 1
    return candidate


def merge_fulfillment_channels(apps, schema_editor):
    connection = schema_editor.connection
    Org = apps.get_model("common", "Org")
    CatalogChannel = apps.get_model("catalog", "SalesChannel")
    FulfillmentChannel = apps.get_model("fulfillment", "SalesChannel")
    DeliveryOption = apps.get_model("fulfillment", "DeliveryOption")
    Sale = apps.get_model("fulfillment", "Sale")
    Order = apps.get_model("orders", "Order")

    def set_context(org_id):
        if connection.vendor != "postgresql":
            return
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.current_org', %s, false)", [str(org_id)]
            )

    try:
        for org_id in Org.objects.values_list("id", flat=True).iterator():
            set_context(org_id)
            for old in FulfillmentChannel.objects.filter(org_id=org_id).order_by(
                "updated_at", "id"
            ):
                channel = (
                    CatalogChannel.objects.filter(
                        org_id=org_id, name__iexact=old.name
                    )
                    .order_by("created_at", "id")
                    .first()
                )
                if channel is None:
                    # The old fulfillment model had no channel kind or code.
                    # Preserve each row, default it to ONLINE, and let admins
                    # reclassify physical channels after migration.
                    channel = CatalogChannel.objects.create(
                        id=old.pk,
                        org_id=org_id,
                        name=old.name,
                        code=_code(old.name, org_id, CatalogChannel),
                        kind="ONLINE",
                        is_active=True,
                        late_penalty=old.late_penalty,
                        created_by_id=old.created_by_id,
                        updated_by_id=old.updated_by_id,
                    )
                else:
                    channel.late_penalty = old.late_penalty
                    channel.save(update_fields=["late_penalty", "updated_at"])

                DeliveryOption.objects.filter(
                    org_id=org_id, channel_id=old.pk
                ).update(channel_id=channel.pk)
                Sale.objects.filter(org_id=org_id, channel_id=old.pk).update(
                    channel_id=channel.pk
                )

            for sale in Sale.objects.filter(org_id=org_id).only(
                "order_id", "channel_id"
            ):
                Order.objects.filter(org_id=org_id, pk=sale.order_id).exclude(
                    sales_channel_id=sale.channel_id
                ).update(sales_channel_id=sale.channel_id)
    finally:
        set_context("")


def restore_fulfillment_channels(apps, schema_editor):
    connection = schema_editor.connection
    Org = apps.get_model("common", "Org")
    CatalogChannel = apps.get_model("catalog", "SalesChannel")
    FulfillmentChannel = apps.get_model("fulfillment", "SalesChannel")
    DeliveryOption = apps.get_model("fulfillment", "DeliveryOption")
    Sale = apps.get_model("fulfillment", "Sale")

    def set_context(org_id):
        if connection.vendor != "postgresql":
            return
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.current_org', %s, false)", [str(org_id)]
            )

    try:
        for org_id in Org.objects.values_list("id", flat=True).iterator():
            set_context(org_id)
            used_ids = set(
                DeliveryOption.objects.filter(org_id=org_id)
                .exclude(channel_id__isnull=True)
                .values_list("channel_id", flat=True)
            )
            used_ids.update(
                Sale.objects.filter(org_id=org_id).values_list(
                    "channel_id", flat=True
                )
            )
            for channel in CatalogChannel.objects.filter(
                org_id=org_id, pk__in=used_ids
            ):
                FulfillmentChannel.objects.get_or_create(
                    pk=channel.pk,
                    defaults={
                        "org_id": org_id,
                        "name": channel.name,
                        "late_penalty": channel.late_penalty,
                    },
                )
    finally:
        set_context("")


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_enable_rls_import_runs"),
        ("fulfillment", "0002_enable_rls"),
    ]

    operations = [
        migrations.AddField(
            model_name="saleschannel",
            name="late_penalty",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Charged once per sale that reaches the buyer after the committed time.",
                max_digits=12,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.RunPython(
            merge_fulfillment_channels, restore_fulfillment_channels
        ),
    ]
