"""Stock, work orders and returns for channel sales.

Stock lives on the catalog product (`Product.stock_quantity`), shared by every
channel. Each item still to be shipped is planned once: it takes what stock it
can, one unit per unit sold (`plan_line`), and records that as
`stock_allocated`, the units physically set aside for it.

Work orders belong to the sale and are kept in step with its items by
`sync_work_orders`:

* one PACKAGING order per sale, for all its units still to be shipped;
* one PREPARATION order per product whose items stock does not cover, for
  the units short, whatever the quantity.

A finished preparation sets the units it made aside for the items it was for.
Releasing an item (cancelled, or changed before shipping) puts everything set
aside for it back in stock. A packaging order cannot be finished while any
preparation order of its sale is open.

Goods that come back after shipping get an INSPECTION work order, which can
take days: nothing goes back in stock until someone accepts it.

Callers run these inside a transaction; the product row is locked while its
stock is read and written, since two channels can sell it at once.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.db.models.functions import Coalesce
from django.utils import timezone

from business_hours.calendar import get_default_calendar
from invoices.models import Product
from orders.models import (
    ChannelPreference,
    Order,
    OrderLineItem,
    WorkOrder,
)

ZERO = Decimal("0")
_MONDAY_TO_FRIDAY = frozenset(range(5))


# ---------------------------------------------------------------------------
# Due dates
# ---------------------------------------------------------------------------


def day_counter(org_id, channel: str):
    """A function `(start, days) -> date` counting days as the channel's
    preference says: calendar days, or working days. Working days are the
    org's default business calendar's open weekdays less its holidays, or
    Monday to Friday when it has no calendar."""
    if ChannelPreference.day_count(channel) != "WORKING":
        return lambda start, days: start + datetime.timedelta(days=days)

    weekdays, holidays = _MONDAY_TO_FRIDAY, frozenset()
    calendar = get_default_calendar(org_id)
    if calendar is not None:
        open_days = frozenset(
            weekday
            for weekday, (opens, closes) in enumerate(calendar.windows_by_weekday())
            if opens is not None and closes is not None and closes > opens
        )
        if open_days:
            weekdays = open_days
            holidays = frozenset(h.date for h in calendar.holidays.all())

    def add_working_days(start: datetime.date, days: int) -> datetime.date:
        current = start
        while days > 0:
            current += datetime.timedelta(days=1)
            if current.weekday() in weekdays and current not in holidays:
                days -= 1
        return current

    return add_working_days


def ready_date(order_date, product: Product | None, add_days):
    """The sale date plus the product's availability time, 0 when unknown."""
    if order_date is None:
        return None
    days = (product.availability_days or 0) if product else 0
    return add_days(order_date, days)


def order_due_date(items) -> datetime.date | None:
    """The latest due date of an order's items that were not cancelled, or of
    all of them when every item was. Returned items count: they were
    delivered, and penalties for late delivery are computed from this date."""
    dated = [item for item in items if item.due_date]
    live = [item.due_date for item in dated if item.fulfillment_status != "CANCELLED"]
    return max(live or [item.due_date for item in dated], default=None)


def recalculate_due_dates(order: Order, add_days) -> bool:
    """Set an order's due dates again from the channel's current preference
    and its products' current availability time: items, the order, and its
    open preparation and packaging orders. Returns whether anything moved."""
    changed = False
    items = list(order.line_items.select_related("product"))
    for item in items:
        due = ready_date(order.order_date, item.product, add_days)
        if due != item.due_date:
            item.due_date = due
            item.save(update_fields=["due_date", "updated_at"])
            changed = True
    due = order_due_date(items)
    if due != order.due_date:
        order.due_date = due
        order.save(update_fields=["due_date", "updated_at"])
        changed = True
    for work_order in order.work_orders.filter(
        status="PENDING", kind__in=("PREPARATION", "PACKAGING")
    ):
        if work_order.kind == "PACKAGING":
            due = order.due_date
        else:
            what = _what_of(work_order)
            due = max(
                (i.due_date for i in items if i.due_date and _what(i) == what),
                default=None,
            )
        if due != work_order.due_date:
            work_order.due_date = due
            work_order.save(update_fields=["due_date", "updated_at"])
            changed = True
    return changed


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------


