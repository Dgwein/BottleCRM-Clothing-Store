"""Every write to a sale's fulfilment goes through here, from the API and the admin.

Each transition checks the state it starts from and raises ``Refused`` with a
sentence for the caller when it cannot happen.
"""

from dataclasses import dataclass
from datetime import time, timedelta
from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from common.org_time import org_timezone
from fulfillment import planner
from fulfillment.models import (
    PINNED,
    WORKDAY_FIELDS,
    DeliveryOption,
    DeliveryOptionDay,
    DispatchOrder,
    DispatchOrderItem,
    PackagingOrder,
    PreparationOrder,
    Sale,
)

CANCELLED_ORDER = "The order was cancelled, so it is not fulfilled."


class Refused(Exception):
    pass


def register_sale(order, channel, committed_delivery_at, preparations=()):
    """A sale for ``order`` with its packaging order and a preparation order
    for each ``{"description", "line_item"}`` in ``preparations``."""
    with transaction.atomic():
        sale = Sale.objects.create(
            org=order.org,
            order=order,
            channel=channel,
            committed_delivery_at=committed_delivery_at,
        )
        open_packaging(sale)
        for item in preparations:
            PreparationOrder.objects.create(
                org=sale.org,
                sale=sale,
                description=item["description"],
                line_item=item.get("line_item"),
            )
    return sale


def open_packaging(sale):
    """The sale's packaging order, created if it has none yet."""
    packaging, _ = PackagingOrder.objects.get_or_create(
        sale=sale, defaults={"org": sale.org}
    )
    return packaging


def complete_preparation(preparation):
    with transaction.atomic():
        locked = PreparationOrder.objects.select_for_update().get(pk=preparation.pk)
        if locked.status != "PENDING":
            raise Refused("This preparation order is already completed.")
        locked.status = "COMPLETED"
        locked.completed_at = timezone.now()
        locked.save(
            update_fields=["status", "completed_at", "updated_at", "updated_by"]
        )
    return locked


def complete_packaging(packaging):
    """Mark the sale packed, which makes it ready to dispatch.

    Refused while a preparation order is pending: the product is not ready.
    """
    with transaction.atomic():
        locked = (
            PackagingOrder.objects.select_for_update()
            .select_related("sale__order")
            .get(pk=packaging.pk)
        )
        if locked.status != "PENDING":
            raise Refused("This packaging order is already completed.")
        if locked.sale.order.status == "CANCELLED":
            raise Refused(CANCELLED_ORDER)
        waiting = locked.sale.preparation_orders.filter(status="PENDING").count()
        if waiting:
            raise Refused(
                f"{waiting} preparation order(s) are still pending. "
                "Complete them before packing."
            )
        locked.status = "COMPLETED"
        locked.completed_at = timezone.now()
        locked.save(
            update_fields=["status", "completed_at", "updated_at", "updated_by"]
        )
    return locked


def _lock_dispatch(org):
    """Serialize creating, recalculating, releasing and closing an org's
    dispatch orders until the transaction ends. PostgreSQL only; SQLite runs one
    writer anyway."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", [f"dispatch:{org.id}"]
            )


def ready_sales(org):
    """Packed sales not yet on a closed dispatch order, oldest packed first."""
    return (
        Sale.objects.filter(org=org, packaging_order__status="COMPLETED")
        .exclude(order__status="CANCELLED")
        .exclude(dispatch_item__dispatch_order__status="CLOSED")
        .select_related("order", "channel", "packaging_order")
        .order_by("packaging_order__completed_at", "id")
    )


def plannable_sales(org):
    """Ready sales the plan may place: all but those a person put on an open
    manual or hybrid dispatch order."""
    return ready_sales(org).exclude(dispatch_item__dispatch_order__mode__in=PINNED)


def option_workdays(option, tz):
    """The option's own working days: its weekly opening times and its dated
    free and working days. ``option.days`` is read whole, so prefetch it."""
    return planner.Workdays(
        tz=tz,
        opening=tuple(getattr(option, name) for name in WORKDAY_FIELDS),
        exceptions={
            day.date: (day.opens_at or time(0)) if day.kind == "WORKING" else None
            for day in option.days.all()
        },
    )


def _as_option(option, workdays, **changes):
    fields = {
        "id": option.id,
        "cost": option.cost,
        "channel_id": option.channel_id,
        "weekday": option.weekday,
        "departure_time": option.departure_time,
        "dispatch_minutes": option.dispatch_minutes,
        "capacity": option.capacity,
        "workdays": workdays,
    }
    return planner.Option(**{**fields, **changes})


def _shipment(sale):
    return planner.Shipment(
        id=sale.id,
        channel_id=sale.channel_id,
        committed_at=sale.committed_delivery_at,
        penalty=sale.channel.late_penalty,
    )


def org_zone(org):
    with org_timezone(org):
        return timezone.get_current_timezone()


def import_business_hours(option, calendar):
    """Copy the business hours ``calendar`` onto a new option: each weekday's
    opening time (a day without hours becomes a day off) and every holiday from
    today on as a free day. From then on the option's days are its own."""
    for name, (open_, close) in zip(WORKDAY_FIELDS, calendar.windows_by_weekday()):
        valid = open_ is not None and close is not None and close > open_
        setattr(option, name, open_ if valid else None)
    option.save(update_fields=[*WORKDAY_FIELDS, "updated_at", "updated_by"])
    today = timezone.localdate(timezone=org_zone(option.org))
    for holiday in calendar.holidays.filter(date__gte=today):
        DeliveryOptionDay.objects.get_or_create(
            delivery_option=option,
            date=holiday.date,
            defaults={"org": option.org, "kind": "FREE", "note": holiday.name[:100]},
        )


