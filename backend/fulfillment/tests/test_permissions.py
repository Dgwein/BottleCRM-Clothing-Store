"""Teams handle order types: the assignment settings, what each level allows,
and the shared queues each type's teams work from, with their filters and
paging."""

from datetime import time, timedelta

import pytest
from django.utils import timezone

from common.models import Profile, Teams, User
from common.testing import _make_authenticated_client
from fulfillment import access, services
from fulfillment.models import DispatchOrder, QueueAssignment

ASSIGNMENTS = "/api/fulfillment/queue-assignments/"
QUEUES = "/api/fulfillment/queues/"


def _assignment(assignment):
    return f"{ASSIGNMENTS}{assignment.id}/"


def _team(org, name="Packers"):
    return Teams.objects.create(name=name, description="", org=org)


def _sale(make_order, channel, name="Sale", due_in_hours=24, preparations=()):
    return services.register_sale(
        make_order(name=name),
        channel,
        timezone.now() + timedelta(hours=due_in_hours),
        [{"description": d} for d in preparations],
    )


def _member(org, email):
    user = User.objects.create_user(email=email, password="x")
    profile = Profile.objects.create(user=user, org=org, role="USER", is_active=True)
    return profile, _make_authenticated_client(user, org, profile)


def _items(client, kind, **params):
    response = client.get(f"{QUEUES}{kind}/", params)
    assert response.status_code == 200, response.content
    return response.json()


class TestAssignments:
    def test_an_admin_assigns_a_team_to_an_order_type(self, admin_client, org_a):
        team = _team(org_a)
        body = {"team": str(team.id), "order_type": "PACKAGING", "access": "HANDLE"}

        response = admin_client.post(ASSIGNMENTS, body, format="json")

        assert response.status_code == 201, response.content
        data = response.json()
        assert (data["team_name"], data["order_type"], data["access"]) == (
            "Packers",
            "PACKAGING",
            "HANDLE",
        )
        assert QueueAssignment.objects.get().org == org_a

    def test_an_admin_lists_changes_and_removes_assignments(self, admin_client, org_a):
        row = QueueAssignment.objects.create(
            team=_team(org_a), order_type="DELIVERY", access="VIEW"
        )

        listed = admin_client.get(ASSIGNMENTS).json()["assignments"]
        changed = admin_client.patch(
            _assignment(row), {"access": "CLOSE"}, format="json"
        )
        removed = admin_client.delete(_assignment(row))

        assert [a["id"] for a in listed] == [str(row.id)]
        assert changed.status_code == 200
        assert changed.json()["access"] == "CLOSE"
        assert removed.status_code == 204
        assert not QueueAssignment.objects.exists()

    def test_a_non_admin_cannot_read_or_edit_the_assignment_settings(
        self, user_client, org_a
    ):
        # The settings say which teams handle which order types. A member
        # works the queues their teams are given; they do not configure them.
        row = QueueAssignment.objects.create(team=_team(org_a), order_type="PACKAGING")
        body = {"team": str(row.team_id), "order_type": "DELIVERY"}

        assert user_client.get(ASSIGNMENTS).status_code == 403
        assert user_client.post(ASSIGNMENTS, body, format="json").status_code == 403
        assert user_client.patch(_assignment(row), {}, format="json").status_code == 403
        assert user_client.delete(_assignment(row)).status_code == 403
        assert QueueAssignment.objects.count() == 1

    def test_an_app_member_cannot_change_assignments(self, app_client, org_a):
        body = {"team": str(_team(org_a).id), "order_type": "DELIVERY"}
        assert app_client.post(ASSIGNMENTS, body, format="json").status_code == 403
        assert not QueueAssignment.objects.exists()

    def test_a_team_can_be_given_inspection(self, admin_client, org_a):
        body = {"team": str(_team(org_a, "Returns").id), "order_type": "INSPECTION"}
        response = admin_client.post(ASSIGNMENTS, body, format="json")
        assert response.status_code == 201
        assert response.json()["order_type"] == "INSPECTION"

    @pytest.mark.parametrize(
        "body",
        [
            {"order_type": "PACKAGING", "access": "CLOSE"},
            {"order_type": "SHIPPING"},
            {"order_type": "PACKAGING", "access": "OWNER"},
        ],
        ids=["close-outside-delivery", "order-type-unknown", "access-unknown"],
    )
    def test_malformed_assignments_are_refused(self, admin_client, org_a, body):
        body = {"team": str(_team(org_a).id), **body}
        response = admin_client.post(ASSIGNMENTS, body, format="json")
        assert response.status_code == 400
        assert not QueueAssignment.objects.exists()

    def test_a_team_is_assigned_once_per_order_type(self, admin_client, org_a):
        team = _team(org_a)
        QueueAssignment.objects.create(team=team, order_type="PACKAGING")
        body = {"team": str(team.id), "order_type": "PACKAGING", "access": "VIEW"}

        response = admin_client.post(ASSIGNMENTS, body, format="json")

        assert response.status_code == 400
        assert "order_type" in response.json()["errors"]

    def test_changing_to_close_outside_delivery_is_refused(self, admin_client, org_a):
        row = QueueAssignment.objects.create(team=_team(org_a), order_type="PACKAGING")
        response = admin_client.patch(
            _assignment(row), {"access": "CLOSE"}, format="json"
        )
        assert response.status_code == 400
        row.refresh_from_db()
        assert row.access == "HANDLE"

    def test_another_orgs_team_cannot_be_named(self, admin_client, org_b):
        body = {"team": str(_team(org_b, "Theirs").id), "order_type": "PACKAGING"}
        response = admin_client.post(ASSIGNMENTS, body, format="json")
        assert response.status_code == 400
        assert "team" in response.json()["errors"]

    def test_another_org_sees_a_404_and_an_empty_list(self, org_b_client, org_a):
        row = QueueAssignment.objects.create(team=_team(org_a), order_type="PACKAGING")
        assert (
            org_b_client.patch(_assignment(row), {}, format="json").status_code == 404
        )
        assert org_b_client.delete(_assignment(row)).status_code == 404
        assert org_b_client.get(ASSIGNMENTS).json()["assignments"] == []


