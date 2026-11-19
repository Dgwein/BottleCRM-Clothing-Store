"""Channels and delivery options: every member reads them, only a person who
is an org admin changes them. Each permission test asserts both directions."""

from datetime import time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from business_hours.models import BusinessCalendar, BusinessHoliday
from fulfillment import services
from fulfillment.models import (
    DeliveryOption,
    DeliveryOptionDay,
    DispatchOrder,
    SalesChannel,
)

CHANNELS = "/api/fulfillment/channels/"
WEEK = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
OPTIONS = "/api/fulfillment/delivery-options/"


def _channel(channel):
    return f"{CHANNELS}{channel.id}/"


def _option(option):
    return f"{OPTIONS}{option.id}/"


class TestChannels:
    def test_an_admin_creates_a_channel(self, admin_client, org_a):
        response = admin_client.post(
            CHANNELS, {"name": " Amazon ", "late_penalty": "80.50"}, format="json"
        )
        assert response.status_code == 201, response.content
        assert response.json()["name"] == "Amazon"
        assert response.json()["late_penalty"] == "80.50"
        assert SalesChannel.objects.get(org=org_a).late_penalty == Decimal("80.50")

    def test_a_channel_may_charge_no_penalty(self, admin_client):
        response = admin_client.post(
            CHANNELS, {"name": "Store", "late_penalty": "0"}, format="json"
        )
        assert response.status_code == 201
        assert response.json()["late_penalty"] == "0.00"

    def test_a_member_cannot_create_one(self, user_client):
        response = user_client.post(CHANNELS, {"name": "Amazon"}, format="json")
        assert response.status_code == 403
        assert not SalesChannel.objects.exists()

    def test_an_app_member_cannot_create_one(self, app_client):
        response = app_client.post(CHANNELS, {"name": "Amazon"}, format="json")
        assert response.status_code == 403

    def test_a_member_lists_channels(self, user_client, ml, amazon):
        response = user_client.get(CHANNELS)
        assert response.status_code == 200
        assert [c["name"] for c in response.json()["channels"]] == [
            "Amazon",
            "Mercado Libre",
        ]

    @pytest.mark.parametrize(
        "body",
        [
            {"name": "mercado libre"},
            {"name": "   "},
            {"name": "X", "late_penalty": "-1"},
            {"name": "X", "late_penalty": "abc"},
        ],
        ids=[
            "name-taken-ignoring-case",
            "name-blank",
            "penalty-negative",
            "penalty-not-a-number",
        ],
    )
    def test_a_taken_name_blank_name_or_bad_penalty_is_refused(
        self, admin_client, ml, body
    ):
        response = admin_client.post(CHANNELS, body, format="json")
        assert response.status_code == 400
        assert response.json()["error"] is True
        assert SalesChannel.objects.count() == 1

    def test_another_orgs_channel_name_is_free(self, admin_client, org_b):
        SalesChannel.objects.create(org=org_b, name="Amazon")
        response = admin_client.post(CHANNELS, {"name": "Amazon"}, format="json")
        assert response.status_code == 201

    def test_an_admin_changes_the_penalty(self, admin_client, ml):
        response = admin_client.patch(
            _channel(ml), {"late_penalty": "150"}, format="json"
        )
        assert response.status_code == 200
        ml.refresh_from_db()
        assert ml.late_penalty == Decimal("150")

    def test_renaming_to_its_own_name_is_not_a_clash(self, admin_client, ml):
        response = admin_client.patch(
            _channel(ml), {"name": "Mercado Libre"}, format="json"
        )
        assert response.status_code == 200

    def test_a_member_cannot_change_or_delete_one(self, user_client, ml):
        assert (
            user_client.patch(_channel(ml), {"name": "X"}, format="json").status_code
            == 403
        )
        assert user_client.delete(_channel(ml)).status_code == 403
        ml.refresh_from_db()
        assert ml.name == "Mercado Libre"

    def test_an_unused_channel_is_deleted(self, admin_client, ml):
        assert admin_client.delete(_channel(ml)).status_code == 204
        assert not SalesChannel.objects.exists()

    def test_a_channel_with_sales_is_not_deleted(self, admin_client, ml, packed):
        packed(ml)
        response = admin_client.delete(_channel(ml))
        assert response.status_code == 409
        assert SalesChannel.objects.filter(pk=ml.pk).exists()

    def test_another_org_sees_a_404(self, org_b_client, ml):
        assert org_b_client.get(_channel(ml)).status_code == 404
        assert (
            org_b_client.patch(_channel(ml), {"name": "X"}, format="json").status_code
            == 404
        )
        assert org_b_client.delete(_channel(ml)).status_code == 404
        assert org_b_client.get(CHANNELS).json()["channels"] == []

    def test_a_member_reads_one(self, user_client, ml):
        response = user_client.get(_channel(ml))
        assert response.status_code == 200
        assert response.json()["late_penalty"] == "100.00"

    def test_anonymous_is_refused(self, unauthenticated_client):
        assert unauthenticated_client.get(CHANNELS).status_code in (401, 403)


