"""The dispatch rules, on the pure planner: departures, lateness, penalties,
shared costs and the choice between options."""

import itertools
import random
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fulfillment.planner import (
    Option,
    Shipment,
    Workdays,
    next_departure,
    plan,
    single_run,
    split_evenly,
    trip_key,
    upcoming_departures,
)

TZ = ZoneInfo("America/Chicago")
# A Wednesday, 09:00 in Chicago.
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=TZ)
D = Decimal


def _option(id, cost, channel=None, **kwargs):
    return Option(id=id, cost=D(cost), channel_id=channel, **kwargs)


def _ship(id, channel, due_in_hours, penalty="0"):
    return Shipment(
        id=id,
        channel_id=channel,
        committed_at=NOW + timedelta(hours=due_in_hours),
        penalty=D(penalty),
    )


def _by_shipment(result):
    return {a.shipment_id: a for a in result.assignments}


class TestNextDeparture:
    def test_an_option_with_no_day_or_time_leaves_now(self):
        assert next_departure(_option("a", 0), NOW, TZ) == NOW

    def test_a_daily_time_later_today_is_today(self):
        option = _option("a", 0, departure_time=time(11, 0))
        assert next_departure(option, NOW, TZ) == datetime(2026, 11, 18, 11, tzinfo=TZ)

    def test_a_daily_time_already_gone_is_tomorrow(self):
        option = _option("a", 0, departure_time=time(8, 0))
        assert next_departure(option, NOW, TZ) == datetime(2026, 11, 19, 8, tzinfo=TZ)

    def test_a_daily_time_exactly_now_is_now(self):
        option = _option("a", 0, departure_time=time(9, 0))
        assert next_departure(option, NOW, TZ) == NOW

    def test_a_weekday_and_time_later_this_week(self):
        option = _option("a", 0, weekday="FRI", departure_time=time(16, 0))
        assert next_departure(option, NOW, TZ) == datetime(2026, 11, 20, 16, tzinfo=TZ)

    def test_todays_weekday_slot_already_gone_is_next_week(self):
        option = _option("a", 0, weekday="WED", departure_time=time(8, 0))
        assert next_departure(option, NOW, TZ) == datetime(2026, 11, 25, 8, tzinfo=TZ)

    def test_a_weekday_with_no_time_is_now_on_that_day(self):
        option = _option("a", 0, weekday="WED")
        assert next_departure(option, NOW, TZ) == NOW

    def test_a_weekday_with_no_time_starts_at_midnight_on_a_later_day(self):
        option = _option("a", 0, weekday="THU")
        assert next_departure(option, NOW, TZ) == datetime(2026, 11, 19, 0, tzinfo=TZ)

    def test_the_day_is_the_orgs_not_utcs(self):
        # 03:00 UTC on Wednesday is still Tuesday 21:00 in Chicago, so a
        # Tuesday 22:00 pickup is an hour away, not a week.
        now = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)
        option = _option("a", 0, weekday="TUE", departure_time=time(22, 0))
        assert next_departure(option, now, TZ) == datetime(2026, 11, 17, 22, tzinfo=TZ)


