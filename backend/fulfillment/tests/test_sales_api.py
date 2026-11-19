"""Registering a sale, and working it through preparation and packaging until
its dispatch order exists."""

from datetime import timedelta

import pytest
from django.utils import timezone

from fulfillment import services
from fulfillment.models import (
    DispatchOrder,
    PackagingOrder,
    PreparationOrder,
    Sale,
)
from orders.models import OrderLineItem

# Every member here is on the store's team, which handles every order type;
# what a member without it may do is in test_permissions.py.
pytestmark = pytest.mark.usefixtures("crew")

SALES = "/api/fulfillment/sales/"


def _sale(sale):
    return f"{SALES}{sale.id}/"


def _complete_preparation(preparation):
    return f"/api/fulfillment/preparation-orders/{preparation.id}/complete/"


def _complete_packaging(packaging):
    return f"/api/fulfillment/packaging-orders/{packaging.id}/complete/"


def _body(order, channel, **extra):
    due = (timezone.now() + timedelta(days=2)).isoformat()
    return {
        "order": str(order.id),
        "channel": str(channel.id),
        "committed_delivery_at": due,
        **extra,
    }


class TestRegister:
    def test_an_admin_registers_a_sale_with_its_preparations(
        self, admin_client, make_order, ml, org_a
    ):
        order = make_order()
        dress = OrderLineItem.objects.create(order=order, name="Dress", org=org_a)
        body = _body(
            order,
            ml,
            preparations=[
                {"description": "Sew the hem", "line_item": str(dress.id)},
                {"description": "Print the name"},
            ],
        )
        response = admin_client.post(SALES, body, format="json")

        assert response.status_code == 201, response.content
        data = response.json()
        assert data["order"] == str(order.id)
        assert (data["channel_name"], data["order_status"]) == (
            "Mercado Libre",
            "ACTIVATED",
        )
        assert data["packaging_order"]["status"] == "PENDING"
        assert data["dispatch"] is None
        assert [p["description"] for p in data["preparation_orders"]] == [
            "Sew the hem",
            "Print the name",
        ]
        assert data["preparation_orders"][0]["line_item"] == str(dress.id)
        sale = Sale.objects.get(order=order)
        assert sale.org == org_a
        assert PackagingOrder.objects.filter(sale=sale, org=org_a).exists()

    def test_the_channel_sync_registers_sales_as_an_app_member(
        self, app_client, make_order, ml
    ):
        response = app_client.post(SALES, _body(make_order(), ml), format="json")
        assert response.status_code == 201, response.content

    def test_a_member_cannot_register_one(self, user_client, make_order, ml):
        response = user_client.post(SALES, _body(make_order(), ml), format="json")
        assert response.status_code == 403
        assert not Sale.objects.exists()

    def test_another_orgs_order_or_channel_cannot_be_named(
        self, admin_client, make_order, ml, org_b
    ):
        from fulfillment.models import SalesChannel

        theirs = make_order(org=org_b)
        their_channel = SalesChannel.objects.create(org=org_b, name="Theirs")
        first = admin_client.post(SALES, _body(theirs, ml), format="json")
        second = admin_client.post(
            SALES, _body(make_order(), their_channel), format="json"
        )
        assert first.status_code == 400
        assert "order" in first.json()["errors"]
        assert second.status_code == 400
        assert "channel" in second.json()["errors"]
        assert not Sale.objects.exists()

    def test_a_cancelled_order_is_not_registered(self, admin_client, make_order, ml):
        order = make_order(status="CANCELLED")
        response = admin_client.post(SALES, _body(order, ml), format="json")
        assert response.status_code == 400
        assert "order" in response.json()["errors"]

    def test_an_order_has_one_sale(self, admin_client, make_order, ml):
        order = make_order()
        assert (
            admin_client.post(SALES, _body(order, ml), format="json").status_code == 201
        )
        again = admin_client.post(SALES, _body(order, ml), format="json")
        assert again.status_code == 400
        assert Sale.objects.count() == 1

    def test_a_line_item_from_another_order_is_refused(
        self, admin_client, make_order, ml, org_a
    ):
        other = OrderLineItem.objects.create(
            order=make_order(name="Other"), name="Hat", org=org_a
        )
        body = _body(
            make_order(),
            ml,
            preparations=[
                {"description": "Fine"},
                {"description": "Sew", "line_item": str(other.id)},
            ],
        )
        response = admin_client.post(SALES, body, format="json")
        assert response.status_code == 400
        assert "1" in response.json()["errors"]["preparations"]
        assert not Sale.objects.exists()

    @pytest.mark.parametrize(
        "extra",
        [
            {"committed_delivery_at": "tomorrow"},
            {"preparations": [{"description": ""}]},
            {"preparations": [{"description": "Sew", "line_item": "nope"}]},
            {"preparations": "Sew"},
        ],
        ids=[
            "date-not-a-date",
            "preparation-without-description",
            "line-item-not-an-id",
            "preparations-not-a-list",
        ],
    )
    def test_malformed_bodies_are_refused(self, admin_client, make_order, ml, extra):
        body = {**_body(make_order(), ml), **extra}
        response = admin_client.post(SALES, body, format="json")
        assert response.status_code == 400
        assert not Sale.objects.exists()