@dataclass
class _Context:
    """What planning reads, once per request."""

    now: object
    tz: object
    options: dict
    candidates: dict
    taken: dict


def _context(org, allow_non_working_days):
    now = timezone.now()
    tz = org_zone(org)
    options = {
        o.id: o
        for o in DeliveryOption.objects.filter(org=org, is_active=True)
        .prefetch_related("days")
        .order_by()
    }
    # A run is taken once closed, or while a person's order holds it.
    taken = {}
    rows = (
        DispatchOrder.objects.filter(org=org, delivery_option_id__in=list(options))
        .filter(
            Q(status="CLOSED", departs_at__gte=now - timedelta(days=1))
            | Q(status="OPEN", mode__in=PINNED)
        )
        .values_list("delivery_option_id", "departs_at")
    )
    for option_id, departs_at in rows:
        taken.setdefault(option_id, set()).add(planner.trip_key(departs_at, tz))
    return _Context(
        now=now,
        tz=tz,
        options=options,
        candidates={
            o.id: _as_option(
                o, None if allow_non_working_days else option_workdays(o, tz)
            )
            for o in options.values()
        },
        taken=taken,
    )


@dataclass
class DispatchPlan:
    """A computed plan and the rows it was computed over, for the response."""

    plan: planner.Plan
    generated_at: object
    sales: dict
    options: dict
    tz: object


def compute_plan(org, allow_non_working_days=False):
    """The plan for every sale the plan may place, from the current values.
    Writes nothing."""
    ctx = _context(org, allow_non_working_days)
    sales = list(plannable_sales(org))
    result = planner.plan(
        [_shipment(sale) for sale in sales],
        list(ctx.candidates.values()),
        ctx.now,
        ctx.tz,
        closed=ctx.taken,
    )
    return DispatchPlan(
        plan=result,
        generated_at=ctx.now,
        sales={sale.id: sale for sale in sales},
        options=ctx.options,
        tz=ctx.tz,
    )


ITEM_FIGURES = ["estimated_delivery_at", "late", "cost_share", "penalty"]
ORDER_FIGURES = ["departs_at", "cost", "updated_at", "updated_by"]


def _refigure(order, departs):
    """Recompute ``order``'s figures from the current values, leaving at
    ``departs``: the option's cost split evenly between its sales, and each
    late sale's channel penalty. Saves the items; the caller saves the order."""
    option = order.delivery_option
    items = list(
        order.items.select_related("sale__channel").order_by(
            "sale__packaging_order__completed_at", "sale_id"
        )
    )
    arrives = departs + timedelta(minutes=option.dispatch_minutes)
    for item, share in zip(items, planner.split_evenly(option.cost, len(items))):
        item.estimated_delivery_at = arrives
        item.late = arrives > item.sale.committed_delivery_at
        item.cost_share = share
        item.penalty = item.sale.channel.late_penalty if item.late else 0
        item.save(update_fields=[*ITEM_FIGURES, "updated_at", "updated_by"])
    order.departs_at = departs
    order.cost = option.cost
    return items


def _drop_cancelled(order):
    """Take off the sales whose order was cancelled; False when none is left."""
    order.items.filter(sale__order__status="CANCELLED").delete()
    return order.items.exists()


