"""Dispatch: the plan preview, dispatch orders created and recalculated on
demand, and closing one, which fixes its actual figures."""

from datetime import time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from common.models import PersonalAccessToken
from fulfillment import services
from fulfillment.models import (
    DeliveryOptionDay,
    DispatchOrder,
    DispatchOrderItem,
    SalesChannel,
)

# Every member here is on the store's team, which handles every order type;
# what a member without it may do is in test_permissions.py.
pytestmark = pytest.mark.usefixtures("crew")

PLAN = "/api/fulfillment/dispatch-plan/"
DISPATCH_ORDERS = "/api/fulfillment/dispatch-orders/"
MANUAL = "/api/fulfillment/dispatch-orders/manual/"
WEEK = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
TZ = ZoneInfo("America/Chicago")


def _detail(dispatch_order):
    return f"{DISPATCH_ORDERS}{dispatch_order.id}/"


def _close(dispatch_order):
    return f"{DISPATCH_ORDERS}{dispatch_order.id}/close/"


def _agency(make_option, channel, cost="10", **fields):
    return make_option("Agency", cost, channel=channel, kind="CHANNEL_AGENCY", **fields)


class TestPreview:
    def test_the_preview_writes_nothing(self, user_client, ml, packed, make_option):
        sale = packed(ml)
        option = _agency(make_option, ml, "30")

        response = user_client.get(PLAN)

        assert response.status_code == 200
        data = response.json()
        assert data["exhaustive"] is True
        assert data["currency"] == "USD"
        assert [a["sale"] for a in data["assignments"]] == [str(sale.id)]
        assert data["assignments"][0]["delivery_option"] == str(option.id)
        assert not DispatchOrder.objects.exists()

    def test_sales_are_grouped_and_share_the_cost(
        self, user_client, ml, amazon, packed, make_option
    ):
        # Alone, each would take its own channel's agency at 70. Together the
        # common dispatch at 100 is cheaper, 50 each.
        make_option("ML agency", "70", channel=ml, kind="CHANNEL_AGENCY")
        make_option("Amazon agency", "70", channel=amazon, kind="CHANNEL_AGENCY")
        common = make_option("Common dispatch", "100")
        first, second = packed(ml, name="A"), packed(amazon, name="B")

        data = user_client.get(PLAN).json()

        assert (data["total_cost"], data["total_penalty"], data["total"]) == (
            "100.00",
            "0.00",
            "100.00",
        )
        [departure] = data["departures"]
        assert departure["delivery_option"]["id"] == str(common.id)
        assert departure["delivery_option"]["capacity"] is None
        assert sorted(departure["sales"]) == sorted([str(first.id), str(second.id)])
        assert [a["cost_share"] for a in data["assignments"]] == ["50.00", "50.00"]

    def test_a_late_sale_carries_its_channels_penalty(
        self, user_client, ml, packed, make_option
    ):
        packed(ml, due_in_hours=1)
        make_option("Courier", "20", kind="DOOR_TO_DOOR", dispatch_minutes=24 * 60)

        [assignment] = user_client.get(PLAN).json()["assignments"]

        assert assignment["late"] is True
        assert (assignment["cost_share"], assignment["penalty"]) == ("20.00", "100.00")

    def test_a_full_departure_sends_the_rest_to_the_next(
        self, user_client, ml, packed, make_option
    ):
        make_option("Courier", "40", departure_time=time(16, 0), capacity=1)
        packed(ml, name="A", due_in_hours=24 * 7)
        packed(ml, name="B", due_in_hours=24 * 7)

        data = user_client.get(PLAN).json()

        assert [len(d["sales"]) for d in data["departures"]] == [1, 1]
        first, second = (d["departs_at"] for d in data["departures"])
        assert first < second

    def test_a_sale_no_active_option_serves_is_left_unassigned(
        self, user_client, ml, amazon, packed, make_option
    ):
        _agency(make_option, ml, "0")
        make_option("Off", "0", is_active=False)
        stranded = packed(amazon)

        data = user_client.get(PLAN).json()

        assert data["assignments"] == []
        assert [u["sale"] for u in data["unassigned"]] == [str(stranded.id)]
        assert data["unassigned"][0]["channel_name"] == "Amazon"

    def test_only_packed_sales_of_live_orders_are_planned(
        self, user_client, ml, make_order, packed, make_option
    ):
        _agency(make_option, ml)
        services.register_sale(make_order(name="Unpacked"), ml, timezone.now())
        cancelled = packed(ml, name="Cancelled")
        cancelled.order.status = "CANCELLED"
        cancelled.order.save()
        ready = packed(ml, name="Ready")

        data = user_client.get(PLAN).json()

        assert [a["sale"] for a in data["assignments"]] == [str(ready.id)]