class TestRead:
    def test_a_member_lists_sales_newest_first_a_page_at_a_time(
        self, user_client, make_order, ml
    ):
        for name in ("First", "Second", "Third"):
            services.register_sale(make_order(name=name), ml, timezone.now())
        response = user_client.get(SALES, {"limit": 2})
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3
        assert [s["order_name"] for s in data["sales"]] == ["Third", "Second"]
        rest = user_client.get(SALES, {"limit": 2, "offset": 2}).json()
        assert [s["order_name"] for s in rest["sales"]] == ["First"]

    def test_a_malformed_page_falls_back_to_the_default(
        self, user_client, make_order, ml
    ):
        services.register_sale(make_order(), ml, timezone.now())
        response = user_client.get(SALES, {"limit": "lots", "offset": "-3"})
        assert response.status_code == 200
        assert (len(response.json()["sales"]), response.json()["count"]) == (1, 1)

    def test_a_packed_sale_waits_for_a_dispatch_order_to_be_created(
        self, user_client, ml, packed
    ):
        sale = packed(ml)
        response = user_client.get(_sale(sale))
        assert response.status_code == 200
        data = response.json()
        assert data["packaging_order"]["status"] == "COMPLETED"
        assert data["packaging_order"]["completed_at"] is not None
        assert data["dispatch"] is None

    def test_another_org_sees_a_404_and_an_empty_list(
        self, org_b_client, make_order, ml
    ):
        sale = services.register_sale(make_order(), ml, timezone.now())
        assert org_b_client.get(_sale(sale)).status_code == 404
        assert org_b_client.get(SALES).json() == {
            "sales": [],
            "count": 0,
            "limit": 50,
            "offset": 0,
        }


class TestWorkOrders:
    @pytest.fixture
    def sale(self, make_order, ml):
        return services.register_sale(
            make_order(),
            ml,
            timezone.now() + timedelta(days=1),
            [{"description": "Sew"}, {"description": "Print"}],
        )

    def test_packing_waits_for_every_preparation(self, user_client, sale):
        sew, print_ = sale.preparation_orders.order_by("created_at")
        packaging = sale.packaging_order

        refused = user_client.post(_complete_packaging(packaging))
        assert refused.status_code == 409
        assert "2 preparation order(s)" in refused.json()["detail"]

        done = user_client.post(_complete_preparation(sew))
        assert done.status_code == 200
        statuses = [p["status"] for p in done.json()["preparation_orders"]]
        assert statuses == ["COMPLETED", "PENDING"]
        assert user_client.post(_complete_packaging(packaging)).status_code == 409

        user_client.post(_complete_preparation(print_))
        packed = user_client.post(_complete_packaging(packaging))
        assert packed.status_code == 200
        data = packed.json()
        assert data["packaging_order"]["status"] == "COMPLETED"
        assert data["dispatch"] is None
        assert not DispatchOrder.objects.exists()

    def test_a_sale_with_nothing_to_prepare_is_packed_at_once(
        self, user_client, make_order, ml
    ):
        sale = services.register_sale(make_order(), ml, timezone.now())
        response = user_client.post(_complete_packaging(sale.packaging_order))
        assert response.status_code == 200
        assert response.json()["packaging_order"]["completed_at"] is not None

    def test_completing_twice_is_refused(self, user_client, make_order, ml):
        sale = services.register_sale(
            make_order(), ml, timezone.now(), [{"description": "Sew"}]
        )
        for url in (
            _complete_preparation(sale.preparation_orders.get()),
            _complete_packaging(sale.packaging_order),
        ):
            assert user_client.post(url).status_code == 200
            again = user_client.post(url)
            assert again.status_code == 409
            assert "already completed" in again.json()["detail"]

    def test_a_cancelled_order_is_not_packed(self, user_client, make_order, ml):
        order = make_order()
        sale = services.register_sale(order, ml, timezone.now())
        order.status = "CANCELLED"
        order.save()
        assert (
            user_client.post(_complete_packaging(sale.packaging_order)).status_code
            == 409
        )
        sale.packaging_order.refresh_from_db()
        assert sale.packaging_order.status == "PENDING"

    def test_another_org_sees_a_404(self, org_b_client, sale):
        preparation = sale.preparation_orders.first()
        for url in (
            _complete_preparation(preparation),
            _complete_packaging(sale.packaging_order),
        ):
            assert org_b_client.post(url).status_code == 404
        preparation.refresh_from_db()
        assert preparation.status == "PENDING"
        assert PreparationOrder.objects.filter(status="COMPLETED").count() == 0