class TestLevels:
    @pytest.mark.parametrize(
        "method,url",
        [
            ("get", "/api/fulfillment/sales/"),
            ("get", f"{QUEUES}preparation/"),
            ("get", f"{QUEUES}packaging/"),
            ("get", f"{QUEUES}delivery/"),
            ("get", "/api/fulfillment/dispatch-plan/"),
            ("get", "/api/fulfillment/dispatch-orders/"),
            ("post", "/api/fulfillment/dispatch-orders/"),
            ("post", "/api/fulfillment/dispatch-orders/manual/"),
        ],
        ids=[
            "sales",
            "preparation-queue",
            "packaging-queue",
            "delivery-queue",
            "dispatch-plan",
            "dispatch-orders",
            "create-dispatch-orders",
            "create-manual-dispatch-order",
        ],
    )
    def test_a_member_on_no_team_is_refused(self, user_client, method, url):
        assert getattr(user_client, method)(url, {}, format="json").status_code == 403

    def test_a_member_on_no_team_sees_no_queues(self, user_client):
        response = user_client.get(QUEUES)
        assert response.status_code == 200
        assert response.json()["queues"] == []

    def test_a_member_on_no_team_cannot_work_any_order(
        self, user_client, make_order, ml
    ):
        sale = _sale(make_order, ml, preparations=["Sew"])
        for url in (
            f"/api/fulfillment/preparation-orders/{sale.preparation_orders.get().id}/complete/",
            f"/api/fulfillment/packaging-orders/{sale.packaging_order.id}/complete/",
        ):
            response = user_client.post(url)
            assert response.status_code == 403
            assert "None of your teams may work" in response.json()["detail"]
        assert sale.preparation_orders.get().status == "PENDING"

    def test_view_shows_a_queue_but_does_not_work_it(
        self, user_client, user_profile, grant, make_order, ml
    ):
        grant(user_profile, PACKAGING="VIEW")
        sale = _sale(make_order, ml)

        queue = user_client.get(f"{QUEUES}packaging/")
        work = user_client.post(
            f"/api/fulfillment/packaging-orders/{sale.packaging_order.id}/complete/"
        )

        assert queue.status_code == 200
        assert [i["sale"] for i in queue.json()["items"]] == [str(sale.id)]
        assert work.status_code == 403
        assert user_client.get(f"{QUEUES}preparation/").status_code == 403

    def test_handle_works_its_own_type_only(
        self, user_client, user_profile, grant, make_order, ml
    ):
        grant(user_profile, PREPARATION="HANDLE")
        sale = _sale(make_order, ml, preparations=["Sew"])

        done = user_client.post(
            f"/api/fulfillment/preparation-orders/{sale.preparation_orders.get().id}/complete/"
        )
        other = user_client.post(
            f"/api/fulfillment/packaging-orders/{sale.packaging_order.id}/complete/"
        )

        assert done.status_code == 200
        assert other.status_code == 403

    def test_delivery_handle_creates_dispatch_orders_but_close_closes_them(
        self, user_client, user_profile, grant, ml, packed, make_option
    ):
        make_option("Agency", "10", channel=ml, kind="CHANNEL_AGENCY")
        packed(ml)
        team = grant(user_profile, DELIVERY="HANDLE")

        assert user_client.post("/api/fulfillment/dispatch-orders/").status_code == 200
        order = DispatchOrder.objects.get()
        close = f"/api/fulfillment/dispatch-orders/{order.id}/close/"
        refused = user_client.post(close)
        assert refused.status_code == 403
        assert (
            "None of your teams may close dispatch orders." == refused.json()["detail"]
        )

        QueueAssignment.objects.filter(team=team).update(access="CLOSE")
        assert user_client.post(close).status_code == 200

    def test_delivery_view_cannot_create_or_release_dispatch_orders(
        self, user_client, user_profile, grant, ml, packed, make_option
    ):
        make_option("Agency", "10", channel=ml, kind="CHANNEL_AGENCY")
        sale = packed(ml)
        services.create_dispatch_orders(ml.org)
        order = DispatchOrder.objects.get()
        grant(user_profile, DELIVERY="VIEW")

        assert user_client.get("/api/fulfillment/dispatch-orders/").status_code == 200
        body = {"mode": "MANUAL", "sales": [str(sale.id)]}
        manual = user_client.post(
            "/api/fulfillment/dispatch-orders/manual/", body, format="json"
        )
        release = user_client.delete(f"/api/fulfillment/dispatch-orders/{order.id}/")
        assert manual.status_code == release.status_code == 403
        assert DispatchOrder.objects.filter(pk=order.pk).exists()

    def test_the_highest_level_of_the_members_teams_counts(
        self, user_client, user_profile, grant
    ):
        grant(user_profile, "Viewers", DELIVERY="VIEW")
        grant(user_profile, "Closers", DELIVERY="CLOSE")
        [queue] = user_client.get(QUEUES).json()["queues"]
        assert (queue["order_type"], queue["access"]) == ("DELIVERY", "CLOSE")

    def test_leaving_the_team_takes_the_access_away(
        self, user_client, user_profile, grant
    ):
        team = grant(user_profile, PACKAGING="HANDLE")
        assert user_client.get(f"{QUEUES}packaging/").status_code == 200
        team.users.remove(user_profile)
        assert user_client.get(f"{QUEUES}packaging/").status_code == 403

    def test_any_assignment_opens_the_sales(
        self, user_client, user_profile, grant, make_order, ml
    ):
        sale = _sale(make_order, ml)
        grant(user_profile, PREPARATION="VIEW")
        assert user_client.get("/api/fulfillment/sales/").json()["count"] == 1
        assert user_client.get(f"/api/fulfillment/sales/{sale.id}/").status_code == 200

    def test_another_orgs_team_gives_nothing_here(
        self, user_client, user_profile, org_b
    ):
        theirs = _team(org_b, "Theirs")
        theirs.users.add(user_profile)
        QueueAssignment.objects.create(team=theirs, order_type="PACKAGING")
        assert user_client.get(QUEUES).json()["queues"] == []

    def test_an_admin_on_no_team_holds_every_level(self, admin_client):
        queues = admin_client.get(QUEUES).json()["queues"]
        assert [(q["order_type"], q["access"]) for q in queues] == [
            ("PREPARATION", "HANDLE"),
            ("PACKAGING", "HANDLE"),
            ("DELIVERY", "CLOSE"),
        ]

    def test_inspection_access_is_held_for_the_returns_project(
        self, user_client, user_profile, grant
    ):
        # Inspection orders and their queue live in the returns project, which
        # asks this app for the level; no inspection queue is served here.
        grant(user_profile, "Returns", INSPECTION="HANDLE")
        assert access.may(user_profile, "INSPECTION", "HANDLE") is True
        assert access.may(user_profile, "PACKAGING", "VIEW") is False
        assert user_client.get(QUEUES).json()["queues"] == []
        assert user_client.get(f"{QUEUES}inspection/").status_code == 404