class TestUpcomingDepartures:
    def test_a_timed_option_offers_its_next_departures_in_order(self):
        option = _option("a", 0, weekday="FRI", departure_time=time(16, 0))
        assert upcoming_departures(option, NOW, TZ, 2) == [
            datetime(2026, 11, 20, 16, tzinfo=TZ),
            datetime(2026, 11, 27, 16, tzinfo=TZ),
        ]

    def test_a_daily_option_offers_one_departure_a_day(self):
        option = _option("a", 0, departure_time=time(16, 0))
        assert upcoming_departures(option, NOW, TZ, 3) == [
            datetime(2026, 11, 18, 16, tzinfo=TZ),
            datetime(2026, 11, 19, 16, tzinfo=TZ),
            datetime(2026, 11, 20, 16, tzinfo=TZ),
        ]

    def test_a_weekday_option_with_no_time_offers_one_departure_a_week(self):
        option = _option("a", 0, weekday="WED")
        assert upcoming_departures(option, NOW, TZ, 2) == [
            NOW,
            datetime(2026, 11, 25, 0, tzinfo=TZ),
        ]

    def test_an_option_with_no_day_or_time_only_offers_todays_run(self):
        assert upcoming_departures(_option("a", 0), NOW, TZ, 3) == [NOW]

    def test_once_todays_run_is_closed_an_untimed_option_runs_tomorrow(self):
        closed = {trip_key(NOW - timedelta(hours=2), TZ)}
        assert upcoming_departures(_option("a", 0), NOW, TZ, 1, closed) == [
            datetime(2026, 11, 19, 0, tzinfo=TZ)
        ]

    def test_a_closed_trip_is_skipped(self):
        option = _option("a", 0, departure_time=time(11, 0))
        closed = {trip_key(datetime(2026, 11, 18, 11, tzinfo=TZ), TZ)}
        assert upcoming_departures(option, NOW, TZ, 1, closed) == [
            datetime(2026, 11, 19, 11, tzinfo=TZ)
        ]

    def test_a_weekday_options_trip_is_the_whole_day(self):
        option = _option("a", 0, weekday="WED")
        closed = {trip_key(datetime(2026, 11, 18, 8, tzinfo=TZ), TZ)}
        assert upcoming_departures(option, NOW, TZ, 1, closed) == [
            datetime(2026, 11, 25, 0, tzinfo=TZ)
        ]

    def test_a_trip_is_the_day_in_the_orgs_calendar(self):
        # 03:00 UTC Wednesday is still Tuesday in Chicago.
        late_tuesday = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)
        assert trip_key(late_tuesday, TZ) == trip_key(
            datetime(2026, 11, 17, 9, tzinfo=TZ), TZ
        )


def _workdays(closed_weekdays=(), free=(), working=None, opens=time(9, 0)):
    """Open at ``opens`` every day but ``closed_weekdays`` (0 is Monday), with
    ``free`` dates off and ``working`` ({date: opening}) dates on."""
    opening = tuple(None if d in closed_weekdays else opens for d in range(7))
    exceptions = {day: None for day in free}
    exceptions.update(working or {})
    return Workdays(tz=TZ, opening=opening, exceptions=exceptions)


class TestWorkdays:
    # NOW is Wednesday 2026-11-18, 09:00.
    def test_a_daily_run_skips_a_free_day(self):
        option = _option(
            "a", 0, departure_time=time(11, 0), workdays=_workdays(free=[NOW.date()])
        )
        assert next_departure(option, NOW, TZ) == datetime(2026, 11, 19, 11, tzinfo=TZ)

    def test_a_weekly_run_on_a_free_day_waits_for_the_next_week(self):
        friday = datetime(2026, 11, 20).date()
        option = _option(
            "a",
            0,
            weekday="FRI",
            departure_time=time(16, 0),
            workdays=_workdays(free=[friday]),
        )
        assert next_departure(option, NOW, TZ) == datetime(2026, 11, 27, 16, tzinfo=TZ)

    def test_a_run_with_no_time_leaves_when_the_next_working_day_opens(self):
        # Wednesday and Thursday are off.
        option = _option("a", 0, workdays=_workdays(closed_weekdays=(2, 3)))
        assert next_departure(option, NOW, TZ) == datetime(2026, 11, 20, 9, tzinfo=TZ)

    def test_a_run_with_no_time_today_waits_for_opening_time(self):
        early = NOW.replace(hour=7)
        option = _option("a", 0, workdays=_workdays(opens=time(8, 30)))
        assert next_departure(option, early, TZ) == early.replace(hour=8, minute=30)
        assert next_departure(option, NOW, TZ) == NOW

    def test_an_extra_working_day_runs_on_a_day_off(self):
        # Off every day but an extra working Saturday opening at 10:00.
        saturday = datetime(2026, 11, 21).date()
        workdays = _workdays(closed_weekdays=range(7), working={saturday: time(10, 0)})
        option = _option("a", 0, workdays=workdays)
        assert next_departure(option, NOW, TZ) == datetime(2026, 11, 21, 10, tzinfo=TZ)

    def test_each_option_follows_its_own_days(self):
        # Same cost; the first is off today, the second works today.
        off_today = _option(
            "off-today", 10, workdays=_workdays(free=[NOW.date()]), dispatch_minutes=60
        )
        works_today = _option(
            "works-today", 10, workdays=_workdays(), dispatch_minutes=60
        )
        result = plan(
            [_ship("s", "ml", 5, penalty="50")], [off_today, works_today], NOW, TZ
        )
        assert _by_shipment(result)["s"].option_id == "works-today"

    def test_without_a_working_day_in_reach_there_is_no_run(self):
        never = _option("a", 0, workdays=_workdays(closed_weekdays=range(7)))
        assert next_departure(never, NOW, TZ) is None
        result = plan([_ship("s", "ml", 24)], [never], NOW, TZ)
        assert result.unassigned == ["s"]