class TestCreate:
    def test_packing_a_sale_creates_no_dispatch_order(self, ml, packed, make_option):
        _agency(make_option, ml)
        packed(ml)
        assert not DispatchOrder.objects.exists()

    def test_a_member_creates_the_dispatch_orders_on_demand(
        self, user_client, org_a, ml, packed, make_option
    ):
        org_a.timezone = "America/Chicago"
        org_a.save()
        first, second = packed(ml, name="A"), packed(ml, name="B")
        pickup = make_option(
            "ML pickup",
            "45",
            channel=ml,
            kind="CHANNEL_PICKUP",
            departure_time=time(11, 0),
            capacity=10,
        )

        response = user_client.post(DISPATCH_ORDERS)

        assert response.status_code == 200
        data = response.json()
        assert (data["exhaustive"], data["unassigned"]) == (True, [])
        [order] = data["dispatch_orders"]
        assert (order["delivery_option"], order["status"]) == (str(pickup.id), "OPEN")
        assert (order["cost"], order["capacity"]) == ("45.00", 10)
        assert sorted(i["sale"] for i in order["items"]) == sorted(
            [str(first.id), str(second.id)]
        )
        assert [i["cost_share"] for i in order["items"]] == ["22.50", "22.50"]
        row = DispatchOrder.objects.get()
        local = row.departs_at.astimezone(TZ)
        assert (local.hour, local.minute) == (11, 0)
        sale = user_client.get(f"/api/fulfillment/sales/{first.id}/").json()
        assert sale["dispatch"]["dispatch_order"] == str(row.id)
        assert sale["dispatch"]["status"] == "OPEN"

    def test_recalculating_lets_a_new_sale_join_and_splits_the_cost_again(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml, "45", departure_time=time(16, 0))
        packed(ml, name="A")
        first = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"][0]
        assert [i["cost_share"] for i in first["items"]] == ["45.00"]

        packed(ml, name="B")
        again = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]

        assert [o["id"] for o in again] == [first["id"]]
        assert [i["cost_share"] for i in again[0]["items"]] == ["22.50", "22.50"]

    def test_recalculating_with_nothing_changed_changes_nothing(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml, "45")
        packed(ml, name="A")
        packed(ml, name="B")
        first = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]

        again = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]

        assert [o["id"] for o in again] == [o["id"] for o in first]
        assert [i["id"] for i in again[0]["items"]] == [
            i["id"] for i in first[0]["items"]
        ]
        assert [i["cost_share"] for i in again[0]["items"]] == ["22.50", "22.50"]

    def test_recalculating_uses_the_current_cost(
        self, user_client, ml, packed, make_option
    ):
        option = _agency(make_option, ml, "45")
        packed(ml)
        user_client.post(DISPATCH_ORDERS)
        option.cost = Decimal("60")
        option.save()

        [order] = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]

        assert order["cost"] == "60.00"
        assert order["items"][0]["cost_share"] == "60.00"

    def test_recalculating_moves_sales_to_a_cheaper_option(
        self, user_client, ml, packed, make_option
    ):
        make_option("Common", "100")
        sale = packed(ml)
        user_client.post(DISPATCH_ORDERS)
        cheaper = _agency(make_option, ml, "30")

        [order] = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]

        assert order["delivery_option"] == str(cheaper.id)
        assert DispatchOrder.objects.count() == 1
        assert DispatchOrderItem.objects.get().sale == sale

    def test_a_sale_no_option_serves_any_more_leaves_its_open_order(
        self, user_client, ml, packed, make_option
    ):
        option = _agency(make_option, ml)
        sale = packed(ml)
        user_client.post(DISPATCH_ORDERS)
        option.is_active = False
        option.save()

        data = user_client.post(DISPATCH_ORDERS).json()

        assert data["dispatch_orders"] == []
        assert [u["sale"] for u in data["unassigned"]] == [str(sale.id)]
        assert not DispatchOrder.objects.exists()
        assert not DispatchOrderItem.objects.exists()

    def test_capacity_splits_sales_between_departures(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml, "40", departure_time=time(16, 0), capacity=1)
        packed(ml, name="A", due_in_hours=24 * 7)
        packed(ml, name="B", due_in_hours=24 * 7)

        orders = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]

        assert [len(o["items"]) for o in orders] == [1, 1]
        assert [o["items"][0]["cost_share"] for o in orders] == ["40.00", "40.00"]

    def test_a_cancelled_order_leaves_its_open_dispatch_order(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml)
        gone, kept = packed(ml, name="Gone"), packed(ml, name="Kept")
        user_client.post(DISPATCH_ORDERS)
        gone.order.status = "CANCELLED"
        gone.order.save()

        [order] = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]

        assert [i["sale"] for i in order["items"]] == [str(kept.id)]
        assert order["items"][0]["cost_share"] == "10.00"

    def test_another_orgs_request_touches_nothing_here(
        self, org_b_client, ml, packed, make_option
    ):
        _agency(make_option, ml)
        packed(ml)

        data = org_b_client.post(DISPATCH_ORDERS).json()

        assert (data["dispatch_orders"], data["unassigned"]) == ([], [])
        assert not DispatchOrder.objects.exists()