class TestSharedQueues:
    def test_every_member_of_a_team_works_the_same_queue(
        self, org_a, grant, make_order, ml
    ):
        first, first_client = _member(org_a, "first@test.com")
        second, second_client = _member(org_a, "second@test.com")
        team = grant(first, "Packers", PACKAGING="HANDLE")
        team.users.add(second)
        sale = _sale(make_order, ml)

        seen = [_items(c, "packaging")["items"] for c in (first_client, second_client)]
        assert seen[0] == seen[1]
        assert [i["sale"] for i in seen[0]] == [str(sale.id)]
        done = second_client.post(
            f"/api/fulfillment/packaging-orders/{sale.packaging_order.id}/complete/"
        )
        assert done.status_code == 200
        assert _items(first_client, "packaging")["items"] == []

    def test_two_teams_with_the_same_type_share_one_queue(
        self, org_a, grant, make_order, ml
    ):
        morning, morning_client = _member(org_a, "morning@test.com")
        evening, evening_client = _member(org_a, "evening@test.com")
        grant(morning, "Morning", PACKAGING="HANDLE")
        grant(evening, "Evening", PACKAGING="HANDLE")
        _sale(make_order, ml)
        assert (
            _items(morning_client, "packaging")["items"]
            == _items(evening_client, "packaging")["items"]
        )
        assert _items(morning_client, "packaging")["count"] == 1

    def test_a_member_of_two_teams_sees_both_teams_queues(
        self, user_client, user_profile, grant, make_order, ml
    ):
        grant(user_profile, "Sewing", PREPARATION="HANDLE")
        grant(user_profile, "Packers", PACKAGING="VIEW")
        _sale(make_order, ml, "Sewn", preparations=["Sew"])
        _sale(make_order, ml, "Ready")

        overview = user_client.get(QUEUES).json()["queues"]

        assert [(q["order_type"], q["access"]) for q in overview] == [
            ("PREPARATION", "HANDLE"),
            ("PACKAGING", "VIEW"),
        ]
        assert _items(user_client, "preparation")["count"] == 1
        assert _items(user_client, "packaging")["count"] == 1