class TestSingleRun:
    def test_the_cheapest_run_that_takes_them_all(self):
        ships = [_ship("a", "ml", 24), _ship("b", "ml", 24)]
        options = [_option("common", 40), _option("pickup", 25, channel="ml")]
        option, departs, _ = single_run(ships, options, NOW, TZ)
        assert (option.id, departs) == ("pickup", NOW)

    def test_a_penalty_counts_in_the_choice(self):
        ships = [_ship("a", "ml", 2, penalty="100")]
        slow = _option("slow", 10, dispatch_minutes=24 * 60)
        fast = _option("fast", 50, dispatch_minutes=60)
        assert single_run(ships, [slow, fast], NOW, TZ)[0].id == "fast"

    def test_an_option_too_small_or_for_one_channel_only_is_passed_over(self):
        ships = [_ship("a", "ml", 24), _ship("b", "amazon", 24)]
        options = [
            _option("one-seat", 5, capacity=1),
            _option("ml-only", 5, channel="ml"),
            _option("common", 60),
        ]
        assert single_run(ships, options, NOW, TZ)[0].id == "common"

    def test_none_when_no_option_can(self):
        ships = [_ship("a", "ml", 24), _ship("b", "amazon", 24)]
        assert single_run(ships, [_option("ml-only", 5, channel="ml")], NOW, TZ) is None

    def test_a_taken_run_is_skipped(self):
        ships = [_ship("a", "ml", 72)]
        pickup = _option("pickup", 5, departure_time=time(11, 0))
        taken = {"pickup": {trip_key(datetime(2026, 11, 18, 11, tzinfo=TZ), TZ)}}
        assert single_run(ships, [pickup], NOW, TZ, taken)[1] == datetime(
            2026, 11, 19, 11, tzinfo=TZ
        )

    def test_an_option_with_no_run_in_reach_is_passed_over(self):
        never = _option("x", 5, workdays=_workdays(closed_weekdays=range(7)))
        assert single_run([_ship("a", "ml", 24)], [never], NOW, TZ) is None


class TestFixedDeparture:
    def test_an_option_with_a_fixed_departure_offers_only_that_run(self):
        when = datetime(2026, 11, 25, 10, tzinfo=TZ)
        seat = _option("seat", 0, fixed_departure=when, capacity=5)
        assert upcoming_departures(seat, NOW, TZ, 3) == [when]
        assignment = _by_shipment(plan([_ship("s", "ml", 24 * 30)], [seat], NOW, TZ))[
            "s"
        ]
        assert assignment.departs_at == when


class TestFixedDepartureTies:
    def test_a_chosen_run_is_filled_first_when_it_costs_no_more(self):
        # Both ways cost 90 and nobody is late; the chosen run leaves anyway,
        # so two sales take it and one takes the other option's next run.
        real = _option("real", 90, capacity=3)
        seat = _option("seat", 0, capacity=2, fixed_departure=NOW)
        closed = {"real": {trip_key(NOW, TZ)}}
        ships = [_ship(i, "ml", 48) for i in "bcd"]
        result = plan(ships, [real, seat], NOW, TZ, closed=closed)
        assert sorted(a.option_id for a in result.assignments) == [
            "real",
            "seat",
            "seat",
        ]


class TestSplitEvenly:
    def test_shares_differ_by_a_cent_at_most_and_add_up(self):
        shares = split_evenly(D("100.00"), 3)
        assert shares == [D("33.34"), D("33.33"), D("33.33")]
        assert sum(shares) == D("100.00")

    def test_a_free_option_splits_into_zeros(self):
        assert split_evenly(D("0"), 2) == [D("0.00"), D("0.00")]