class TestClose:
    def test_closing_computes_and_fixes_the_actual_figures(
        self, user_client, ml, packed, make_option
    ):
        option = _agency(make_option, ml, "45")
        first = packed(ml, name="A")
        packed(ml, name="B")
        user_client.post(DISPATCH_ORDERS)
        order = DispatchOrder.objects.get()
        option.cost = Decimal("50")
        option.save()

        response = user_client.post(_close(order))

        assert response.status_code == 200
        data = response.json()
        assert (data["status"], data["cost"]) == ("CLOSED", "50.00")
        assert data["closed_at"] is not None
        assert [i["cost_share"] for i in data["items"]] == ["25.00", "25.00"]
        sale = user_client.get(f"/api/fulfillment/sales/{first.id}/").json()
        assert sale["dispatch"]["status"] == "CLOSED"
        assert sale["dispatch"]["cost_share"] == "25.00"

    def test_closing_recomputes_lateness_and_penalty_from_the_current_values(
        self, user_client, ml, packed, make_option
    ):
        option = _agency(make_option, ml, dispatch_minutes=60)
        packed(ml, due_in_hours=2)
        [order] = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]
        assert order["items"][0]["late"] is False
        option.dispatch_minutes = 180
        option.save()
        ml.late_penalty = Decimal("150")
        ml.save()

        closed = user_client.post(_close(DispatchOrder.objects.get())).json()

        assert closed["items"][0]["late"] is True
        assert closed["items"][0]["penalty"] == "150.00"

    def test_an_option_without_a_delivery_time_closes_when_executed(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml, dispatch_minutes=30)
        packed(ml, due_in_hours=48)
        user_client.post(DISPATCH_ORDERS)
        order = DispatchOrder.objects.get()
        before = timezone.now()

        assert user_client.post(_close(order)).status_code == 200

        order.refresh_from_db()
        item = order.items.get()
        assert order.departs_at >= before
        assert item.estimated_delivery_at == order.departs_at + timedelta(minutes=30)

    def test_an_option_with_a_delivery_time_cannot_close_before_it(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml, departure_time=time(11, 0))
        packed(ml, due_in_hours=24 * 7)
        user_client.post(DISPATCH_ORDERS)
        order = DispatchOrder.objects.get()

        response = user_client.post(_close(order))

        assert response.status_code == 409
        assert "once that time has passed" in response.json()["detail"]
        order.refresh_from_db()
        assert order.status == "OPEN"

    def test_an_option_with_a_delivery_time_closes_after_it_at_that_time(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml, departure_time=time(11, 0), dispatch_minutes=60)
        packed(ml, due_in_hours=24 * 7)
        user_client.post(DISPATCH_ORDERS)
        order = DispatchOrder.objects.get()
        left = timezone.now() - timedelta(seconds=1)
        DispatchOrder.objects.filter(pk=order.pk).update(departs_at=left)

        assert user_client.post(_close(order)).status_code == 200

        order.refresh_from_db()
        assert (order.status, order.departs_at) == ("CLOSED", left)
        assert order.items.get().estimated_delivery_at == left + timedelta(hours=1)

    def test_a_closed_order_is_left_alone_by_the_next_recalculation(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml)
        sale = packed(ml, name="A")
        user_client.post(DISPATCH_ORDERS)
        order = DispatchOrder.objects.get()
        user_client.post(_close(order))
        newcomer = packed(ml, name="B")

        [fresh] = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]

        assert fresh["id"] != str(order.id)
        assert [i["sale"] for i in fresh["items"]] == [str(newcomer.id)]
        order.refresh_from_db()
        assert order.status == "CLOSED"
        assert list(order.items.values_list("sale", flat=True)) == [sale.id]
        assert order.items.get().cost_share == Decimal("10.00")

    def test_a_closed_departure_takes_no_new_sales(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml, departure_time=time(11, 0))
        packed(ml, name="A", due_in_hours=24 * 7)
        user_client.post(DISPATCH_ORDERS)
        closed = DispatchOrder.objects.get()
        left = timezone.now() - timedelta(seconds=1)
        DispatchOrder.objects.filter(pk=closed.pk).update(departs_at=left)
        user_client.post(_close(closed))
        packed(ml, name="B", due_in_hours=24 * 7)

        [fresh] = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]

        later = DispatchOrder.objects.get(pk=fresh["id"]).departs_at
        assert later.date() > left.date()

    def test_an_option_without_a_time_runs_once_a_day(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml)
        packed(ml, name="A")
        user_client.post(DISPATCH_ORDERS)
        closed = DispatchOrder.objects.get()
        user_client.post(_close(closed))
        packed(ml, name="B")

        [fresh] = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]

        closed.refresh_from_db()
        tomorrow = closed.departs_at.date() + timedelta(days=1)
        row = DispatchOrder.objects.get(pk=fresh["id"])
        assert (row.departs_at.date(), row.departs_at.hour) == (tomorrow, 0)

    def test_closing_twice_is_refused(self, user_client, ml, packed, make_option):
        _agency(make_option, ml)
        packed(ml)
        user_client.post(DISPATCH_ORDERS)
        order = DispatchOrder.objects.get()
        assert user_client.post(_close(order)).status_code == 200

        again = user_client.post(_close(order))

        assert again.status_code == 409
        assert "already closed" in again.json()["detail"]

    def test_closing_on_an_option_switched_off_is_refused(
        self, user_client, ml, packed, make_option
    ):
        option = _agency(make_option, ml)
        packed(ml)
        user_client.post(DISPATCH_ORDERS)
        option.is_active = False
        option.save()
        order = DispatchOrder.objects.get()

        assert user_client.post(_close(order)).status_code == 409
        order.refresh_from_db()
        assert order.status == "OPEN"

    def test_closing_over_a_capacity_lowered_since_is_refused(
        self, user_client, ml, packed, make_option
    ):
        option = _agency(make_option, ml, capacity=5)
        packed(ml, name="A")
        packed(ml, name="B")
        user_client.post(DISPATCH_ORDERS)
        option.capacity = 1
        option.save()

        response = user_client.post(_close(DispatchOrder.objects.get()))

        assert response.status_code == 409
        assert "takes 1 package(s)" in response.json()["detail"]

    def test_closing_takes_cancelled_sales_off_first(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml, "30")
        gone = packed(ml, name="Gone")
        kept = packed(ml, name="Kept")
        user_client.post(DISPATCH_ORDERS)
        gone.order.status = "CANCELLED"
        gone.order.save()

        data = user_client.post(_close(DispatchOrder.objects.get())).json()

        assert [i["sale"] for i in data["items"]] == [str(kept.id)]
        assert data["items"][0]["cost_share"] == "30.00"

    def test_closing_with_every_sale_cancelled_is_refused(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml)
        sale = packed(ml)
        user_client.post(DISPATCH_ORDERS)
        sale.order.status = "CANCELLED"
        sale.order.save()

        assert user_client.post(_close(DispatchOrder.objects.get())).status_code == 409

    def test_another_org_sees_a_404(self, org_b_client, ml, packed, make_option):
        _agency(make_option, ml)
        packed(ml)
        services.create_dispatch_orders(ml.org)
        order = DispatchOrder.objects.get()

        assert org_b_client.get(_detail(order)).status_code == 404
        assert org_b_client.post(_close(order)).status_code == 404
        order.refresh_from_db()
        assert order.status == "OPEN"