def _locked_product(product_id) -> Product | None:
    if not product_id:
        return None
    return Product.objects.select_for_update().filter(pk=product_id).first()


def _add_stock(product_id, units: Decimal) -> None:
    Product.objects.filter(pk=product_id).update(
        stock_quantity=Coalesce("stock_quantity", ZERO) + units
    )


def plan_line(line: OrderLineItem) -> Decimal:
    """Take stock for an item still to be shipped. Returns the units taken."""
    product = _locked_product(line.product_id)
    on_hand = max(product.stock_quantity or ZERO, ZERO) if product else ZERO
    taken = min(on_hand, line.quantity)
    if taken:
        _add_stock(product.pk, -taken)
    line.stock_allocated = taken
    line.fulfillment_planned = True
    line.save(update_fields=["stock_allocated", "fulfillment_planned", "updated_at"])
    return taken


def release_line(line: OrderLineItem) -> Decimal:
    """Put everything set aside for an item back in stock. Returns the units.
    Its sale's work orders follow on the next `sync_work_orders`."""
    restocked = line.stock_allocated
    if restocked and _locked_product(line.product_id):
        _add_stock(line.product_id, restocked)
    line.stock_allocated = ZERO
    line.fulfillment_planned = False
    line.save(update_fields=["stock_allocated", "fulfillment_planned", "updated_at"])
    return restocked


# ---------------------------------------------------------------------------
# Work orders
# ---------------------------------------------------------------------------


def _what(line: OrderLineItem):
    """What a preparation is for: the product, or the item when it has none."""
    return ("product", line.product_id) if line.product_id else ("item", line.pk)


def _what_of(work_order: WorkOrder):
    if work_order.product_id:
        return ("product", work_order.product_id)
    return ("item", work_order.line_item_id)


def sync_work_orders(order: Order) -> list[WorkOrder]:
    """Bring a sale's work orders in line with its items. Returns the work
    orders it created."""
    planned = [line for line in order.line_items.all() if line.fulfillment_planned]
    to_ship = [line for line in planned if line.fulfillment_status == "PENDING"]
    shipped = [
        line for line in planned if line.fulfillment_status in ("SHIPPED", "DELIVERED")
    ]
    pending = list(order.work_orders.filter(status="PENDING", kind="PREPARATION"))
    created = []

    # Preparation: one open order per product still short.
    short = defaultdict(lambda: ZERO)
    due = {}
    for line in to_ship:
        what = _what(line)
        short[what] += line.quantity - line.stock_allocated
        if line.due_date and (what not in due or line.due_date > due[what]):
            due[what] = line.due_date
    shipped_what = {_what(line) for line in shipped}
    preparing = {}
    for work_order in pending:
        what = _what_of(work_order)
        if short[what] > 0 and what not in preparing:
            preparing[what] = work_order
        elif what in shipped_what:
            # It left the building, so the work got done.
            complete_work_order(work_order)
        else:
            _cancel(work_order)
    for what, units in short.items():
        if units <= 0:
            continue
        work_order = preparing.get(what)
        if work_order is None:
            created.append(
                _create(order, "PREPARATION", units, due.get(what), what, to_ship)
            )
        elif work_order.quantity != units:
            work_order.quantity = units
            work_order.save(update_fields=["quantity", "updated_at"])

    # Packaging: one per sale, for every unit still to be shipped.
    packaging = order.work_orders.filter(kind="PACKAGING").exclude(status="CANCELLED")
    open_packaging = packaging.filter(status="PENDING").first()
    if to_ship:
        units = sum((line.quantity for line in to_ship), ZERO)
        if open_packaging is not None:
            if open_packaging.quantity != units:
                open_packaging.quantity = units
                open_packaging.save(update_fields=["quantity", "updated_at"])
        elif not packaging.exists():
            created.append(
                _create(order, "PACKAGING", units, order.due_date, None, to_ship)
            )
    elif open_packaging is not None:
        if shipped:
            complete_work_order(open_packaging)
        else:
            _cancel(open_packaging)
    return created