class TestLateness:
    def test_arriving_exactly_at_the_committed_time_is_on_time(self):
        option = _option("agency", 0, dispatch_minutes=120)
        result = plan([_ship("s", "ml", 2, penalty="50")], [option], NOW, TZ)
        assignment = _by_shipment(result)["s"]
        assert assignment.arrives_at == NOW + timedelta(hours=2)
        assert (assignment.late, assignment.penalty) == (False, D("0.00"))

    def test_a_minute_past_the_committed_time_pays_the_penalty(self):
        option = _option("agency", 0, dispatch_minutes=121)
        result = plan([_ship("s", "ml", 2, penalty="50")], [option], NOW, TZ)
        assignment = _by_shipment(result)["s"]
        assert (assignment.late, assignment.penalty) == (True, D("50"))
        assert result.total_penalty == D("50")

    def test_departure_day_and_time_count_towards_arrival(self):
        # Leaves Friday 16:00 and takes a day: Saturday 16:00.
        option = _option(
            "common",
            0,
            weekday="FRI",
            departure_time=time(16, 0),
            dispatch_minutes=24 * 60,
        )
        due_saturday_noon = _ship("s", "ml", 3 * 24 + 3, penalty="10")
        assignment = _by_shipment(plan([due_saturday_noon], [option], NOW, TZ))["s"]
        assert assignment.departs_at == datetime(2026, 11, 20, 16, tzinfo=TZ)
        assert assignment.arrives_at == datetime(2026, 11, 21, 16, tzinfo=TZ)
        assert assignment.late is True

    def test_a_channel_that_charges_nothing_takes_a_late_cheap_option(self):
        slow_free = _option("slow", 0, dispatch_minutes=48 * 60)
        fast_paid = _option("fast", 80, dispatch_minutes=60)
        result = plan(
            [_ship("s", "store", 24, penalty="0")], [slow_free, fast_paid], NOW, TZ
        )
        assignment = _by_shipment(result)["s"]
        assert assignment.option_id == "slow"
        assert (assignment.late, assignment.penalty) == (True, D("0.00"))