def _manual(client, sales, mode="MANUAL", option=None, **extra):
    body = {"mode": mode, "sales": [str(s.id) for s in sales], **extra}
    if option is not None:
        body["delivery_option"] = str(option.id)
    return client.post(MANUAL, body, format="json")


def _sales_on(order_data):
    return {i["sale"] for i in order_data["items"]}


class TestManual:
    def test_a_person_dispatches_the_sales_they_chose_on_the_option_they_chose(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml, "30")
        courier = make_option("Courier", "80", kind="DOOR_TO_DOOR")
        first, second = packed(ml, name="A"), packed(ml, name="B")
        packed(ml, name="C")

        response = _manual(user_client, [first, second], option=courier)

        assert response.status_code == 201, response.content
        data = response.json()
        assert (data["mode"], data["delivery_option"]) == ("MANUAL", str(courier.id))
        assert _sales_on(data) == {str(first.id), str(second.id)}
        assert [i["cost_share"] for i in data["items"]] == ["40.00", "40.00"]

    def test_without_an_option_the_best_single_run_is_chosen(
        self, user_client, ml, packed, make_option
    ):
        agency = _agency(make_option, ml, "30")
        make_option("Common", "100")
        sale = packed(ml)

        data = _manual(user_client, [sale]).json()

        assert data["delivery_option"] == str(agency.id)

    def test_a_recalculation_leaves_a_manual_orders_sales_and_run_alone(
        self, user_client, ml, packed, make_option
    ):
        courier = make_option("Courier", "80", kind="DOOR_TO_DOOR")
        chosen = packed(ml, name="Chosen")
        manual = _manual(user_client, [chosen], option=courier).json()
        other = packed(ml, name="Other")

        orders = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]

        by_mode = {o["mode"]: o for o in orders}
        assert _sales_on(by_mode["MANUAL"]) == {str(chosen.id)}
        assert _sales_on(by_mode["AUTO"]) == {str(other.id)}
        # The manual order holds today's run, so the plan takes tomorrow's.
        assert by_mode["AUTO"]["departs_at"][:10] > manual["departs_at"][:10]

    def test_a_recalculation_refreshes_a_manual_orders_figures(
        self, user_client, ml, packed, make_option
    ):
        courier = make_option("Courier", "80", kind="DOOR_TO_DOOR")
        chosen = packed(ml)
        _manual(user_client, [chosen], option=courier)
        courier.cost = Decimal("90")
        courier.save()

        [order] = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]

        assert (order["mode"], order["cost"]) == ("MANUAL", "90.00")
        assert order["items"][0]["cost_share"] == "90.00"

    def test_chosen_sales_leave_their_automatic_order(
        self, user_client, ml, packed, make_option
    ):
        agency = _agency(make_option, ml, "30")
        courier = make_option("Courier", "80", kind="DOOR_TO_DOOR")
        moved, stays = packed(ml, name="Moved"), packed(ml, name="Stays")
        user_client.post(DISPATCH_ORDERS)

        _manual(user_client, [moved], option=courier)

        auto = DispatchOrder.objects.get(mode="AUTO", delivery_option=agency)
        assert [item.sale for item in auto.items.all()] == [stays]
        assert auto.items.get().cost_share == Decimal("30.00")

    def test_a_manual_order_takes_over_the_run_and_the_rest_is_planned_again(
        self, user_client, ml, packed, make_option
    ):
        agency = _agency(make_option, ml, "30")
        chosen, left = packed(ml, name="Chosen"), packed(ml, name="Left")
        user_client.post(DISPATCH_ORDERS)
        held = DispatchOrder.objects.get()

        data = _manual(user_client, [chosen], option=agency).json()

        assert _sales_on(data) == {str(chosen.id)}
        assert not DispatchOrder.objects.filter(pk=held.pk).exists()
        # The sale it displaced is planned again at once: today's run is the
        # manual order's now, so it takes tomorrow's.
        replanned = DispatchOrderItem.objects.get(sale=left).dispatch_order
        assert replanned.mode == "AUTO"
        assert replanned.departs_at.date() > held.departs_at.date()

    def test_hybrid_fills_the_rest_of_the_run(
        self, user_client, ml, packed, make_option
    ):
        pickup = make_option(
            "Pickup", "90", channel=ml, kind="CHANNEL_PICKUP", capacity=3
        )
        chosen = packed(ml, name="Chosen")
        for name in ("B", "C", "D"):
            packed(ml, name=name)

        data = _manual(user_client, [chosen], mode="HYBRID", option=pickup).json()

        assert data["mode"] == "HYBRID"
        assert len(data["items"]) == 3
        assert str(chosen.id) in _sales_on(data)
        assert [i["cost_share"] for i in data["items"]] == ["30.00"] * 3

    def test_hybrid_only_adds_sales_the_run_is_best_for(
        self, user_client, ml, packed, make_option
    ):
        # The slow pickup would make the urgent sale late (100), while the
        # agency gets it there for 10: the plan leaves it off the pickup.
        pickup = make_option(
            "Pickup", "50", channel=ml, kind="CHANNEL_PICKUP", dispatch_minutes=48 * 60
        )
        _agency(make_option, ml, "10")
        chosen = packed(ml, name="Chosen", due_in_hours=24 * 7)
        relaxed = packed(ml, name="Relaxed", due_in_hours=24 * 7)
        packed(ml, name="Urgent", due_in_hours=2)

        data = _manual(user_client, [chosen], mode="HYBRID", option=pickup).json()

        assert _sales_on(data) == {str(chosen.id), str(relaxed.id)}

    def test_hybrid_on_a_full_run_adds_nobody(
        self, user_client, ml, packed, make_option
    ):
        pickup = make_option(
            "Pickup", "20", channel=ml, kind="CHANNEL_PICKUP", capacity=1
        )
        chosen = packed(ml, name="Chosen")
        packed(ml, name="Other")

        data = _manual(user_client, [chosen], mode="HYBRID", option=pickup).json()

        assert _sales_on(data) == {str(chosen.id)}

    def test_an_automatic_order_left_empty_is_removed(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml, "30")
        courier = make_option("Courier", "80", kind="DOOR_TO_DOOR")
        alone = packed(ml)
        user_client.post(DISPATCH_ORDERS)

        _manual(user_client, [alone], option=courier)

        assert list(DispatchOrder.objects.values_list("mode", flat=True)) == ["MANUAL"]

    def test_a_manual_order_whose_sales_were_cancelled_goes_at_the_next_recalculation(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml)
        sale = packed(ml)
        _manual(user_client, [sale])
        sale.order.status = "CANCELLED"
        sale.order.save()

        assert user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"] == []
        assert not DispatchOrder.objects.exists()

    def test_a_sale_that_is_not_ready_is_refused(
        self, user_client, ml, make_order, packed, make_option
    ):
        _agency(make_option, ml)
        unpacked = services.register_sale(
            make_order(name="Unpacked"), ml, timezone.now()
        )
        response = _manual(user_client, [packed(ml), unpacked])
        assert response.status_code == 409
        assert "Unpacked" in response.json()["detail"]
        assert not DispatchOrder.objects.exists()

    def test_a_sale_on_another_manual_order_is_refused(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml)
        sale = packed(ml)
        assert _manual(user_client, [sale]).status_code == 201
        assert _manual(user_client, [sale]).status_code == 409
        assert DispatchOrder.objects.count() == 1

    def test_an_option_that_cannot_take_them_all_on_one_run_is_refused(
        self, user_client, ml, amazon, packed, make_option
    ):
        ml_agency = _agency(make_option, ml)
        single_seat = make_option("Courier", "10", kind="DOOR_TO_DOOR", capacity=1)
        two = [packed(ml, name="A"), packed(ml, name="B")]
        mixed = [packed(ml, name="C"), packed(amazon, name="D")]

        too_many = _manual(user_client, two, option=single_seat)
        wrong_channel = _manual(user_client, mixed, option=ml_agency)

        assert too_many.status_code == wrong_channel.status_code == 409
        assert "one run" in too_many.json()["detail"]
        assert not DispatchOrder.objects.exists()

    def test_an_option_switched_off_is_refused(
        self, user_client, ml, packed, make_option
    ):
        courier = make_option("Courier", "10", kind="DOOR_TO_DOOR", is_active=False)
        response = _manual(user_client, [packed(ml)], option=courier)
        assert response.status_code == 409
        assert "switched off" in response.json()["detail"]

    @pytest.mark.parametrize(
        "change",
        ["no-sales", "sale-twice", "unknown-mode", "another-orgs-sale"],
    )
    def test_malformed_requests_are_refused(
        self, user_client, org_b, make_order, ml, packed, make_option, change
    ):
        _agency(make_option, ml)
        sale = packed(ml)
        body = {"mode": "MANUAL", "sales": [str(sale.id)]}
        if change == "no-sales":
            body["sales"] = []
        elif change == "sale-twice":
            body["sales"] = [str(sale.id)] * 2
        elif change == "unknown-mode":
            body["mode"] = "AUTO"
        else:
            theirs_channel = SalesChannel.objects.create(org=org_b, name="Theirs")
            theirs = services.register_sale(
                make_order(org=org_b), theirs_channel, timezone.now()
            )
            body["sales"] = [str(theirs.id)]

        response = user_client.post(MANUAL, body, format="json")

        assert response.status_code == 400
        assert not DispatchOrder.objects.exists()


