"""Which departure each ready sale should leave on.

Pure: no ORM, so the rules can be tested on their own. The caller passes the
options, the shipments, the departures already closed, the moment to plan from
and the org's timezone. See docs/features/dispatch.md for the rules and worked
examples.

The plan minimises, in this order: sales left without a departure, money (the
cost of every departure used plus every penalty), late sales, departures used,
and how late the sales arrive. A departure's cost is split between its sales,
so however it is split the money is the same, which is what lets the search
choose departures rather than shares.
"""

import heapq
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

WEEKDAY_INDEX = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}

# How far ahead a run is looked for, so a calendar with no workday in reach
# (or a holiday on every matching day) offers nothing rather than looping.
DAYS_AHEAD = 400

# Bounds the search's work, counted in edges looked at, which tracks time
# (about a second per few million). Realistic setups finish far below it; past
# it the best plan found so far is returned and marked as not exhaustive.
WORK_BUDGET = 3_000_000

CENT = Decimal("0.01")


class _OutOfBudget(Exception):
    pass


@dataclass(frozen=True)
class Option:
    id: object
    cost: Decimal
    # None serves every channel.
    channel_id: object | None
    weekday: str = ""
    departure_time: time | None = None
    dispatch_minutes: int = 0
    # Most packages per departure; None has no limit.
    capacity: int | None = None
    # A run already chosen: the option offers that departure and no other. It
    # leaves anyway, so it adds no departure to a plan, and among choices that
    # cost the same, a sale takes it first.
    fixed_departure: datetime | None = None
    # The option's own working days; None runs any day.
    workdays: "Workdays | None" = field(default=None, hash=False)


@dataclass(frozen=True)
class Workdays:
    """The days an option's runs may leave on. ``opening`` has seven entries,
    Monday first, each the day's opening time or None for a day off;
    ``exceptions`` maps a date to its opening time, or None for a free day."""

    tz: object
    opening: tuple
    exceptions: dict = field(default_factory=dict, hash=False)

    def opens(self, moment: datetime):
        day = moment.astimezone(self.tz).date()
        if day in self.exceptions:
            return self.exceptions[day]
        return self.opening[day.weekday()]


@dataclass(frozen=True)
class Shipment:
    id: object
    channel_id: object
    committed_at: datetime
    penalty: Decimal


@dataclass(frozen=True)
class Departure:
    option: Option
    departs_at: datetime
    arrives_at: datetime


@dataclass(frozen=True)
class Assignment:
    shipment_id: object
    option_id: object
    departs_at: datetime
    arrives_at: datetime
    late: bool
    cost_share: Decimal
    penalty: Decimal


@dataclass
class Plan:
    assignments: list[Assignment] = field(default_factory=list)
    # Shipments no departure could take: none serves their channel, or every
    # one that does is full.
    unassigned: list = field(default_factory=list)
    exhaustive: bool = True

    @property
    def total_cost(self) -> Decimal:
        return sum((a.cost_share for a in self.assignments), Decimal("0.00"))

    @property
    def total_penalty(self) -> Decimal:
        return sum((a.penalty for a in self.assignments), Decimal("0.00"))


def next_departure(option: Option, now: datetime, tz):
    """The first moment at or after ``now`` the option leaves, in ``tz``'s
    calendar, on one of its working days; None when there is none within
    ``DAYS_AHEAD``. A run with no time leaves now on today, or when the day
    opens on a later day (midnight for an option that runs any day)."""
    workdays = option.workdays
    local_now = now.astimezone(tz)
    weekday = WEEKDAY_INDEX.get(option.weekday) if option.weekday else None
    for offset in range(DAYS_AHEAD):
        day = local_now.date() + timedelta(days=offset)
        if weekday is not None and day.weekday() != weekday:
            continue
        if option.departure_time is not None:
            start = datetime.combine(day, option.departure_time, tz)
            if start < now:
                continue
        else:
            start = max(now, datetime.combine(day, time(0), tz))
        if workdays is not None:
            opens = workdays.opens(start)
            if opens is None:
                continue
            if option.departure_time is None:
                local_day = start.astimezone(workdays.tz).date()
                start = max(start, datetime.combine(local_day, opens, workdays.tz))
        return start
    return None