class TestChoosing:
    def test_a_penalty_cheaper_than_the_faster_option_is_paid(self):
        on_time = _option("courier", 100, dispatch_minutes=60)
        late = _option("common", 20, dispatch_minutes=48 * 60)
        result = plan([_ship("s", "ml", 24, penalty="50")], [on_time, late], NOW, TZ)
        assert _by_shipment(result)["s"].option_id == "common"
        assert result.total_cost + result.total_penalty == D("70")

    def test_a_penalty_dearer_than_the_faster_option_is_avoided(self):
        on_time = _option("courier", 100, dispatch_minutes=60)
        late = _option("common", 20, dispatch_minutes=48 * 60)
        result = plan([_ship("s", "ml", 24, penalty="200")], [on_time, late], NOW, TZ)
        assert _by_shipment(result)["s"].option_id == "courier"
        assert result.total_cost + result.total_penalty == D("100")

    def test_grouping_on_a_shared_option_beats_each_sales_own_cheapest(self):
        # Alone, each sale's cheapest is its channel's pickup at 70. Together
        # they share the common dispatch at 100, 50 each.
        options = [
            _option("common", 100),
            _option("ml-pickup", 70, channel="ml"),
            _option("amazon-agency", 70, channel="amazon"),
        ]
        ships = [_ship("a", "ml", 24), _ship("b", "amazon", 24)]
        result = plan(ships, options, NOW, TZ)
        chosen = _by_shipment(result)
        assert {chosen["a"].option_id, chosen["b"].option_id} == {"common"}
        assert [chosen["a"].cost_share, chosen["b"].cost_share] == [D("50"), D("50")]
        assert result.total_cost == D("100")

    def test_a_single_sale_does_not_pay_for_a_shared_option_it_cannot_fill(self):
        options = [_option("common", 100), _option("ml-pickup", 70, channel="ml")]
        result = plan([_ship("a", "ml", 24)], options, NOW, TZ)
        assert _by_shipment(result)["a"].option_id == "ml-pickup"

    def test_the_cost_is_split_between_every_sale_on_the_option(self):
        options = [_option("pickup", D("100.00"), channel="ml")]
        ships = [_ship(i, "ml", 24) for i in "abc"]
        shares = [a.cost_share for a in plan(ships, options, NOW, TZ).assignments]
        assert shares == [D("33.34"), D("33.33"), D("33.33")]

    def test_the_penalty_stays_with_the_late_sale(self):
        option = _option("pickup", 60, channel="ml", dispatch_minutes=10 * 60)
        ships = [_ship("early", "ml", 5, penalty="40"), _ship("later", "ml", 48, "40")]
        chosen = _by_shipment(plan(ships, [option], NOW, TZ))
        assert (chosen["early"].penalty, chosen["later"].penalty) == (
            D("40"),
            D("0.00"),
        )
        assert chosen["early"].cost_share == chosen["later"].cost_share == D("30")

    def test_a_channel_option_serves_only_its_channel(self):
        result = plan(
            [_ship("a", "amazon", 24)],
            [_option("ml-pickup", 0, channel="ml")],
            NOW,
            TZ,
        )
        assert result.assignments == []
        assert result.unassigned == ["a"]

    def test_a_sale_no_option_serves_does_not_block_the_others(self):
        result = plan(
            [_ship("a", "amazon", 24), _ship("b", "ml", 24)],
            [_option("ml-pickup", 10, channel="ml")],
            NOW,
            TZ,
        )
        assert [a.shipment_id for a in result.assignments] == ["b"]
        assert result.unassigned == ["a"]

    def test_on_equal_money_the_plan_with_fewer_late_sales_wins(self):
        late = _option("a-late", 30, dispatch_minutes=48 * 60)
        on_time = _option("b-on-time", 30, dispatch_minutes=60)
        result = plan([_ship("s", "store", 24, penalty="0")], [late, on_time], NOW, TZ)
        assert _by_shipment(result)["s"].option_id == "b-on-time"

    def test_on_equal_money_and_lateness_fewer_departures_win(self):
        options = [_option("x", 0), _option("y", 0)]
        ships = [_ship("a", "ml", 24), _ship("b", "ml", 24)]
        result = plan(ships, options, NOW, TZ)
        assert len({a.option_id for a in result.assignments}) == 1

    def test_among_open_options_a_sale_takes_the_earliest_arrival(self):
        # The urgent ML sale needs the fast pickup and the Amazon sale needs
        # the common dispatch, so both leave. The relaxed ML sale is on time
        # on either and rides the one that gets there first.
        common = _option("common", 10, dispatch_minutes=600)
        fast_ml = _option("ml-fast", 10, channel="ml", dispatch_minutes=60)
        ships = [
            _ship("urgent", "ml", 2, penalty="500"),
            _ship("amz", "amazon", 24),
            _ship("relaxed", "ml", 24),
        ]
        chosen = _by_shipment(plan(ships, [common, fast_ml], NOW, TZ))
        assert chosen["urgent"].option_id == "ml-fast"
        assert chosen["amz"].option_id == "common"
        assert chosen["relaxed"].option_id == "ml-fast"

    def test_nothing_to_ship_is_an_empty_plan(self):
        result = plan([], [_option("x", 50)], NOW, TZ)
        assert (result.assignments, result.unassigned) == ([], [])
        assert result.total_cost == D("0")