def create_dispatch_orders(org, allow_non_working_days=False):
    """Recalculate the plan from the current values and write it as the org's
    open automatic dispatch orders: one per run used, reusing the open order of
    the same run, so sales join, move and leave them. An automatic order the
    plan no longer uses is deleted, and a sale the plan does not place (it
    cannot, or the sale is no longer ready) is taken off its automatic order.
    Manual and hybrid orders keep their sales and runs; their figures are
    recomputed from the current values."""
    with transaction.atomic():
        _lock_dispatch(org)
        return _recalculate(org, allow_non_working_days)


def _recalculate(org, allow_non_working_days):
    """``create_dispatch_orders`` without its transaction and lock, for a caller
    already holding both."""
    computed = compute_plan(org, allow_non_working_days)
    open_orders = {}
    for order in DispatchOrder.objects.select_for_update().filter(
        org=org, status="OPEN", mode="AUTO"
    ):
        # An order on an option switched off since matches no trip.
        live = order.delivery_option_id in computed.options
        key = (
            order.delivery_option_id,
            planner.trip_key(order.departs_at, computed.tz) if live else order.pk,
        )
        open_orders[key] = order

    trips = {}
    for a in computed.plan.assignments:
        key = (a.option_id, planner.trip_key(a.departs_at, computed.tz))
        trips.setdefault(key, []).append(a)

    # Stale orders go first, so a reused or new one never meets one on
    # the same departure; their sales' items are written again below.
    stale = [o.pk for key, o in open_orders.items() if key not in trips]
    DispatchOrder.objects.filter(pk__in=stale).delete()
    for key, assignments in trips.items():
        option = computed.options[key[0]]
        order = open_orders.get(key) or DispatchOrder(org=org, delivery_option=option)
        order.departs_at = assignments[0].departs_at
        order.cost = option.cost
        order.save()
        for a in assignments:
            DispatchOrderItem.objects.update_or_create(
                sale_id=a.shipment_id,
                defaults={
                    "org": org,
                    "dispatch_order": order,
                    "estimated_delivery_at": a.arrives_at,
                    "late": a.late,
                    "cost_share": a.cost_share,
                    "penalty": a.penalty,
                },
            )
    # An automatic order holds exactly what the plan placed: this takes
    # off sales it could not place and sales no longer ready (cancelled).
    placed = [a.shipment_id for a in computed.plan.assignments]
    DispatchOrderItem.objects.filter(
        org=org, dispatch_order__status="OPEN", dispatch_order__mode="AUTO"
    ).exclude(sale_id__in=placed).delete()

    for order in (
        DispatchOrder.objects.select_for_update()
        .filter(org=org, status="OPEN", mode__in=PINNED)
        .select_related("delivery_option")
    ):
        if not _drop_cancelled(order):
            order.delete()
            continue
        _refigure(order, order.departs_at)
        order.save(update_fields=ORDER_FIGURES)
    return computed


FILL = "fill"


def create_manual_dispatch_order(
    org, sales, option=None, fill=False, allow_non_working_days=False
):
    """A dispatch order for the ``sales`` a person chose, all on one run.

    With ``option`` the run is that option's next free one; without, the one
    run that carries them all at the lowest cost and penalties. With ``fill``
    (hybrid) the plan then fills the run's remaining capacity with the other
    ready sales it is best for, the run's cost being paid already. An automatic
    order on the same run gives it up, and its other sales are planned again at
    once (a recalculation of the automatic orders, keeping to working days).
    Chosen sales leave the automatic orders they were on, whose cost is split
    again between who is left.
    """
    with transaction.atomic():
        _lock_dispatch(org)
        plannable = {sale.id: sale for sale in plannable_sales(org)}
        waiting = [sale for sale in sales if sale.id not in plannable]
        if waiting:
            names = ", ".join(sale.order.name for sale in waiting)
            raise Refused(
                f"These sales are not ready to dispatch, or another manual "
                f"dispatch order holds them: {names}."
            )
        ctx = _context(org, allow_non_working_days)
        if option is not None and option.id not in ctx.candidates:
            raise Refused("This delivery option is switched off.")
        candidates = (
            [ctx.candidates[option.id]]
            if option is not None
            else list(ctx.candidates.values())
        )
        run = planner.single_run(
            [_shipment(plannable[sale.id]) for sale in sales],
            candidates,
            ctx.now,
            ctx.tz,
            ctx.taken,
        )
        if run is None:
            raise Refused(
                "No active delivery option can take all the chosen sales on one "
                "run: it must serve all their channels, have room for all of "
                "them and a run available."
            )
        chosen, departs, _ = run
        row_option = ctx.options[chosen.id]
        trip = planner.trip_key(departs, ctx.tz)
        displaced = False
        for auto in DispatchOrder.objects.select_for_update().filter(
            org=org, status="OPEN", mode="AUTO", delivery_option=row_option
        ):
            if planner.trip_key(auto.departs_at, ctx.tz) == trip:
                displaced = (
                    displaced
                    or auto.items.exclude(
                        sale_id__in=[sale.id for sale in sales]
                    ).exists()
                )
                auto.delete()

        members = [sale.id for sale in sales]
        if fill:
            members += _fill(ctx, plannable, members, row_option, departs, trip)

        order = DispatchOrder.objects.create(
            org=org,
            delivery_option=row_option,
            mode="HYBRID" if fill else "MANUAL",
            departs_at=departs,
            cost=row_option.cost,
        )
        left_behind = set(
            DispatchOrder.objects.filter(
                org=org, status="OPEN", mode="AUTO", items__sale_id__in=members
            ).values_list("pk", flat=True)
        )
        for sale_id in members:
            DispatchOrderItem.objects.update_or_create(
                sale_id=sale_id,
                defaults={
                    "org": org,
                    "dispatch_order": order,
                    "estimated_delivery_at": departs,
                },
            )
        _refigure(order, departs)
        order.save(update_fields=ORDER_FIGURES)
        for other in DispatchOrder.objects.filter(pk__in=left_behind).select_related(
            "delivery_option"
        ):
            if not other.items.exists():
                other.delete()
                continue
            _refigure(other, other.departs_at)
            other.save(update_fields=ORDER_FIGURES)
        if displaced:
            _recalculate(org, allow_non_working_days=False)
    return order


