"""The queues this app serves: the orders their teams can act on now. A
packaging order waits outside its queue until the sale's preparations are done.
The inspection queue belongs to the returns project."""

from django.db.models import Q

from common.validators import choice_list_param, date_param, uuid_list_param
from fulfillment.models import (
    DISPATCH_MODES,
    DispatchOrder,
    DispatchOrderItem,
    PackagingOrder,
    PreparationOrder,
)
from fulfillment.services import ready_sales

WORK_MODELS = {
    "PREPARATION": PreparationOrder,
    "PACKAGING": PackagingOrder,
}


def work_queue(org, order_type):
    rows = (
        WORK_MODELS[order_type]
        .objects.filter(org=org, status="PENDING")
        .exclude(sale__order__status="CANCELLED")
    )
    if order_type == "PACKAGING":
        rows = rows.exclude(sale__preparation_orders__status="PENDING")
    return rows.select_related("sale__order", "sale__channel")


def open_dispatch_orders(org):
    return DispatchOrder.objects.filter(org=org, status="OPEN")


def unplanned_sales(org):
    """Ready sales not on any dispatch order yet."""
    return ready_sales(org).filter(dispatch_item__isnull=True)


def pending(org, order_type):
    if order_type == "DELIVERY":
        return open_dispatch_orders(org).count()
    return work_queue(org, order_type).count()


WORK_ORDERING = {
    "due": ("sale__committed_delivery_at", "created_at", "id"),
    "-due": ("-sale__committed_delivery_at", "-created_at", "-id"),
    "created": ("created_at", "id"),
    "-created": ("-created_at", "-id"),
}
DISPATCH_ORDERING = {
    "departs": ("departs_at", "id"),
    "-departs": ("-departs_at", "-id"),
}


def _ordering(params, choices, default):
    picked = choice_list_param(params, "ordering", choices)
    return choices[picked[0] if picked else default]


def filter_work(rows, params):
    """Narrow a preparation or packaging queue by the query parameters:
    ``channel`` (repeatable), ``due_from`` and ``due_to`` (the committed
    delivery's day, inclusive), ``search`` (order name or number) and
    ``ordering`` (``due``, the default, ``-due``, ``created``, ``-created``)."""
    channels = uuid_list_param(params, "channel")
    if channels:
        rows = rows.filter(sale__channel_id__in=channels)
    due_from = date_param(params, "due_from")
    if due_from:
        rows = rows.filter(sale__committed_delivery_at__date__gte=due_from)
    due_to = date_param(params, "due_to")
    if due_to:
        rows = rows.filter(sale__committed_delivery_at__date__lte=due_to)
    search = (params.get("search") or "").strip()
    if search:
        rows = rows.filter(
            Q(sale__order__name__icontains=search)
            | Q(sale__order__order_number__icontains=search)
        )
    return rows.order_by(*_ordering(params, WORK_ORDERING, "due"))


def filter_dispatch(rows, params):
    """Narrow dispatch orders by the query parameters: ``delivery_option``,
    ``channel`` (orders carrying a sale of it) and ``mode`` (each repeatable),
    ``departs_from`` and ``departs_to`` (the departure's day, inclusive),
    ``search`` (a carried order's name or number) and ``ordering``
    (``departs``, the default, or ``-departs``)."""
    options = uuid_list_param(params, "delivery_option")
    if options:
        rows = rows.filter(delivery_option_id__in=options)
    channels = uuid_list_param(params, "channel")
    if channels:
        carrying = DispatchOrderItem.objects.filter(sale__channel_id__in=channels)
        rows = rows.filter(pk__in=carrying.values("dispatch_order"))
    modes = choice_list_param(params, "mode", [m for m, _ in DISPATCH_MODES])
    if modes:
        rows = rows.filter(mode__in=modes)
    departs_from = date_param(params, "departs_from")
    if departs_from:
        rows = rows.filter(departs_at__date__gte=departs_from)
    departs_to = date_param(params, "departs_to")
    if departs_to:
        rows = rows.filter(departs_at__date__lte=departs_to)
    search = (params.get("search") or "").strip()
    if search:
        carrying = DispatchOrderItem.objects.filter(
            Q(sale__order__name__icontains=search)
            | Q(sale__order__order_number__icontains=search)
        )
        rows = rows.filter(pk__in=carrying.values("dispatch_order"))
    return rows.order_by(*_ordering(params, DISPATCH_ORDERING, "departs"))