class TestCapacity:
    def test_sales_that_do_not_fit_take_the_options_next_departure(self):
        courier = _option(
            "courier",
            60,
            departure_time=time(16, 0),
            dispatch_minutes=60,
            capacity=2,
        )
        ships = [_ship(i, "ml", 72) for i in "abc"]
        result = plan(ships, [courier], NOW, TZ)
        departures = sorted(a.departs_at for a in result.assignments)
        assert departures == [
            datetime(2026, 11, 18, 16, tzinfo=TZ),
            datetime(2026, 11, 18, 16, tzinfo=TZ),
            datetime(2026, 11, 19, 16, tzinfo=TZ),
        ]
        # Each departure's cost is split between its own sales.
        assert sorted(a.cost_share for a in result.assignments) == [
            D("30"),
            D("30"),
            D("60"),
        ]

    def test_sales_that_do_not_fit_take_another_option(self):
        # Two one-seat options at 10 and 15 beat sharing the common one at 40.
        courier = _option("courier", 10, capacity=1)
        agency = _option("agency", 15, capacity=1)
        common = _option("common", 40)
        ships = [_ship("a", "ml", 24), _ship("b", "ml", 24)]
        result = plan(ships, [courier, agency, common], NOW, TZ)
        chosen = _by_shipment(result)
        assert sorted(a.option_id for a in chosen.values()) == ["agency", "courier"]
        assert result.total_cost == D("25")

    def test_capacity_sends_the_sale_that_would_pay_most_on_time(self):
        # One on-time seat: it goes to the sale whose lateness costs more.
        fast = _option("fast", 0, dispatch_minutes=60, capacity=1)
        slow = _option("slow", 0, dispatch_minutes=48 * 60)
        ships = [_ship("cheap", "ml", 24, penalty="10"), _ship("dear", "amz", 24, "90")]
        chosen = _by_shipment(plan(ships, [fast, slow], NOW, TZ))
        assert chosen["dear"].option_id == "fast"
        assert (chosen["cheap"].option_id, chosen["cheap"].penalty) == ("slow", D("10"))

    def test_sales_beyond_every_departure_are_left_unassigned(self):
        # An option with no day or time has only the one departure.
        courier = _option("courier", 10, capacity=2)
        result = plan([_ship(i, "ml", 24) for i in "abc"], [courier], NOW, TZ)
        assert len(result.assignments) == 2
        assert result.unassigned == ["c"]

    def test_as_many_departures_ahead_as_the_sales_need(self):
        pickup = _option("pickup", 0, departure_time=time(11, 0), capacity=1)
        ships = [_ship(i, "ml", 24 * 30) for i in range(9)]
        result = plan(ships, [pickup], NOW, TZ)
        assert result.unassigned == []
        days = sorted(a.departs_at.date() for a in result.assignments)
        assert len(set(days)) == 9

    def test_sales_go_past_several_full_options(self):
        # Two one-seat options, each running daily, and five sales: they
        # fill both options day after day until everyone has a seat.
        first = _option("first", 10, departure_time=time(11, 0), capacity=1)
        second = _option("second", 10, departure_time=time(16, 0), capacity=1)
        ships = [_ship(i, "ml", 24 * 10) for i in range(5)]
        result = plan(ships, [first, second], NOW, TZ)
        assert result.unassigned == []
        assert len({(a.option_id, a.departs_at) for a in result.assignments}) == 5

    def test_a_closed_departure_takes_no_more_sales(self):
        pickup = _option("pickup", 20, departure_time=time(11, 0), dispatch_minutes=0)
        today = datetime(2026, 11, 18, 11, tzinfo=TZ)
        closed = {"pickup": {trip_key(today, TZ)}}
        assignment = _by_shipment(
            plan([_ship("a", "ml", 72)], [pickup], NOW, TZ, closed=closed)
        )["a"]
        assert assignment.departs_at == datetime(2026, 11, 19, 11, tzinfo=TZ)


class TestTheDocsExample:
    """The worked example in docs/features/dispatch.md, so the page stays true."""

    OPTIONS = [
        _option("ml-agency", 70, channel="ml", dispatch_minutes=30),
        _option("amazon-agency", 70, channel="amazon", dispatch_minutes=30),
        _option(
            "common",
            100,
            weekday="FRI",
            departure_time=time(16, 0),
            dispatch_minutes=24 * 60,
        ),
    ]
    # Wednesday 09:00 to Sunday 09:00.
    SALES = [
        _ship("ml", "ml", 96, penalty="100"),
        _ship("amazon", "amazon", 96, penalty="80"),
    ]

    def test_two_sales_share_the_common_dispatch(self):
        result = plan(self.SALES, self.OPTIONS, NOW, TZ)
        chosen = _by_shipment(result)
        assert {a.option_id for a in chosen.values()} == {"common"}
        assert {a.cost_share for a in chosen.values()} == {D("50")}
        assert chosen["ml"].arrives_at == datetime(2026, 11, 21, 16, tzinfo=TZ)
        assert result.total_cost + result.total_penalty == D("100")

    def test_an_urgent_third_sale_moves_everyone_to_the_agencies(self):
        urgent = _ship("urgent", "ml", 1, penalty="100")
        result = plan([*self.SALES, urgent], self.OPTIONS, NOW, TZ)
        chosen = _by_shipment(result)
        assert chosen["urgent"].option_id == chosen["ml"].option_id == "ml-agency"
        assert chosen["urgent"].cost_share == chosen["ml"].cost_share == D("35")
        assert chosen["amazon"].option_id == "amazon-agency"
        assert result.total_cost + result.total_penalty == D("140")