def trip_key(departs_at: datetime, tz):
    """What makes two departures of one option the same trip: an option runs at
    most once a day, so its trip is the day it leaves, in ``tz``'s calendar."""
    return departs_at.astimezone(tz).date()


def upcoming_departures(option: Option, now: datetime, tz, count: int, closed=()):
    """The next ``count`` runs of ``option``, skipping days in ``closed``.

    ``closed`` holds the ``trip_key`` of every run already taken: it has left
    with its sales fixed, or a manual dispatch order holds it, and that day's
    run is spent. An option with no day or time has one run in a plan: the
    next one whose day is not taken.
    """
    if option.fixed_departure is not None:
        return [option.fixed_departure]
    if not option.weekday and option.departure_time is None:
        count = 1
    found = []
    cursor = now
    # A closed trip is skipped without using up one of the `count`.
    for _ in range(count + len(closed)):
        departs = next_departure(option, cursor, tz)
        if departs is None:
            break
        if trip_key(departs, tz) not in closed:
            found.append(departs)
            if len(found) == count:
                break
        local_day = departs.astimezone(tz).date() + timedelta(days=1)
        cursor = datetime.combine(local_day, time(0), tz)
    return found


def single_run(shipments, options, now, tz, closed=None):
    """The best one run to carry every one of ``shipments`` together, as
    ``(option, departs, arrives)``, or None when no option can.

    An option qualifies when it serves every shipment's channel and its next
    free run has room for them all. The best has the lowest cost plus
    penalties, then the fewest late shipments, then the earliest arrival.
    """
    closed = closed or {}
    channels = {s.channel_id for s in shipments}
    best = None
    for option in sorted(options, key=lambda o: (o.cost, str(o.id))):
        if option.channel_id is not None and channels != {option.channel_id}:
            continue
        if option.capacity is not None and option.capacity < len(shipments):
            continue
        runs = upcoming_departures(option, now, tz, 1, closed.get(option.id, ()))
        if not runs:
            continue
        arrives = runs[0] + timedelta(minutes=option.dispatch_minutes)
        late = [s for s in shipments if arrives > s.committed_at]
        key = (option.cost + sum(s.penalty for s in late), len(late), arrives)
        if best is None or key < best[0]:
            best = (key, (option, runs[0], arrives))
    return None if best is None else best[1]


def split_evenly(amount: Decimal, parts: int) -> list[Decimal]:
    """``amount`` in ``parts`` shares that differ by at most a cent and add up to it."""
    cents = int((amount / CENT).to_integral_value(rounding=ROUND_HALF_UP))
    base, extra = divmod(cents, parts)
    return [(base + (1 if i < extra else 0)) * CENT for i in range(parts)]


def _cents(amount: Decimal) -> int:
    return int((amount / CENT).to_integral_value(rounding=ROUND_HALF_UP))


@dataclass
class _Group:
    """Shipments with the same departures open to them, the same on-time
    departures and the same penalty, so any one may stand in for another."""

    eligible: int
    on_time: int
    penalty: Decimal
    shipments: list = field(default_factory=list)