class TestRelease:
    def test_releasing_an_open_order_makes_its_sales_ready_again(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml)
        sale = packed(ml)
        order = DispatchOrder.objects.get(pk=_manual(user_client, [sale]).json()["id"])

        assert user_client.delete(_detail(order)).status_code == 204

        assert not DispatchOrder.objects.exists()
        assert [a["sale"] for a in user_client.get(PLAN).json()["assignments"]] == [
            str(sale.id)
        ]

    def test_a_closed_order_cannot_be_released(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml)
        packed(ml)
        user_client.post(DISPATCH_ORDERS)
        order = DispatchOrder.objects.get()
        user_client.post(_close(order))

        response = user_client.delete(_detail(order))

        assert response.status_code == 409
        assert DispatchOrder.objects.filter(pk=order.pk, status="CLOSED").exists()

    def test_another_org_sees_a_404(self, org_b_client, ml, packed, make_option):
        _agency(make_option, ml)
        packed(ml)
        services.create_dispatch_orders(ml.org)
        order = DispatchOrder.objects.get()
        assert org_b_client.delete(_detail(order)).status_code == 404
        assert DispatchOrder.objects.filter(pk=order.pk).exists()


def _free(option, *offsets):
    """Mark today plus each offset (days) free on ``option``."""
    today = timezone.now().date()
    for offset in offsets:
        DeliveryOptionDay.objects.create(
            delivery_option=option, date=today + timedelta(days=offset), kind="FREE"
        )
    return today + timedelta(days=max(offsets) + 1)


