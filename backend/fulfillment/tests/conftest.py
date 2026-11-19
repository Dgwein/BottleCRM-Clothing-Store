from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Account
from common.app_members import create_app_member
from common.models import PersonalAccessToken, Teams
from fulfillment import services
from fulfillment.models import DeliveryOption, QueueAssignment, SalesChannel
from orders.models import Order


@pytest.fixture
def make_order(org_a):
    account = Account.objects.create(name="Buyer", org=org_a)

    def make(name="Sale 1001", status="ACTIVATED", org=org_a):
        owner = account if org == org_a else Account.objects.create(name="B", org=org)
        return Order.objects.create(name=name, account=owner, status=status, org=org)

    return make


@pytest.fixture
def ml(org_a):
    return SalesChannel.objects.create(
        org=org_a, name="Mercado Libre", late_penalty=Decimal("100.00")
    )


@pytest.fixture
def amazon(org_a):
    return SalesChannel.objects.create(
        org=org_a, name="Amazon", late_penalty=Decimal("80.00")
    )


@pytest.fixture
def make_option(org_a):
    def make(name, cost, channel=None, kind="COMMON_DISPATCH", **fields):
        return DeliveryOption.objects.create(
            org=org_a,
            name=name,
            cost=Decimal(cost),
            channel=channel,
            kind=kind,
            **fields,
        )

    return make


@pytest.fixture
def packed(make_order):
    """A sale already packed, so it is ready to dispatch."""

    def make(channel, due_in_hours=48, name="Venta"):
        sale = services.register_sale(
            make_order(name=name),
            channel,
            timezone.now() + timedelta(hours=due_in_hours),
        )
        services.complete_packaging(sale.packaging_order)
        return sale

    return make


@pytest.fixture
def app_client(org_a):
    """The channel sync, acting as an app member through its token."""
    member = create_app_member(org_a, "Channel sync")
    raw, _ = PersonalAccessToken.generate(profile=member, name="sync")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


@pytest.fixture
def grant(org_a):
    """Put ``profile`` on a new team holding ``levels`` ({order type: level})."""

    def give(profile, name="Team", **levels):
        team = Teams.objects.create(name=name, description="", org=profile.org)
        team.users.add(profile)
        for order_type, level in levels.items():
            QueueAssignment.objects.create(
                team=team, order_type=order_type, access=level
            )
        return team

    return give


@pytest.fixture
def crew(grant, user_profile):
    """The member handles preparation, packaging and delivery, as a store's one
    team would. Inspection is a returns team's, handled elsewhere."""
    return grant(
        user_profile,
        "Store crew",
        PREPARATION="HANDLE",
        PACKAGING="HANDLE",
        DELIVERY="CLOSE",
    )