class TestSearch:
    def test_a_search_cut_short_keeps_the_best_plan_found(self):
        options = [_option(f"o{i}", 10 + i, dispatch_minutes=60 * i) for i in range(12)]
        ships = [_ship(i, "ml", i % 13, penalty="25") for i in range(30)]
        result = plan(ships, options, NOW, TZ, budget=5)
        assert result.exhaustive is False
        assert len(result.assignments) == 30

    def test_with_no_budget_at_all_sales_are_placed_greedily_within_capacity(self):
        fast = _option("fast", 0, dispatch_minutes=60, capacity=1)
        slow = _option("slow", 0, dispatch_minutes=48 * 60, capacity=1)
        ships = [_ship("cheap", "ml", 24, penalty="10"), _ship("dear", "amz", 24, "90")]
        result = plan(ships, [fast, slow], NOW, TZ, budget=0)
        chosen = _by_shipment(result)
        assert result.exhaustive is False
        assert (chosen["dear"].option_id, chosen["cheap"].option_id) == ("fast", "slow")

    def test_matches_trying_every_assignment(self):
        rng = random.Random(20261118)
        for _ in range(300):
            options = [
                _option(
                    i,
                    rng.choice([0, 50, 80, 120]),
                    channel=rng.choice([None, None, "ml", "amazon"]),
                    weekday=rng.choice(["", "MON", "WED", "FRI"]),
                    departure_time=rng.choice([None, time(11), time(16)]),
                    dispatch_minutes=rng.choice([0, 60, 1440, 2880]),
                    capacity=rng.choice([None, None, 1, 2, 3]),
                )
                for i in range(rng.randint(1, 4))
            ]
            ships = [
                _ship(
                    j,
                    rng.choice(["ml", "amazon", "store"]),
                    rng.randint(-5, 200),
                    penalty=rng.choice(["0", "30", "100"]),
                )
                for j in range(rng.randint(1, 5))
            ]
            closed = {}
            if rng.random() < 0.3:
                option = rng.choice(options)
                first = next_departure(option, NOW, TZ)
                closed = {option.id: {trip_key(first, TZ)}}
            result = plan(ships, options, NOW, TZ, closed=closed)
            for key, riders in _loads(result).items():
                capacity = next(o.capacity for o in options if o.id == key[0])
                assert capacity is None or riders <= capacity
            late = sum(a.late for a in result.assignments)
            money = result.total_cost + result.total_penalty
            assert (len(result.unassigned), money, late) == _brute(
                ships, options, closed
            )


def _loads(result):
    loads = {}
    for a in result.assignments:
        key = (a.option_id, a.departs_at)
        loads[key] = loads.get(key, 0) + 1
    return loads


def _brute(ships, options, closed):
    """The lowest (unassigned, money, late) over every assignment of each
    shipment to a departure it may use, or to none, within capacities."""
    departures = []
    for option in options:
        serves = sum(1 for s in ships if option.channel_id in (None, s.channel_id))
        if not serves:
            continue
        count = -(-serves // option.capacity) if option.capacity else 1
        for departs in upcoming_departures(
            option, NOW, TZ, count, closed.get(option.id, ())
        ):
            arrives = departs + timedelta(minutes=option.dispatch_minutes)
            departures.append((option, arrives))
    choices = [
        [None]
        + [
            i
            for i, (option, _) in enumerate(departures)
            if option.channel_id in (None, ship.channel_id)
        ]
        for ship in ships
    ]
    best = None
    for combo in itertools.product(*choices):
        loads = {}
        for i in combo:
            if i is not None:
                loads[i] = loads.get(i, 0) + 1
        if any(
            departures[i][0].capacity is not None and n > departures[i][0].capacity
            for i, n in loads.items()
        ):
            continue
        money = sum(departures[i][0].cost for i in loads)
        late = 0
        for ship, i in zip(ships, combo):
            if i is not None and departures[i][1] > ship.committed_at:
                money += ship.penalty
                late += 1
        key = (combo.count(None), money, late)
        if best is None or key < best:
            best = key
    return best