class TestWorkdays:
    def test_runs_leave_on_the_options_working_days_only(
        self, user_client, ml, packed, make_option
    ):
        option = _agency(make_option, ml, departure_time=time(11, 0))
        first_working = _free(option, 0, 1)
        packed(ml, due_in_hours=24 * 7)

        [assignment] = user_client.get(PLAN).json()["assignments"]

        assert assignment["departs_at"] == f"{first_working}T11:00:00Z"

    def test_a_run_with_no_time_leaves_when_the_next_working_day_opens(
        self, user_client, ml, packed, make_option
    ):
        opens = {f"{day}_opens": time(9) for day in WEEK}
        option = _agency(make_option, ml, **opens)
        first_working = _free(option, 0, 1)
        packed(ml, due_in_hours=24 * 7)

        [order] = user_client.post(DISPATCH_ORDERS).json()["dispatch_orders"]

        assert order["departs_at"] == f"{first_working}T09:00:00Z"

    def test_a_request_may_allow_non_working_days(
        self, user_client, ml, packed, make_option
    ):
        option = _agency(make_option, ml)
        _free(option, 0, 1)
        first, second = packed(ml, name="A"), packed(ml, name="B")
        today = str(timezone.now().date())

        preview = user_client.get(PLAN, {"allow_non_working_days": "true"}).json()
        manual = _manual(user_client, [first], allow_non_working_days=True).json()
        auto = user_client.post(
            DISPATCH_ORDERS, {"allow_non_working_days": True}, format="json"
        ).json()

        assert preview["assignments"][0]["departs_at"][:10] == today
        assert manual["departs_at"][:10] == today
        # The manual order holds today's run; the plan takes tomorrow's.
        [tomorrow] = [o for o in auto["dispatch_orders"] if o["mode"] == "AUTO"]
        assert _sales_on(tomorrow) == {str(second.id)}

    def test_an_extra_working_day_opens_a_day_off(
        self, user_client, ml, packed, make_option
    ):
        closed = {f"{day}_opens": None for day in WEEK}
        option = _agency(make_option, ml, **closed)
        tomorrow = timezone.now().date() + timedelta(days=1)
        DeliveryOptionDay.objects.create(
            delivery_option=option, date=tomorrow, kind="WORKING", opens_at=time(10)
        )
        packed(ml, due_in_hours=24 * 7)

        [assignment] = user_client.get(PLAN).json()["assignments"]

        assert assignment["departs_at"] == f"{tomorrow}T10:00:00Z"

    def test_each_option_keeps_to_its_own_days(
        self, user_client, ml, packed, make_option
    ):
        resting = _agency(make_option, ml, "10")
        _free(resting, 0, 1)
        working = make_option("Courier", "10", kind="DOOR_TO_DOOR")
        packed(ml, due_in_hours=6)

        [assignment] = user_client.get(PLAN).json()["assignments"]

        assert assignment["delivery_option"] == str(working.id)

    def test_a_malformed_flag_is_refused(self, user_client):
        preview = user_client.get(PLAN, {"allow_non_working_days": "maybe"})
        body = {"allow_non_working_days": "maybe"}
        recalculation = user_client.post(DISPATCH_ORDERS, body, format="json")
        assert preview.status_code == recalculation.status_code == 400
        assert "allow_non_working_days" in recalculation.json()["errors"]


