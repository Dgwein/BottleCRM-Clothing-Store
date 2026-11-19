"""Row-level security on the channel sales tables, and the import command
under it (PostgreSQL only, as a role RLS binds)."""

import pytest
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction

from common.testing import rls_policy_gaps
from conftest import rls_org
from invoices.models import Product
from orders.models import (
    ChannelListing,
    ImportRun,
    ImportRunEntry,
    Order,
    ReturnInspection,
    WorkOrder,
)
from orders.tests.test_channel_import import report, sale

TABLES = [
    "order_channel_listing",
    "order_work_order",
    "order_import_run",
    "order_import_run_entry",
]


def _need_rls():
    if connection.vendor != "postgresql":
        pytest.skip("Row-level security is a PostgreSQL feature")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if cursor.fetchone()[0]:
            pytest.skip("Needs a role row-level security binds, as in CI")


def _import(org, tmp_path, *rows):
    path = tmp_path / f"{org.id}.xlsx"
    path.write_bytes(report(*rows))
    call_command(
        "import_channel_orders", str(path), org=str(org.id), channel="MERCADOLIBRE"
    )


@pytest.mark.postgres_only
def test_the_channel_tables_have_their_policies():
    if connection.vendor != "postgresql":
        pytest.skip("RLS policies exist only on PostgreSQL")
    assert rls_policy_gaps(TABLES) == []


@pytest.mark.postgres_only
def test_the_command_imports_under_its_orgs_context(org_a, org_b, tmp_path):
    _need_rls()
    for org in (org_a, org_b):
        _import(org, tmp_path, sale("S1"))
        # Shipped then returned, so each org also gets an inspection.
        _import(org, tmp_path, sale("S2", status="Entregado"))
        _import(org, tmp_path, sale("S2", status="Devolución finalizada"))

    for org in (org_a, org_b):
        with rls_org(org):
            assert set(Order.objects.values_list("org_id", flat=True)) == {org.id}
            assert Order.objects.filter(external_id="S1").count() == 1
            for model in (
                ChannelListing,
                WorkOrder,
                ReturnInspection,
                ImportRun,
                ImportRunEntry,
                Product,
            ):
                assert model.objects.exists()
                assert not model.objects.exclude(org=org).exists()


@pytest.mark.postgres_only
def test_channel_rows_cannot_be_written_into_another_org(org_a, org_b):
    _need_rls()
    with pytest.raises(DatabaseError), transaction.atomic():
        ChannelListing.objects.create(
            org=org_b, channel="MERCADOLIBRE", listing_id="MLM1"
        )