class TestDeliveryOptions:
    def test_an_admin_creates_a_weekly_pickup(self, admin_client, ml):
        body = {
            "name": "ML pickup",
            "kind": "CHANNEL_PICKUP",
            "channel": str(ml.id),
            "location": "Store, back door",
            "weekday": "TUE",
            "departure_time": "11:00",
            "dispatch_minutes": 0,
            "cost": "0",
        }
        response = admin_client.post(OPTIONS, body, format="json")
        assert response.status_code == 201, response.content
        data = response.json()
        assert data["channel_name"] == "Mercado Libre"
        assert (data["weekday"], data["departure_time"]) == ("TUE", "11:00:00")
        assert data["is_active"] is True

    def test_an_anytime_door_to_door_option_needs_no_channel(self, admin_client):
        body = {
            "name": "Courier",
            "kind": "DOOR_TO_DOOR",
            "dispatch_minutes": 240,
            "cost": "120",
            "capacity": 15,
        }
        response = admin_client.post(OPTIONS, body, format="json")
        assert response.status_code == 201, response.content
        data = response.json()
        assert data["capacity"] == 15
        assert (data["channel"], data["channel_name"]) == (None, None)
        # Without an import the option works every day from midnight.
        assert {data[f"{day}_opens"] for day in WEEK} == {"00:00:00"}
        assert (data["weekday"], data["departure_time"]) == ("", None)

    def test_a_channel_kind_without_its_channel_is_refused(self, admin_client):
        body = {"name": "Agency", "kind": "CHANNEL_AGENCY", "cost": "0"}
        response = admin_client.post(OPTIONS, body, format="json")
        assert response.status_code == 400
        assert "channel" in response.json()["errors"]

    def test_changing_the_kind_to_a_channel_kind_needs_a_channel(
        self, admin_client, make_option
    ):
        option = make_option("Common", 50)
        response = admin_client.patch(
            _option(option), {"kind": "CHANNEL_PICKUP"}, format="json"
        )
        assert response.status_code == 400
        option.refresh_from_db()
        assert option.kind == "COMMON_DISPATCH"

    def test_another_orgs_channel_cannot_be_named(self, admin_client, org_b):
        theirs = SalesChannel.objects.create(org=org_b, name="Theirs")
        body = {
            "name": "Pickup",
            "kind": "CHANNEL_PICKUP",
            "channel": str(theirs.id),
            "cost": "0",
        }
        response = admin_client.post(OPTIONS, body, format="json")
        assert response.status_code == 400
        assert "channel" in response.json()["errors"]
        assert not DeliveryOption.objects.exists()

    @pytest.mark.parametrize(
        "fields",
        [
            {"weekday": "MONDAY"},
            {"departure_time": "25:00"},
            {"dispatch_minutes": -1},
            {"dispatch_minutes": 60 * 24 * 61},
            {"cost": "-5"},
            {"capacity": 0},
            {"kind": "TELEPORT"},
            {"monday_opens": "9am"},
        ],
        ids=[
            "weekday-unknown",
            "time-invalid",
            "minutes-negative",
            "minutes-over-60-days",
            "cost-negative",
            "capacity-zero",
            "kind-unknown",
            "opening-time-invalid",
        ],
    )
    def test_malformed_fields_are_refused(self, admin_client, fields):
        body = {"name": "X", "kind": "DOOR_TO_DOOR", "cost": "1", **fields}
        response = admin_client.post(OPTIONS, body, format="json")
        assert response.status_code == 400
        assert set(fields) <= set(response.json()["errors"])

    def test_a_member_reads_but_cannot_write(self, user_client, make_option):
        option = make_option("Common", 50)
        assert (
            user_client.get(OPTIONS).json()["delivery_options"][0]["name"] == "Common"
        )
        assert user_client.get(_option(option)).status_code == 200
        assert (
            user_client.post(OPTIONS, {"name": "X"}, format="json").status_code == 403
        )
        patch = user_client.patch(_option(option), {"is_active": False}, format="json")
        assert patch.status_code == 403
        assert user_client.delete(_option(option)).status_code == 403
        option.refresh_from_db()
        assert option.is_active is True

    def test_an_admin_switches_one_off(self, admin_client, make_option):
        option = make_option("Common", 50)
        response = admin_client.patch(
            _option(option), {"is_active": False}, format="json"
        )
        assert response.status_code == 200
        option.refresh_from_db()
        assert option.is_active is False

    def test_an_option_a_dispatch_order_used_is_not_deleted(
        self, admin_client, make_option, ml, packed
    ):
        option = make_option("Common", 50)
        packed(ml)
        services.create_dispatch_orders(ml.org)
        assert DispatchOrder.objects.filter(delivery_option=option).exists()
        assert admin_client.delete(_option(option)).status_code == 409
        assert DeliveryOption.objects.filter(pk=option.pk).exists()

    def test_an_unused_option_is_deleted(self, admin_client, make_option):
        option = make_option("Common", 50)
        assert admin_client.delete(_option(option)).status_code == 204

    def test_another_org_sees_a_404(self, org_b_client, make_option):
        option = make_option("Common", 50)
        assert org_b_client.get(_option(option)).status_code == 404
        assert org_b_client.patch(_option(option), {}, format="json").status_code == 404
        assert org_b_client.delete(_option(option)).status_code == 404
        assert org_b_client.get(OPTIONS).json()["delivery_options"] == []