class TestRead:
    def test_a_member_lists_them_filtered_by_status_and_reads_one(
        self, user_client, ml, packed, make_option
    ):
        _agency(make_option, ml)
        packed(ml, name="A")
        user_client.post(DISPATCH_ORDERS)
        closed = DispatchOrder.objects.get()
        user_client.post(_close(closed))
        packed(ml, name="B")
        user_client.post(DISPATCH_ORDERS)
        open_ = DispatchOrder.objects.get(status="OPEN")

        only_closed = user_client.get(DISPATCH_ORDERS, {"status": "CLOSED"}).json()
        both = user_client.get(
            f"{DISPATCH_ORDERS}?status=OPEN&status=CLOSED&status=NOPE"
        ).json()
        one = user_client.get(_detail(open_))

        assert [o["id"] for o in only_closed["dispatch_orders"]] == [str(closed.id)]
        assert both["count"] == 2
        assert one.status_code == 200
        assert one.json()["items"][0]["order_name"] == "B"

    def test_another_org_lists_nothing(self, org_b_client, ml, packed, make_option):
        _agency(make_option, ml)
        packed(ml)
        services.create_dispatch_orders(ml.org)
        assert org_b_client.get(DISPATCH_ORDERS).json()["dispatch_orders"] == []


class TestAccess:
    ROUTES = [
        ("get", "/api/fulfillment/channels/"),
        ("post", "/api/fulfillment/channels/"),
        ("get", "/api/fulfillment/delivery-options/"),
        ("post", "/api/fulfillment/delivery-options/"),
        ("get", "/api/fulfillment/sales/"),
        ("post", "/api/fulfillment/sales/"),
        ("get", PLAN),
        ("get", DISPATCH_ORDERS),
        ("post", DISPATCH_ORDERS),
        ("post", MANUAL),
    ]

    @pytest.mark.parametrize(
        "method,url",
        ROUTES,
        ids=[f"{m}-{'-'.join(u.split('/')[3:-1])}" for m, u in ROUTES],
    )
    def test_anonymous_is_refused(self, unauthenticated_client, method, url):
        response = getattr(unauthenticated_client, method)(url)
        assert response.status_code in (401, 403)

    def test_anonymous_cannot_close_or_complete(
        self, unauthenticated_client, ml, packed, make_option
    ):
        _agency(make_option, ml)
        sale = packed(ml)
        services.create_dispatch_orders(ml.org)
        order = DispatchOrder.objects.get()
        for url in (
            _close(order),
            f"/api/fulfillment/packaging-orders/{sale.packaging_order.id}/complete/",
        ):
            assert unauthenticated_client.post(url).status_code in (401, 403)
        order.refresh_from_db()
        assert order.status == "OPEN"


class TestTokens:
    def test_a_read_token_reads_but_cannot_create_or_close(self, org_a, admin_profile):
        SalesChannel.objects.create(org=org_a, name="Store")
        raw, _ = PersonalAccessToken.generate(
            profile=admin_profile, name="reader", scopes=["fulfillment:read"]
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

        assert client.get(PLAN).status_code == 200
        assert client.get(DISPATCH_ORDERS).status_code == 200
        assert client.post(DISPATCH_ORDERS).status_code == 403

    def test_another_resources_token_cannot_read(self, admin_profile):
        raw, _ = PersonalAccessToken.generate(
            profile=admin_profile, name="leads", scopes=["leads:read"]
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        assert client.get(PLAN).status_code == 403
