"""Getting a sale out of the door: preparation, packaging and dispatch.

A `Sale` extends one `orders.Order` with the time promised to the buyer. Its
channel is the shared `catalog.SalesChannel` also used by orders and invoices.
See docs/features/dispatch.md.
"""

from datetime import time

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

# Keep the old import path usable; Django registers the model only in Catalog.
from catalog.models import SalesChannel
from common.base import BaseOrgModel

WEEKDAYS = (
    ("MON", "Monday"),
    ("TUE", "Tuesday"),
    ("WED", "Wednesday"),
    ("THU", "Thursday"),
    ("FRI", "Friday"),
    ("SAT", "Saturday"),
    ("SUN", "Sunday"),
)

DELIVERY_KINDS = (
    ("DOOR_TO_DOOR", "Door to door"),
    ("CHANNEL_PICKUP", "Picked up by the channel"),
    ("CHANNEL_AGENCY", "Dropped at the channel's agency"),
    ("COMMON_DISPATCH", "Common dispatch"),
)
CHANNEL_KINDS = frozenset({"CHANNEL_PICKUP", "CHANNEL_AGENCY"})

WORK_STATUS = (("PENDING", "Pending"), ("COMPLETED", "Completed"))
DISPATCH_STATUS = (("OPEN", "Open"), ("CLOSED", "Closed"))
# How a dispatch order was made: by the plan over every ready sale, from sales
# a person chose, or from chosen sales with the rest of the run filled by the
# plan. Only AUTO orders are rewritten by a recalculation.
DISPATCH_MODES = (("AUTO", "Automatic"), ("MANUAL", "Manual"), ("HYBRID", "Hybrid"))
PINNED = ("MANUAL", "HYBRID")

# Inspection orders review returned products before they go back into
# inventory. The returns project owns them and their queue; the type lives here
# so teams can be given access to it alongside the others.
ORDER_TYPES = (
    ("PREPARATION", "Preparation"),
    ("INSPECTION", "Inspection"),
    ("PACKAGING", "Packaging"),
    ("DELIVERY", "Delivery"),
)
# The order types whose queues this app serves.
SERVED_TYPES = ("PREPARATION", "PACKAGING", "DELIVERY")
# Each level includes the ones before it. Closing fixes a dispatch order's
# money, so it is its own level, and only delivery has it.
ACCESS_LEVELS = (("VIEW", "View"), ("HANDLE", "Handle"), ("CLOSE", "Close"))

MAX_DISPATCH_MINUTES = 60 * 24 * 60


def _money(verbose_name, **kwargs):
    return models.DecimalField(
        verbose_name,
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        **kwargs,
    )


WORKDAY_FIELDS = tuple(f"{day}_opens" for day in (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
))  # fmt: skip


def _opens(day):
    return models.TimeField(
        null=True,
        blank=True,
        default=time(0),
        help_text=f"When a {day} opens for this option; blank when it does not work.",
    )


class DeliveryOption(BaseOrgModel):
    """One way a package can leave: who takes it, when, where and at what cost.

    A blank `weekday` or a null `departure_time` means any day or any time, and
    with neither the option leaves as soon as a package is ready. `cost` is paid
    once per departure and split evenly between the sales on it.

    Its runs leave on its own working days: the weekdays with an opening time
    (every day from midnight until changed), less its free days, plus its extra
    working days (`DeliveryOptionDay`).
    """

    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=DELIVERY_KINDS)
    channel = models.ForeignKey(
        SalesChannel,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="delivery_options",
        help_text="Only this channel's sales may use it. Blank serves every channel.",
    )
    location = models.CharField(max_length=255, blank=True, default="")
    weekday = models.CharField(max_length=3, choices=WEEKDAYS, blank=True, default="")
    departure_time = models.TimeField(null=True, blank=True)
    dispatch_minutes = models.PositiveIntegerField(
        default=0,
        validators=[MaxValueValidator(MAX_DISPATCH_MINUTES)],
        help_text="From departure until the committed delivery is fulfilled.",
    )
    cost = _money("Cost")
    capacity = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="Most packages one departure can take. Blank has no limit.",
    )
    is_active = models.BooleanField(default=True)
    monday_opens = _opens("Monday")
    tuesday_opens = _opens("Tuesday")
    wednesday_opens = _opens("Wednesday")
    thursday_opens = _opens("Thursday")
    friday_opens = _opens("Friday")
    saturday_opens = _opens("Saturday")
    sunday_opens = _opens("Sunday")

    class Meta:
        db_table = "delivery_option"
        ordering = ("name",)
        indexes = [models.Index(fields=["org", "-created_at"])]
        constraints = [
            models.CheckConstraint(
                condition=~Q(kind__in=sorted(CHANNEL_KINDS)) | Q(channel__isnull=False),
                name="delivery_option_channel_kind_has_channel",
            )
        ]

    def __str__(self):
        return self.name