def _fill(ctx, plannable, members, option, departs, trip):
    """The other sales the plan puts on the chosen run's remaining room."""
    room = None if option.capacity is None else option.capacity - len(members)
    if room is not None and room <= 0:
        return []
    seat = _as_option(
        option,
        None,
        id=FILL,
        cost=Decimal("0"),
        capacity=room,
        fixed_departure=departs,
    )
    taken = {k: set(v) for k, v in ctx.taken.items()}
    taken.setdefault(option.id, set()).add(trip)
    chosen = set(members)
    result = planner.plan(
        [_shipment(s) for i, s in plannable.items() if i not in chosen],
        [*ctx.candidates.values(), seat],
        ctx.now,
        ctx.tz,
        closed=taken,
    )
    return [a.shipment_id for a in result.assignments if a.option_id == FILL]


def release_dispatch_order(dispatch_order):
    """Undo an open dispatch order: its sales are ready again."""
    with transaction.atomic():
        _lock_dispatch(dispatch_order.org)
        locked = DispatchOrder.objects.select_for_update().get(pk=dispatch_order.pk)
        if locked.status != "OPEN":
            raise Refused("A closed dispatch order cannot be released.")
        locked.delete()


def close_dispatch_order(dispatch_order):
    """Confirm the dispatch order: compute its actual figures from the current
    values and fix them.

    An option with a delivery time closes once that time has passed, and its
    departure is that time; one without closes when it is executed, now. The
    cost is the option's cost now, split evenly between its sales, and each
    late sale pays its channel's penalty now. A sale whose order was cancelled
    is taken off first.
    """
    org = dispatch_order.org
    with transaction.atomic():
        _lock_dispatch(org)
        locked = (
            DispatchOrder.objects.select_for_update()
            .select_related("delivery_option")
            .get(pk=dispatch_order.pk)
        )
        if locked.status != "OPEN":
            raise Refused("This dispatch order is already closed.")
        option = locked.delivery_option
        if not option.is_active:
            raise Refused(
                "Its delivery option was switched off. "
                "Recalculate the dispatch orders first."
            )
        now = timezone.now()
        if option.departure_time is not None and now < locked.departs_at:
            local = timezone.localtime(locked.departs_at, org_zone(org))
            raise Refused(
                f"This departure leaves at {local:%Y-%m-%d %H:%M}. "
                "It can be closed once that time has passed."
            )
        if not _drop_cancelled(locked):
            raise Refused("No sales are left on this dispatch order.")
        count = locked.items.count()
        if option.capacity is not None and count > option.capacity:
            raise Refused(
                f"Its delivery option now takes {option.capacity} package(s) and "
                f"this order has {count}. Recalculate the dispatch orders first."
            )
        departs = locked.departs_at if option.departure_time is not None else now
        _refigure(locked, departs)
        locked.status = "CLOSED"
        locked.closed_at = now
        locked.save(update_fields=[*ORDER_FIGURES, "status", "closed_at"])
    return locked