class TestQueues:
    def test_each_queue_holds_what_can_be_worked_now(
        self, admin_client, make_order, ml, packed, make_option
    ):
        preparing = _sale(make_order, ml, "Preparing", preparations=["Sew"])
        packing = _sale(make_order, ml, "Packing")
        make_option("Agency", "10", channel=ml, kind="CHANNEL_AGENCY")
        packed(ml, name="Leaving")
        services.create_dispatch_orders(ml.org)
        packed(ml, name="Unplanned")

        def sales(kind):
            return [i["sale"] for i in _items(admin_client, kind)["items"]]

        assert sales("preparation") == [str(preparing.id)]
        assert sales("packaging") == [str(packing.id)]
        delivery = _items(admin_client, "delivery")
        assert delivery["count"] == 1
        assert delivery["unplanned_sales"] == 1
        assert [q["pending"] for q in admin_client.get(QUEUES).json()["queues"]] == [
            1,
            1,
            1,
        ]

    def test_the_most_urgent_comes_first(self, admin_client, make_order, ml):
        later = _sale(make_order, ml, "Later", due_in_hours=72, preparations=["Sew"])
        sooner = _sale(make_order, ml, "Sooner", due_in_hours=2, preparations=["Sew"])

        items = _items(admin_client, "preparation")["items"]

        assert [i["sale"] for i in items] == [str(sooner.id), str(later.id)]
        assert items[0]["description"] == "Sew"

    def test_done_and_cancelled_work_leaves_the_queue(
        self, admin_client, make_order, ml
    ):
        done = _sale(make_order, ml, "Done", preparations=["Sew"])
        services.complete_preparation(done.preparation_orders.get())
        cancelled = _sale(make_order, ml, "Cancelled", preparations=["Sew"])
        cancelled.order.status = "CANCELLED"
        cancelled.order.save()

        assert _items(admin_client, "preparation")["items"] == []

    def test_another_orgs_queues_are_empty(self, org_b_client, make_order, ml):
        _sale(make_order, ml, preparations=["Sew"])
        queues = org_b_client.get(QUEUES).json()["queues"]
        assert {q["pending"] for q in queues} == {0}
        assert _items(org_b_client, "preparation")["items"] == []


