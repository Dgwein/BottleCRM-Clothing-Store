"""What the models refuse on their own, and the admin's setup path."""

import pytest
from django.contrib.admin.sites import AdminSite
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from django.utils import timezone

from common.models import Teams
from fulfillment import services
from fulfillment.admin import DispatchOrderAdmin, SaleAdmin
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
from orders.models import OrderLineItem


def test_a_sale_takes_its_orders_org(make_order, ml, org_a):
    sale = Sale(order=make_order(), channel=ml, committed_delivery_at=timezone.now())
    sale.save()
    assert sale.org == org_a


def test_a_sale_cannot_sit_in_another_org_than_its_order(make_order, ml, org_b):
    with pytest.raises(ValueError):
        Sale.objects.create(
            org=org_b,
            order=make_order(),
            channel=ml,
            committed_delivery_at=timezone.now(),
        )


def test_a_sale_cannot_use_another_orgs_channel(make_order, org_b):
    theirs = SalesChannel.objects.create(org=org_b, name="Theirs")
    with pytest.raises(ValueError):
        Sale.objects.create(
            order=make_order(), channel=theirs, committed_delivery_at=timezone.now()
        )


def test_a_preparation_cannot_point_at_another_orders_line(make_order, ml, org_a):
    sale = services.register_sale(make_order(), ml, timezone.now())
    other = OrderLineItem.objects.create(
        order=make_order(name="Other"), name="Hat", org=org_a
    )
    with pytest.raises(ValueError):
        PreparationOrder.objects.create(sale=sale, description="Sew", line_item=other)


def test_children_take_the_sales_org(make_order, ml, org_a):
    sale = services.register_sale(
        make_order(), ml, timezone.now(), [{"description": "Sew"}]
    )
    assert sale.preparation_orders.get().org == org_a
    assert sale.packaging_order.org == org_a


def test_a_channel_kind_option_without_a_channel_is_refused_by_the_database(org_a):
    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryOption.objects.create(org=org_a, name="Pickup", kind="CHANNEL_PICKUP")


def test_channel_names_are_unique_per_org(org_a, org_b):
    SalesChannel.objects.create(org=org_a, name="Amazon")
    SalesChannel.objects.create(org=org_b, name="Amazon")
    with pytest.raises(IntegrityError), transaction.atomic():
        SalesChannel.objects.create(org=org_a, name="Amazon")


def test_a_sale_added_in_the_admin_gets_its_packaging_order(make_order, ml, admin_user):
    request = RequestFactory().post("/admin/")
    request.user = admin_user
    sale = Sale(order=make_order(), channel=ml, committed_delivery_at=timezone.now())

    SaleAdmin(Sale, AdminSite()).save_model(request, sale, form=None, change=False)

    assert PackagingOrder.objects.filter(sale=sale, status="PENDING").count() == 1
    SaleAdmin(Sale, AdminSite()).save_model(request, sale, form=None, change=True)
    assert PackagingOrder.objects.filter(sale=sale).count() == 1


def test_dispatch_orders_are_read_only_in_the_admin(admin_user):
    request = RequestFactory().get("/admin/")
    request.user = admin_user
    model_admin = DispatchOrderAdmin(DispatchOrder, AdminSite())
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False


def test_a_departure_has_one_dispatch_order(make_option):
    option = make_option("Pickup", "10")
    when = timezone.now()
    DispatchOrder.objects.create(delivery_option=option, departs_at=when)
    with pytest.raises(IntegrityError), transaction.atomic():
        DispatchOrder.objects.create(delivery_option=option, departs_at=when)


def test_a_dispatch_item_cannot_carry_another_orgs_sale(make_option, make_order, org_b):
    theirs = SalesChannel.objects.create(org=org_b, name="Theirs")
    their_sale = services.register_sale(make_order(org=org_b), theirs, timezone.now())
    order = DispatchOrder.objects.create(
        delivery_option=make_option("Pickup", "10"), departs_at=timezone.now()
    )
    with pytest.raises(ValueError):
        DispatchOrderItem.objects.create(
            dispatch_order=order,
            sale=their_sale,
            estimated_delivery_at=timezone.now(),
        )


def _team(org, name="Packers"):
    return Teams.objects.create(name=name, description="", org=org)


def test_only_delivery_has_a_close_level_in_the_database(org_a):
    team = _team(org_a)
    QueueAssignment.objects.create(team=team, order_type="DELIVERY", access="CLOSE")
    with pytest.raises(IntegrityError), transaction.atomic():
        QueueAssignment.objects.create(
            team=team, order_type="PACKAGING", access="CLOSE"
        )


def test_a_team_has_one_assignment_per_order_type(org_a):
    team = _team(org_a)
    QueueAssignment.objects.create(team=team, order_type="PACKAGING", access="VIEW")
    with pytest.raises(IntegrityError), transaction.atomic():
        QueueAssignment.objects.create(
            team=team, order_type="PACKAGING", access="HANDLE"
        )


def test_an_assignment_takes_its_teams_org(org_a, org_b):
    mine = QueueAssignment.objects.create(team=_team(org_a), order_type="PACKAGING")
    assert mine.org == org_a
    with pytest.raises(ValueError):
        QueueAssignment.objects.create(
            org=org_a, team=_team(org_b, "Theirs"), order_type="PACKAGING"
        )


def test_an_options_date_has_one_setting_in_the_database(make_option):
    option = make_option("Courier", "10")
    DeliveryOptionDay.objects.create(
        delivery_option=option, date="2026-12-25", kind="FREE"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryOptionDay.objects.create(
            delivery_option=option, date="2026-12-25", kind="WORKING"
        )


def test_a_free_day_has_no_opening_in_the_database(make_option):
    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryOptionDay.objects.create(
            delivery_option=make_option("Courier", "10"),
            date="2026-12-25",
            kind="FREE",
            opens_at="09:00",
        )


def test_a_day_takes_its_options_org(make_option, org_a, org_b):
    option = make_option("Courier", "10")
    day = DeliveryOptionDay.objects.create(
        delivery_option=option, date="2026-12-25", kind="FREE"
    )
    assert day.org == org_a
    with pytest.raises(ValueError):
        DeliveryOptionDay.objects.create(
            org=org_b, delivery_option=option, date="2026-12-26", kind="FREE"
        )