DAY_KINDS = (("FREE", "Free"), ("WORKING", "Working"))


def _inherit_org(child, parent, label):
    if not child.org_id:
        child.org_id = parent.org_id
    elif child.org_id != parent.org_id:
        raise ValueError(f"{label}.org must match its parent's org")


class DeliveryOptionDay(BaseOrgModel):
    """A dated exception to an option's working week: a free day it does not
    run, or an extra working day it runs, opening at `opens_at`."""

    delivery_option = models.ForeignKey(
        DeliveryOption, on_delete=models.CASCADE, related_name="days"
    )
    date = models.DateField()
    kind = models.CharField(max_length=10, choices=DAY_KINDS)
    opens_at = models.TimeField(
        null=True,
        blank=True,
        help_text="For a working day: when it opens. Blank is midnight.",
    )
    note = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        db_table = "delivery_option_day"
        ordering = ("date",)
        indexes = [models.Index(fields=["org", "-created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["delivery_option", "date"],
                name="unique_delivery_option_day",
            ),
            models.CheckConstraint(
                condition=Q(kind="WORKING") | Q(opens_at__isnull=True),
                name="delivery_option_day_free_has_no_opening",
            ),
        ]

    def __str__(self):
        return f"{self.delivery_option} {self.kind} {self.date}"

    def save(self, *args, **kwargs):
        _inherit_org(self, self.delivery_option, "DeliveryOptionDay")
        super().save(*args, **kwargs)


class Sale(BaseOrgModel):
    """The fulfilment side of one order: its channel and its promised time."""

    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="sale"
    )
    channel = models.ForeignKey(
        SalesChannel, on_delete=models.PROTECT, related_name="sales"
    )
    committed_delivery_at = models.DateTimeField()

    class Meta:
        db_table = "sale"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["org", "-created_at"])]

    def __str__(self):
        return str(self.order)

    def save(self, *args, **kwargs):
        _inherit_org(self, self.order, "Sale")
        if self.channel.org_id != self.org_id:
            raise ValueError("Sale.channel must belong to the sale's org")
        if self.order.sales_channel_id not in (None, self.channel_id):
            raise ValueError("Sale.channel must match Order.sales_channel")
        if self.order.sales_channel_id is None:
            self.order.sales_channel = self.channel
            self.order.save(
                update_fields=["sales_channel", "updated_at", "updated_by"]
            )
        super().save(*args, **kwargs)


class PreparationOrder(BaseOrgModel):
    """Work to make a product ready (sewing, printing) before it can be packed."""

    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name="preparation_orders"
    )
    line_item = models.ForeignKey(
        "orders.OrderLineItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="preparation_orders",
    )
    description = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=WORK_STATUS, default="PENDING")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "preparation_order"
        ordering = ("created_at",)
        indexes = [models.Index(fields=["org", "-created_at"])]

    def __str__(self):
        return self.description

    def save(self, *args, **kwargs):
        _inherit_org(self, self.sale, "PreparationOrder")
        if self.line_item_id and self.line_item.order_id != self.sale.order_id:
            raise ValueError("PreparationOrder.line_item must be on the sale's order")
        super().save(*args, **kwargs)