def _days(option):
    return f"{OPTIONS}{option.id}/days/"


class TestOptionDays:
    def test_an_admin_adds_a_free_day_and_an_extra_working_day(
        self, admin_client, make_option
    ):
        option = make_option("Courier", 20, kind="DOOR_TO_DOOR")
        free = {"date": "2026-12-25", "kind": "FREE", "note": "Christmas"}
        extra = {"date": "2026-12-27", "kind": "WORKING", "opens_at": "10:00"}

        first = admin_client.post(_days(option), free, format="json")
        second = admin_client.post(_days(option), extra, format="json")
        listed = admin_client.get(_days(option)).json()["days"]

        assert (first.status_code, second.status_code) == (201, 201)
        assert [(d["date"], d["kind"], d["opens_at"]) for d in listed] == [
            ("2026-12-25", "FREE", None),
            ("2026-12-27", "WORKING", "10:00:00"),
        ]
        assert DeliveryOptionDay.objects.filter(org=option.org).count() == 2

    @pytest.mark.parametrize(
        "body",
        [
            {"date": "2026-12-25", "kind": "FREE", "opens_at": "09:00"},
            {"date": "2026-12-25", "kind": "HOLIDAY"},
            {"date": "someday", "kind": "FREE"},
        ],
        ids=["free-day-with-opening", "kind-unknown", "date-not-a-date"],
    )
    def test_malformed_days_are_refused(self, admin_client, make_option, body):
        option = make_option("Courier", 20, kind="DOOR_TO_DOOR")
        assert admin_client.post(_days(option), body, format="json").status_code == 400
        assert not DeliveryOptionDay.objects.exists()

    def test_a_date_has_one_setting(self, admin_client, make_option):
        option = make_option("Courier", 20, kind="DOOR_TO_DOOR")
        body = {"date": "2026-12-25", "kind": "FREE"}
        assert admin_client.post(_days(option), body, format="json").status_code == 201
        again = admin_client.post(
            _days(option), {**body, "kind": "WORKING"}, format="json"
        )
        assert again.status_code == 400
        assert "date" in again.json()["errors"]

    def test_the_same_date_may_differ_between_options(self, admin_client, make_option):
        body = {"date": "2026-12-25", "kind": "FREE"}
        for name in ("Courier", "Agency"):
            option = make_option(name, 20, kind="DOOR_TO_DOOR")
            assert (
                admin_client.post(_days(option), body, format="json").status_code == 201
            )

    def test_an_admin_removes_a_day(self, admin_client, make_option):
        option = make_option("Courier", 20, kind="DOOR_TO_DOOR")
        day = DeliveryOptionDay.objects.create(
            delivery_option=option, date="2026-12-25", kind="FREE"
        )
        assert admin_client.delete(f"{_days(option)}{day.id}/").status_code == 204
        assert not DeliveryOptionDay.objects.exists()

    def test_a_member_reads_the_days_but_cannot_change_them(
        self, user_client, make_option
    ):
        option = make_option("Courier", 20, kind="DOOR_TO_DOOR")
        day = DeliveryOptionDay.objects.create(
            delivery_option=option, date="2026-12-25", kind="FREE"
        )
        body = {"date": "2026-12-26", "kind": "FREE"}

        assert user_client.get(_days(option)).json()["days"][0]["id"] == str(day.id)
        assert user_client.post(_days(option), body, format="json").status_code == 403
        assert user_client.delete(f"{_days(option)}{day.id}/").status_code == 403
        assert DeliveryOptionDay.objects.count() == 1

    def test_a_day_is_reached_only_through_its_own_option(
        self, admin_client, make_option
    ):
        option = make_option("Courier", 20, kind="DOOR_TO_DOOR")
        other = make_option("Agency", 20, kind="DOOR_TO_DOOR")
        day = DeliveryOptionDay.objects.create(
            delivery_option=option, date="2026-12-25", kind="FREE"
        )
        assert admin_client.delete(f"{_days(other)}{day.id}/").status_code == 404
        assert DeliveryOptionDay.objects.filter(pk=day.pk).exists()

    def test_another_org_sees_a_404(self, org_b_client, make_option):
        option = make_option("Courier", 20, kind="DOOR_TO_DOOR")
        day = DeliveryOptionDay.objects.create(
            delivery_option=option, date="2026-12-25", kind="FREE"
        )
        body = {"date": "2026-12-26", "kind": "FREE"}
        assert org_b_client.get(_days(option)).status_code == 404
        assert org_b_client.post(_days(option), body, format="json").status_code == 404
        assert org_b_client.delete(f"{_days(option)}{day.id}/").status_code == 404
        assert DeliveryOptionDay.objects.count() == 1