class TestQueueFilters:
    @pytest.fixture
    def spread(self, make_order, ml, amazon):
        """Four sales waiting for packing: two channels, due in 1 to 4 days."""
        return [
            _sale(make_order, channel, f"Order {i}", due_in_hours=24 * i)
            for i, channel in enumerate((ml, amazon, ml, amazon), start=1)
        ]

    def _names(self, data):
        return [i["order_name"] for i in data["items"]]

    def test_pages_walk_the_whole_queue(self, admin_client, spread):
        first = _items(admin_client, "packaging", limit=3)
        second = _items(admin_client, "packaging", limit=3, offset=3)

        assert (first["count"], first["limit"], first["offset"]) == (4, 3, 0)
        assert self._names(first) == ["Order 1", "Order 2", "Order 3"]
        assert (self._names(second), second["offset"]) == (["Order 4"], 3)

    def test_by_channel(self, admin_client, spread, amazon):
        data = _items(admin_client, "packaging", channel=str(amazon.id))
        assert self._names(data) == ["Order 2", "Order 4"]
        assert data["count"] == 2

    def test_by_due_day(self, admin_client, spread):
        today = timezone.localdate()
        data = _items(
            admin_client,
            "packaging",
            due_from=str(today + timedelta(days=2)),
            due_to=str(today + timedelta(days=3)),
        )
        assert self._names(data) == ["Order 2", "Order 3"]

    def test_by_search_on_the_order(self, admin_client, spread):
        assert self._names(_items(admin_client, "packaging", search="order 3")) == [
            "Order 3"
        ]

    def test_ordered_latest_due_first(self, admin_client, spread):
        latest_first = _items(admin_client, "packaging", ordering="-due")
        assert self._names(latest_first) == ["Order 4", "Order 3", "Order 2", "Order 1"]

    def test_an_unknown_ordering_keeps_the_most_urgent_first(
        self, admin_client, spread
    ):
        data = _items(admin_client, "packaging", ordering="price")
        assert self._names(data)[0] == "Order 1"

    @pytest.mark.parametrize(
        "params",
        [
            {"channel": "not-an-id"},
            {"due_from": "someday"},
            {"departs_to": "2026-02-30"},
        ],
        ids=["channel-not-an-id", "date-not-a-date", "date-that-does-not-exist"],
    )
    def test_malformed_filters_are_refused(self, admin_client, params):
        kind = "delivery" if "departs_to" in params else "packaging"
        assert admin_client.get(f"{QUEUES}{kind}/", params).status_code == 400

    def test_the_delivery_queue_filters_by_departure_day(
        self, admin_client, ml, packed, make_option
    ):
        make_option(
            "Daily",
            "10",
            channel=ml,
            kind="CHANNEL_AGENCY",
            capacity=1,
            departure_time=time(16, 0),
        )
        packed(ml, name="First")
        packed(ml, name="Second")
        services.create_dispatch_orders(ml.org)
        days = sorted(DispatchOrder.objects.values_list("departs_at", flat=True))
        first, second = (str(d.date()) for d in days)

        later = _items(admin_client, "delivery", departs_from=second)
        earlier = _items(admin_client, "delivery", departs_to=first)

        assert [o["departs_at"][:10] for o in later["dispatch_orders"]] == [second]
        assert [o["departs_at"][:10] for o in earlier["dispatch_orders"]] == [first]

    def test_the_delivery_queue_filters_and_pages(
        self, admin_client, ml, amazon, packed, make_option
    ):
        ml_agency = make_option("ML agency", "10", channel=ml, kind="CHANNEL_AGENCY")
        make_option("Amazon agency", "10", channel=amazon, kind="CHANNEL_AGENCY")
        packed(ml, name="Shirt")
        packed(amazon, name="Dress")
        services.create_dispatch_orders(ml.org)

        everything = _items(admin_client, "delivery")
        by_option = _items(admin_client, "delivery", delivery_option=str(ml_agency.id))
        by_channel = _items(admin_client, "delivery", channel=str(amazon.id))
        by_search = _items(admin_client, "delivery", search="shirt")
        automatic = _items(admin_client, "delivery", mode="AUTO", limit=1)

        assert everything["count"] == 2
        assert [o["delivery_option"] for o in by_option["dispatch_orders"]] == [
            str(ml_agency.id)
        ]
        assert by_channel["dispatch_orders"][0]["items"][0]["order_name"] == "Dress"
        assert by_search["dispatch_orders"][0]["items"][0]["order_name"] == "Shirt"
        assert (automatic["count"], len(automatic["dispatch_orders"])) == (2, 1)
        assert _items(admin_client, "delivery", mode="MANUAL")["count"] == 0
