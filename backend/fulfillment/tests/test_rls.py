"""Row-level security on the fulfilment tables (PostgreSQL only)."""

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from common.models import Teams
from common.testing import rls_policy_gaps
from conftest import rls_org
from fulfillment import services
from fulfillment.models import (
    DeliveryOption,
    DeliveryOptionDay,
    DispatchOrder,
    DispatchOrderItem,
    PackagingOrder,
    PreparationOrder,
    QueueAssignment,
    Sale,
    SalesChannel,
)

TABLES = [
    "delivery_option",
    "delivery_option_day",
    "sale",
    "preparation_order",
    "packaging_order",
    "dispatch_order",
    "dispatch_order_item",
    "queue_assignment",
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


@pytest.mark.postgres_only
def test_every_table_has_its_policies():
    if connection.vendor != "postgresql":
        pytest.skip("RLS policies exist only on PostgreSQL")
    assert rls_policy_gaps(TABLES) == []


def _packed_sale(org, make_order):
    channel = SalesChannel.objects.create(org=org, name=f"Channel {org.name}")
    option = DeliveryOption.objects.create(
        org=org, name="Common", kind="COMMON_DISPATCH"
    )
    DeliveryOptionDay.objects.create(
        delivery_option=option, date=timezone.localdate(), kind="WORKING"
    )
    sale = services.register_sale(
        make_order(org=org), channel, timezone.now(), [{"description": "Sew"}]
    )
    services.complete_preparation(sale.preparation_orders.get())
    services.complete_packaging(sale.packaging_order)
    team = Teams.objects.create(name="Crew", description="", org=org)
    QueueAssignment.objects.create(team=team, order_type="PACKAGING")
    services.create_dispatch_orders(org)
    return sale


@pytest.mark.postgres_only
def test_another_orgs_rows_are_invisible(org_a, org_b, make_order):
    _need_rls()
    mine = _packed_sale(org_a, make_order)
    with rls_org(org_b):
        theirs = _packed_sale(org_b, make_order)
        assert list(Sale.objects.all()) == [theirs]
        for model in (
            SalesChannel,
            DeliveryOption,
            DeliveryOptionDay,
            PreparationOrder,
            PackagingOrder,
            DispatchOrder,
            DispatchOrderItem,
            QueueAssignment,
        ):
            assert set(model.objects.values_list("org_id", flat=True)) == {org_b.id}

    assert list(Sale.objects.all()) == [mine]
    assert DispatchOrderItem.objects.get().sale == mine


@pytest.mark.postgres_only
def test_recalculating_and_closing_under_one_org_leave_the_other_alone(
    org_a, org_b, make_order
):
    _need_rls()
    _packed_sale(org_a, make_order)
    with rls_org(org_b):
        _packed_sale(org_b, make_order)
        theirs = DispatchOrder.objects.get()
    services.create_dispatch_orders(org_a)
    services.close_dispatch_order(DispatchOrder.objects.get())
    assert DispatchOrder.objects.get().status == "CLOSED"
    with rls_org(org_b):
        assert DispatchOrder.objects.get() == theirs
        assert DispatchOrder.objects.get().status == "OPEN"


@pytest.mark.postgres_only
def test_a_channel_cannot_be_written_into_another_org(org_a, org_b):
    _need_rls()
    with pytest.raises(DatabaseError), transaction.atomic():
        SalesChannel.objects.create(org=org_b, name="Planted")