class TestImportBusinessHours:
    @pytest.fixture
    def calendar(self, org_a):
        """Monday to Friday 09:00 to 17:00, with a holiday last week and one
        next week."""
        hours = {}
        for day in WEEK[:5]:
            hours[f"{day}_open"], hours[f"{day}_close"] = time(9), time(17)
        calendar = BusinessCalendar.objects.create(
            org=org_a, name="Store", timezone="UTC", is_default=True, **hours
        )
        today = timezone.localdate()
        for offset, name in ((-7, "Past"), (7, "Coming")):
            BusinessHoliday.objects.create(
                calendar=calendar,
                org=org_a,
                date=today + timedelta(days=offset),
                name=name,
            )
        return calendar

    def _create(self, client, **extra):
        body = {"name": "Courier", "kind": "DOOR_TO_DOOR", "cost": "20", **extra}
        return client.post(OPTIONS, body, format="json")

    def test_a_new_option_can_import_the_business_hours(self, admin_client, calendar):
        response = self._create(admin_client, import_business_hours=True)

        assert response.status_code == 201, response.content
        data = response.json()
        assert [data[f"{day}_opens"] for day in WEEK] == ["09:00:00"] * 5 + [None, None]
        assert "import_business_hours" not in data
        days = admin_client.get(f"{OPTIONS}{data['id']}/days/").json()["days"]
        coming = str(timezone.localdate() + timedelta(days=7))
        assert [(d["date"], d["kind"], d["note"]) for d in days] == [
            (coming, "FREE", "Coming")
        ]

    def test_the_option_keeps_its_own_days_when_the_business_hours_change(
        self, admin_client, calendar
    ):
        data = self._create(admin_client, import_business_hours=True).json()
        calendar.saturday_open, calendar.saturday_close = time(10), time(14)
        calendar.save()
        BusinessHoliday.objects.create(
            calendar=calendar,
            org=calendar.org,
            date=timezone.localdate() + timedelta(days=9),
            name="Later",
        )

        option = DeliveryOption.objects.get(pk=data["id"])
        assert option.saturday_opens is None
        assert option.days.count() == 1

    def test_importing_needs_business_hours(self, admin_client):
        response = self._create(admin_client, import_business_hours=True)
        assert response.status_code == 400
        assert "import_business_hours" in response.json()["errors"]
        assert not DeliveryOption.objects.exists()

    def test_importing_is_for_a_new_option_only(
        self, admin_client, calendar, make_option
    ):
        option = make_option("Courier", 20, kind="DOOR_TO_DOOR")
        response = admin_client.patch(
            _option(option), {"import_business_hours": True}, format="json"
        )
        assert response.status_code == 400
        option.refresh_from_db()
        assert option.saturday_opens is not None