def _create(order, kind, quantity, due_date, what, lines):
    product_id = line_item = None
    if what is not None and what[0] == "product":
        product_id = what[1]
    elif what is not None:
        line_item = next(line for line in lines if line.pk == what[1])
    return WorkOrder.objects.create(
        org_id=order.org_id,
        order=order,
        line_item=line_item,
        product_id=product_id,
        kind=kind,
        quantity=quantity,
        due_date=due_date,
    )


def _cancel(work_order: WorkOrder) -> None:
    work_order.status = "CANCELLED"
    work_order.save(update_fields=["status", "updated_at"])


def why_not_completable(work_order: WorkOrder) -> str | None:
    """Why a work order cannot be marked done yet, or None."""
    if work_order.status != "PENDING":
        return f"It is already {work_order.get_status_display().lower()}."
    if work_order.kind == "INSPECTION":
        return "A return inspection is accepted or rejected, not marked done."
    if work_order.kind == "PACKAGING":
        open_preparations = WorkOrder.objects.filter(
            order_id=work_order.order_id, kind="PREPARATION", status="PENDING"
        ).count()
        if open_preparations:
            return (
                f"{open_preparations} preparation order(s) of the sale are still open."
            )
    return None


def complete_work_order(work_order: WorkOrder) -> bool:
    """Mark a pending work order done, if `why_not_completable` allows it. A
    finished preparation sets the units it made aside for the items it was
    for. Returns whether it was done."""
    if why_not_completable(work_order):
        return False
    work_order.status = "DONE"
    work_order.save(update_fields=["status", "updated_at"])
    if work_order.kind != "PREPARATION":
        return True
    made = work_order.quantity
    what = _what_of(work_order)
    lines = work_order.order.line_items.filter(fulfillment_planned=True).order_by(
        "sort_order", "created_at"
    )
    for line in lines:
        if made <= 0:
            break
        if _what(line) != what:
            continue
        units = min(made, line.quantity - line.stock_allocated)
        if units > 0:
            line.stock_allocated += units
            line.save(update_fields=["stock_allocated", "updated_at"])
            made -= units
    return True


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------


def open_return(line: OrderLineItem) -> tuple[WorkOrder, bool]:
    """The INSPECTION work order for a shipped item that came back; one per
    item. Opening it is all the import does: the inspection itself can take
    days.

    The item's units left with the shipment, so it no longer holds any: a
    later import must not release them back into stock behind the inspection.
    """
    if line.fulfillment_planned:
        line.stock_allocated = ZERO
        line.fulfillment_planned = False
        line.save(
            update_fields=["stock_allocated", "fulfillment_planned", "updated_at"]
        )
    return WorkOrder.objects.get_or_create(
        line_item=line,
        kind="INSPECTION",
        defaults={
            "org_id": line.org_id,
            "order_id": line.order_id,
            "product_id": line.product_id,
            "quantity": line.quantity,
            "restock_quantity": line.quantity,
        },
    )


def accept_return(inspection: WorkOrder, user) -> bool:
    """Put the inspected units fit for sale back in stock. Returns False if
    the inspection was already decided."""
    return _decide_return(inspection, user, "ACCEPTED")


def reject_return(inspection: WorkOrder, user) -> bool:
    """Close the inspection without restocking anything."""
    return _decide_return(inspection, user, "REJECTED")


def _decide_return(inspection: WorkOrder, user, outcome: str) -> bool:
    current = WorkOrder.objects.select_for_update().get(pk=inspection.pk)
    if current.kind != "INSPECTION" or current.status != "PENDING":
        return False
    # Actions on the list bypass the form's validation, so bound it here too.
    units = min(max(inspection.restock_quantity or ZERO, ZERO), current.quantity)
    inspection.restock_quantity = units
    if outcome == "ACCEPTED" and units and current.product_id:
        _locked_product(current.product_id)
        _add_stock(current.product_id, units)
    inspection.status = "DONE"
    inspection.outcome = outcome
    inspection.completed_at = timezone.now()
    inspection.completed_by = user
    inspection.save(
        update_fields=[
            "status",
            "outcome",
            "restock_quantity",
            "notes",
            "completed_at",
            "completed_by",
            "updated_at",
        ]
    )
    return True