def plan(shipments, options, now, tz, closed=None, budget=WORK_BUDGET) -> Plan:
    """The plan for ``shipments``. ``closed`` maps an option id to the set of
    its taken trips (see ``trip_key``)."""
    closed = closed or {}
    shipments = list(shipments)
    options = sorted(options, key=lambda o: (o.cost, str(o.id)))
    departures = []
    for option in options:
        serves = sum(1 for s in shipments if option.channel_id in (None, s.channel_id))
        if not serves:
            continue
        # Enough departures to carry every sale the option serves on it alone:
        # a plan never needs more, as moving sales onto an earlier departure
        # of the same option is never worse.
        count = -(-serves // option.capacity) if option.capacity else 1
        for departs in upcoming_departures(
            option, now, tz, count, closed.get(option.id, ())
        ):
            arrives = departs + timedelta(minutes=option.dispatch_minutes)
            departures.append(Departure(option, departs, arrives))

    result = Plan()
    groups: dict[tuple, _Group] = {}
    for shipment in shipments:
        eligible = on_time = 0
        for i, departure in enumerate(departures):
            if departure.option.channel_id not in (None, shipment.channel_id):
                continue
            eligible |= 1 << i
            if departure.arrives_at <= shipment.committed_at:
                on_time |= 1 << i
        if not eligible:
            result.unassigned.append(shipment.id)
            continue
        key = (eligible, on_time, shipment.penalty)
        group = groups.setdefault(key, _Group(eligible, on_time, shipment.penalty))
        group.shipments.append(shipment)

    search = _Search(departures, list(groups.values()), budget)
    flows = search.run()
    result.exhaustive = search.exhaustive

    riders: dict[int, list[Shipment]] = {}
    by_arrival = sorted(range(len(departures)), key=search.rank.__getitem__)
    for g, group in enumerate(search.groups):
        waiting = iter(group.shipments)
        for i in by_arrival:
            for _ in range(flows.get((g, i), 0)):
                riders.setdefault(i, []).append(next(waiting))
        result.unassigned.extend(s.id for s in waiting)

    for i, riding in sorted(riders.items()):
        departure = departures[i]
        shares = split_evenly(departure.option.cost, len(riding))
        for shipment, share in zip(riding, shares):
            late = departure.arrives_at > shipment.committed_at
            result.assignments.append(
                Assignment(
                    shipment_id=shipment.id,
                    option_id=departure.option.id,
                    departs_at=departure.departs_at,
                    arrives_at=departure.arrives_at,
                    late=late,
                    cost_share=share,
                    penalty=shipment.penalty if late else Decimal("0.00"),
                )
            )
    return result


class _Search:
    """Branch and bound over which departures to open, exact within the budget.

    Every criterion the plan minimises is folded into one integer, each weighted
    above the most every criterion below it can add up to. A node's bound is
    the cost of the departures it has opened plus the best assignment, within
    capacities, onto every departure not yet ruled out, where a sale on a
    departure still undecided is charged that departure's cost divided by the
    most sales it could carry; neither part can overestimate. When the
    assignment uses only opened departures the bound is exact and the node is
    done; otherwise it branches on the undecided departure the bound
    undercharges most.
    """

    def __init__(self, departures, groups, budget):
        self.departures = departures
        self.groups = groups
        self.budget = budget
        self.work = 0
        self.exhaustive = True
        self.best_value, self.best_flows = None, None
        n = sum(len(g.shipments) for g in groups)
        d = len(departures)
        self.total = n
        order = sorted(
            range(d),
            key=lambda i: (
                departures[i].option.fixed_departure is None,
                departures[i].arrives_at,
                i,
            ),
        )
        self.rank = rank = [0] * d
        for position, i in enumerate(order):
            rank[i] = position
        w_departure = n * d + 1
        self.w_late = w_late = w_departure * (d + 1)
        w_money = w_late * (n + 1)
        money = sum(_cents(x.option.cost) for x in departures) + sum(
            _cents(g.penalty) * len(g.shipments) for g in groups
        )
        self.unplaced = w_money * (money + 1)
        self.open_cost = [
            _cents(x.option.cost) * w_money
            + (0 if x.option.fixed_departure else w_departure)
            for x in departures
        ]
        self.capacity = [x.option.capacity or n for x in departures]
        self.edges = []
        reach = [0] * d
        for group in groups:
            row = []
            for i in range(d):
                if not group.eligible >> i & 1:
                    continue
                cost = rank[i]
                if not group.on_time >> i & 1:
                    cost += _cents(group.penalty) * w_money + w_late
                row.append((cost, i))
                reach[i] += len(group.shipments)
            self.edges.append(row)
        self.share = [
            self.open_cost[i] // max(1, min(self.capacity[i], reach[i]))
            for i in range(d)
        ]
        # An option's departures open in order: moving sales from a later one
        # onto an earlier one of the same option is never worse. So opening
        # one opens those before it, and ruling one out rules out those after.
        self.earlier = [0] * d
        self.later = [0] * d
        for i in range(d):
            for j in range(d):
                if departures[j].option is departures[i].option:
                    if departures[j].departs_at <= departures[i].departs_at:
                        self.earlier[i] |= 1 << j
                    if departures[j].departs_at >= departures[i].departs_at:
                        self.later[i] |= 1 << j

    def _cost(self, g, opened, available):
        """Group ``g``'s departures in ``available`` with what a sale costs on each."""
        self.work += len(self.edges[g])
        return [
            (cost if opened >> i & 1 else cost + self.share[i], i)
            for cost, i in self.edges[g]
            if available >> i & 1
        ]

    def _assign(self, opened, available):
        """The best assignment onto ``available``: its value and its flows.

        Once there is a plan to fall back on, past the budget this stops the
        search (``_OutOfBudget``); a flow stops itself at the budget either way.
        """
        if self.best_value is not None and self.work > self.budget:
            raise _OutOfBudget
        flows, loads, value = {}, {}, 0
        costs = [self._cost(g, opened, available) for g in range(len(self.groups))]
        for g, group in enumerate(self.groups):
            size = len(group.shipments)
            if costs[g]:
                cost, i = min(costs[g])
                value += cost * size
                flows[(g, i)] = size
                loads[i] = loads.get(i, 0) + size
            else:
                value += self.unplaced * size
        if all(load <= self.capacity[i] for i, load in loads.items()):
            return value, flows
        return self._flow(costs, available)

    def _settled(self, bound, best):
        """Whether nothing under a node with ``bound`` can place more sales,
        cost less or be late less often than ``best``. Fewer departures and
        earlier arrivals only break ties; the search does not spend time
        proving them."""
        return bound // self.w_late >= best // self.w_late

    def _opened_cost(self, mask):
        return sum(c for i, c in enumerate(self.open_cost) if mask >> i & 1)

    def _keep(self, value, flows):
        if self.best_value is None or value < self.best_value:
            self.best_value, self.best_flows = value, flows

    def _incumbent(self):
        """A good plan to prune against from the first node: the departures
        the assignment onto everything uses, then dropping whichever one
        lowers the value most until none does."""
        everything = (1 << len(self.departures)) - 1
        _, flows = self._assign(everything, everything)
        mask = 0
        for _, i in flows:
            mask |= 1 << i
        value, flows = self._assign(mask, mask)
        self._keep(value + self._opened_cost(mask), flows)
        while True:
            step = None
            for i in range(len(self.departures)):
                if not mask >> i & 1:
                    continue
                trial = mask & ~(1 << i)
                value, flows = self._assign(trial, trial)
                value += self._opened_cost(trial)
                if value < self.best_value:
                    self._keep(value, flows)
                    step = trial
            if step is None:
                return
            mask = step

    def _greedy(self):
        """A plan without search, for when the budget runs out before any:
        the groups whose lateness costs most first, each sale on its cheapest
        departure with room."""
        room = list(self.capacity)
        flows = {}
        order = sorted(
            range(len(self.groups)),
            key=lambda g: (-self.groups[g].penalty, g),
        )
        for g in order:
            left = len(self.groups[g].shipments)
            for _, i in sorted(self.edges[g]):
                take = min(left, room[i])
                if take:
                    flows[(g, i)] = take
                    room[i] -= take
                    left -= take
        return flows

    def run(self):
        if not self.groups:
            return {}
        try:
            self._incumbent()
            self._search()
        except _OutOfBudget:
            self.exhaustive = False
        if self.best_flows is None:
            return self._greedy()
        return self.best_flows

    def _search(self):
        everything = (1 << len(self.departures)) - 1
        stack = [(0, everything, 0, 0)]
        while stack:
            opened, available, spent, floor = stack.pop()
            # A child's bound is never below its parent's.
            if self._settled(floor, self.best_value):
                continue
            value, flows = self._assign(opened, available)
            bound = spent + value
            if self._settled(bound, self.best_value):
                continue
            loads = {}
            for (_, i), amount in flows.items():
                loads[i] = loads.get(i, 0) + amount
            undecided = [i for i in loads if not opened >> i & 1]
            if not undecided:
                # Only opened departures carry sales, so this bound is the
                # value of opening just those, and opening more cannot lower it.
                self._keep(bound, flows)
                continue
            i = max(
                undecided,
                key=lambda i: (self.open_cost[i] - self.share[i] * loads[i], -i),
            )
            added = self.earlier[i] & ~opened
            stack.append((opened, available & ~self.later[i], spent, bound))
            stack.append(
                (opened | added, available, spent + self._opened_cost(added), bound)
            )

    def _flow(self, costs, available):
        """Min-cost flow from groups to the departures in ``available``, within
        capacities, every shipment left unplaced costing ``unplaced``."""
        graph = _Graph()
        source, sink = "s", "t"
        for g, group in enumerate(self.groups):
            size = len(group.shipments)
            graph.add(source, ("g", g), size, 0)
            graph.add(("g", g), sink, size, self.unplaced)
            for cost, i in costs[g]:
                graph.add(("g", g), ("d", i), size, cost)
        for i in range(len(self.departures)):
            if available >> i & 1:
                graph.add(("d", i), sink, self.capacity[i], 0)
        cost = graph.min_cost_flow(source, sink, self.total, self.budget - self.work)
        self.work += graph.work
        flows = {}
        for (u, v), amount in graph.flows().items():
            if u[0] == "g" and v[0] == "d" and amount:
                flows[(u[1], v[1])] = amount
        return cost, flows


class _Graph:
    """A residual graph for min-cost flow by successive shortest paths.

    Dijkstra on costs reduced by node potentials. Every cost starts at zero or
    more, and a node Dijkstra cannot reach stays unreachable (augmenting only
    changes edges between reachable nodes), so reduced costs stay non-negative.
    The sink is always reachable while flow is owed: each group keeps its
    unplaced edge to it, with room for the whole group.
    """

    def __init__(self):
        self.adjacent = {}
        self.edges = []
        self.work = 0

    def add(self, u, v, capacity, cost):
        for node in (u, v):
            self.adjacent.setdefault(node, [])
        self.adjacent[u].append(len(self.edges))
        self.edges.append([u, v, capacity, cost])
        self.adjacent[v].append(len(self.edges))
        self.edges.append([v, u, 0, -cost])

    def min_cost_flow(self, source, sink, amount, limit):
        """The cost of sending ``amount``; ``_OutOfBudget`` past ``limit`` work."""
        potential = dict.fromkeys(self.adjacent, 0)
        total = 0
        while amount:
            distance, via = {source: 0}, {}
            queue, counter = [(0, 0, source)], 0
            while queue:
                dist, _, u = heapq.heappop(queue)
                if dist > distance[u]:
                    continue
                base = dist + potential[u]
                self.work += len(self.adjacent[u])
                if self.work > limit:
                    raise _OutOfBudget
                for e in self.adjacent[u]:
                    _, v, capacity, cost = self.edges[e]
                    if capacity <= 0:
                        continue
                    reduced = base + cost - potential[v]
                    if v not in distance or reduced < distance[v]:
                        distance[v] = reduced
                        via[v] = e
                        counter += 1
                        heapq.heappush(queue, (reduced, counter, v))
            for node, dist in distance.items():
                potential[node] += dist
            path, v = [], sink
            while v != source:
                path.append(via[v])
                v = self.edges[via[v]][0]
            push = min([amount] + [self.edges[e][2] for e in path])
            for e in path:
                self.edges[e][2] -= push
                self.edges[e ^ 1][2] += push
                total += push * self.edges[e][3]
            amount -= push
        return total

    def flows(self):
        return {
            (u, v): self.edges[e ^ 1][2]
            for e, (u, v, _, _) in enumerate(self.edges)
            if e % 2 == 0
        }