class PackagingOrder(BaseOrgModel):
    """Packing the sale. A completed one makes the sale ready to dispatch."""

    sale = models.OneToOneField(
        Sale, on_delete=models.CASCADE, related_name="packaging_order"
    )
    status = models.CharField(max_length=20, choices=WORK_STATUS, default="PENDING")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "packaging_order"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["org", "-created_at"])]

    def __str__(self):
        return f"Packaging for {self.sale}"

    def save(self, *args, **kwargs):
        _inherit_org(self, self.sale, "PackagingOrder")
        super().save(*args, **kwargs)


class DispatchOrder(BaseOrgModel):
    """One departure of one delivery option and the sales leaving on it.

    Created on demand. An automatic one is rewritten by every recalculation,
    which may move sales in or out; a manual or hybrid one keeps its sales and
    run, and only its figures are recomputed. Closing it confirms it, computing
    the actual cost and each sale's share and penalty once more and fixing them.
    """

    delivery_option = models.ForeignKey(
        DeliveryOption, on_delete=models.PROTECT, related_name="dispatch_orders"
    )
    status = models.CharField(max_length=20, choices=DISPATCH_STATUS, default="OPEN")
    mode = models.CharField(max_length=10, choices=DISPATCH_MODES, default="AUTO")
    departs_at = models.DateTimeField()
    cost = _money("Cost")
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "dispatch_order"
        ordering = ("departs_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["org", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["delivery_option", "departs_at"],
                name="unique_dispatch_order_per_departure",
            )
        ]

    def __str__(self):
        return f"{self.delivery_option} at {self.departs_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        _inherit_org(self, self.delivery_option, "DispatchOrder")
        super().save(*args, **kwargs)


class DispatchOrderItem(BaseOrgModel):
    """A sale on a dispatch order. Its figures are estimates until the order closes."""

    dispatch_order = models.ForeignKey(
        DispatchOrder, on_delete=models.CASCADE, related_name="items"
    )
    sale = models.OneToOneField(
        Sale, on_delete=models.CASCADE, related_name="dispatch_item"
    )
    estimated_delivery_at = models.DateTimeField()
    late = models.BooleanField(default=False)
    cost_share = _money("Cost share")
    penalty = _money("Penalty")

    class Meta:
        db_table = "dispatch_order_item"
        ordering = ("created_at",)
        indexes = [models.Index(fields=["org", "-created_at"])]

    def __str__(self):
        return str(self.sale)

    def save(self, *args, **kwargs):
        _inherit_org(self, self.dispatch_order, "DispatchOrderItem")
        if self.sale.org_id != self.org_id:
            raise ValueError("DispatchOrderItem.sale must belong to the order's org")
        super().save(*args, **kwargs)


class QueueAssignment(BaseOrgModel):
    """A team's access to one order type's queue and its orders.

    ``VIEW`` sees the queue and its orders; ``HANDLE`` also works them
    (completes preparation and packaging orders, creates, recalculates and
    releases dispatch orders); ``CLOSE`` also closes dispatch orders.
    """

    team = models.ForeignKey(
        "common.Teams", on_delete=models.CASCADE, related_name="queue_assignments"
    )
    order_type = models.CharField(max_length=20, choices=ORDER_TYPES)
    access = models.CharField(max_length=10, choices=ACCESS_LEVELS, default="HANDLE")

    class Meta:
        db_table = "queue_assignment"
        ordering = ("order_type", "created_at")
        indexes = [models.Index(fields=["org", "-created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "order_type"], name="unique_queue_assignment_per_team"
            ),
            models.CheckConstraint(
                condition=~Q(access="CLOSE") | Q(order_type="DELIVERY"),
                name="queue_assignment_close_is_delivery_only",
            ),
        ]

    def __str__(self):
        return f"{self.team} {self.access} {self.order_type}"

    def save(self, *args, **kwargs):
        _inherit_org(self, self.team, "QueueAssignment")
        super().save(*args, **kwargs)
