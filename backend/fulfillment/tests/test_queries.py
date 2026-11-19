"""The lists and the plan cost the same queries for one row as for several."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from fulfillment import services

# Every member here is on the store's team, which handles every order type;
# what a member without it may do is in test_permissions.py.
pytestmark = pytest.mark.usefixtures("crew")


def _queries(client, url):
    with CaptureQueriesContext(connection) as captured:
        assert client.get(url).status_code == 200
    return len(captured)


def _grow(make_order, channel, make_option, count):
    for i in range(count):
        make_option(f"Option {i}", "10", channel=channel, kind="CHANNEL_AGENCY")
        sale = services.register_sale(
            make_order(name=f"Venta {i}"),
            channel,
            timezone.now(),
            [{"description": "Sew"}],
        )
        services.complete_preparation(sale.preparation_orders.get())
        services.complete_packaging(sale.packaging_order)
        services.register_sale(
            make_order(name=f"Waiting {i}"),
            channel,
            timezone.now(),
            [{"description": "Sew"}],
        )
    services.create_dispatch_orders(channel.org)


def test_no_query_per_row(user_client, make_order, ml, make_option):
    urls = [
        "/api/fulfillment/sales/",
        "/api/fulfillment/queues/",
        "/api/fulfillment/queues/preparation/",
        "/api/fulfillment/queues/delivery/",
        "/api/fulfillment/delivery-options/",
        "/api/fulfillment/dispatch-orders/",
        "/api/fulfillment/dispatch-plan/",
    ]
    _grow(make_order, ml, make_option, 1)
    one = [_queries(user_client, url) for url in urls]
    _grow(make_order, ml, make_option, 3)
    four = [_queries(user_client, url) for url in urls]
    assert four == one
