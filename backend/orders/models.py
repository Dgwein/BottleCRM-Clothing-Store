from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.base import BaseModel, BaseOrgModel
from common.utils import COUNTRIES

# =============================================================================
# CONSTANTS
# =============================================================================

ORDER_STATUS = (
    ("DRAFT", "Draft"),
    ("ACTIVATED", "Activated"),
    ("COMPLETED", "Completed"),
    ("CANCELLED", "Cancelled"),
)

# Where a sale was made. Online channels are imported from their reports
# (`orders.channel_import`); STORE is everything entered in the CRM itself.
SALES_CHANNELS = (
    ("STORE", "Store"),
    ("MERCADOLIBRE", "Mercado Libre"),
    ("AMAZON", "Amazon"),
)

DAY_COUNTS = (
    ("CALENDAR", "Calendar days"),
    ("WORKING", "Working days"),
)

# How a channel counts availability time until an admin sets it in
# `ChannelPreference`: Mercado Libre in calendar days, Amazon's handling time
# in working days.
DEFAULT_DAY_COUNT = {
    "STORE": "CALENDAR",
    "MERCADOLIBRE": "CALENDAR",
    "AMAZON": "WORKING",
}

# A channel's own status text, normalised. Drives stock and work orders.
FULFILLMENT_STATUS = (
    ("PENDING", "Pending"),
    ("SHIPPED", "Shipped"),
    ("DELIVERED", "Delivered"),
    ("CANCELLED", "Cancelled"),
    ("RETURNED", "Returned"),
)

WORK_ORDER_KINDS = (
    ("PREPARATION", "Preparation"),
    ("PACKAGING", "Packaging"),
    ("INSPECTION", "Return inspection"),
)

WORK_ORDER_STATUS = (
    ("PENDING", "Pending"),
    ("DONE", "Done"),
    ("CANCELLED", "Cancelled"),
)

INSPECTION_OUTCOMES = (
    ("ACCEPTED", "Accepted, back in stock"),
    ("REJECTED", "Rejected, not restocked"),
)

IMPORT_SOURCES = (
    ("ADMIN", "Admin"),
    ("COMMAND", "Command line"),
)

IMPORT_OUTCOMES = (
    ("SUCCESS", "Imported"),
    ("WITH_ISSUES", "Imported, with issues"),
    ("FAILED", "Nothing imported"),
)

IMPORT_LOG_LEVELS = (
    ("NOT_IMPORTED", "Not imported"),
    ("ACTION_REQUIRED", "Action required"),
    ("WARNING", "Warning"),
    ("CHANGE", "Change"),
)


# =============================================================================
# ORDER
# =============================================================================


class Order(BaseOrgModel):
    """
    Order model for CRM - tracks sales orders linked to accounts,
    contacts, and opportunities.
    """

    # Core Order Information
    name = models.CharField(_("Order Name"), max_length=255)
    order_number = models.CharField(
        _("Order Number"), max_length=100, blank=True, default=""
    )
    status = models.CharField(
        _("Status"), max_length=20, choices=ORDER_STATUS, default="DRAFT"
    )

    # CRM Integration
    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="orders",
        help_text="The account this order belongs to",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        related_name="orders",
        null=True,
        blank=True,
        help_text="The contact associated with this order",
    )
    opportunity = models.ForeignKey(
        "opportunity.Opportunity",
        on_delete=models.SET_NULL,
        related_name="orders",
        null=True,
        blank=True,
        help_text="The opportunity this order originated from",
    )

    # Financial
    currency = models.CharField(_("Currency"), max_length=3, blank=True, default="")
    subtotal = models.DecimalField(
        _("Subtotal"), max_digits=15, decimal_places=2, default=0
    )
    discount_amount = models.DecimalField(
        _("Discount Amount"), max_digits=15, decimal_places=2, default=0
    )
    tax_amount = models.DecimalField(
        _("Tax Amount"), max_digits=15, decimal_places=2, default=0
    )
    total_amount = models.DecimalField(
        _("Total Amount"), max_digits=15, decimal_places=2, default=0
    )

    # Dates
    order_date = models.DateField(_("Order Date"), null=True, blank=True)
    activated_date = models.DateField(_("Activated Date"), null=True, blank=True)
    shipped_date = models.DateField(_("Shipped Date"), null=True, blank=True)

    # Billing Address
    billing_address_line = models.CharField(
        _("Billing Address"), max_length=255, blank=True, default=""
    )
    billing_city = models.CharField(
        _("Billing City"), max_length=100, blank=True, default=""
    )
    billing_state = models.CharField(
        _("Billing State"), max_length=100, blank=True, default=""
    )
    billing_postcode = models.CharField(
        _("Billing Postal Code"), max_length=20, blank=True, default=""
    )
    billing_country = models.CharField(
        _("Billing Country"), max_length=3, choices=COUNTRIES, blank=True, default=""
    )

    # Shipping Address
    shipping_address_line = models.CharField(
        _("Shipping Address"), max_length=255, blank=True, default=""
    )
    shipping_city = models.CharField(
        _("Shipping City"), max_length=100, blank=True, default=""
    )
    shipping_state = models.CharField(
        _("Shipping State"), max_length=100, blank=True, default=""
    )
    shipping_postcode = models.CharField(
        _("Shipping Postal Code"), max_length=20, blank=True, default=""
    )
    shipping_country = models.CharField(
        _("Shipping Country"), max_length=3, choices=COUNTRIES, blank=True, default=""
    )

    # Notes
    description = models.TextField(_("Description"), blank=True, default="")

    # Sales channel. `external_id` is the channel's own sale id, the key a
    # re-imported report is matched on; empty for orders made in the CRM.
    channel = models.CharField(
        _("Channel"), max_length=20, choices=SALES_CHANNELS, default="STORE"
    )
    external_id = models.CharField(
        _("Channel Sale ID"), max_length=100, blank=True, default=""
    )
    channel_status = models.CharField(
        _("Channel Status"), max_length=255, blank=True, default=""
    )
    channel_status_detail = models.TextField(
        _("Channel Status Detail"), blank=True, default=""
    )
    fulfillment_status = models.CharField(
        _("Fulfillment Status"),
        max_length=20,
        choices=FULFILLMENT_STATUS,
        blank=True,
        default="",
    )
    buyer_name = models.CharField(_("Buyer"), max_length=255, blank=True, default="")
    due_date = models.DateField(
        _("Delivery Due Date"),
        null=True,
        blank=True,
        help_text="Order date plus the longest availability time of its products.",
    )
    last_synced_at = models.DateTimeField(_("Last Synced"), null=True, blank=True)

    class Meta:
        verbose_name = "Order"
        verbose_name_plural = "Orders"
        db_table = "orders"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["org", "channel", "external_id"],
                condition=~models.Q(external_id=""),
                name="orders_unique_channel_sale",
            ),
        ]
        # Restated, not inherited. A subclass that declares its own plain
        # `class Meta` does not pick up `indexes` from `BaseOrgModel.Meta`, so
        # `makemigrations` read these as removed and
        # `0003_remove_order_orders_org_id_9026a7_idx_and_more` dropped them.
        # `PersonalAccessToken` and `PackApplication` spell it out the same
        # way; `common/tests/test_org_index_coverage.py` now fails if a new
        # subclass forgets.
        indexes = [
            models.Index(fields=["org", "-created_at"]),
        ]

    def __str__(self):
        return self.name


# =============================================================================
# ORDER LINE ITEM
# =============================================================================


class OrderLineItem(BaseOrgModel):
    """Line item for Orders - individual products/services within an order."""

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="line_items"
    )
    product = models.ForeignKey(
        "invoices.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_line_items",
    )
    name = models.CharField(_("Item Name"), max_length=255)
    description = models.TextField(_("Description"), blank=True, default="")
    quantity = models.DecimalField(
        _("Quantity"), max_digits=10, decimal_places=2, default=1
    )
    unit_price = models.DecimalField(
        _("Unit Price"), max_digits=15, decimal_places=2, default=0
    )
    discount_amount = models.DecimalField(
        _("Discount Amount"), max_digits=15, decimal_places=2, default=0
    )
    total = models.DecimalField(_("Total"), max_digits=15, decimal_places=2, default=0)
    sort_order = models.IntegerField(_("Sort Order"), default=0)

    # Channel sync. In a multi-product sale each item has its own channel id.
    external_id = models.CharField(
        _("Channel Item ID"), max_length=100, blank=True, default=""
    )
    listing = models.ForeignKey(
        "orders.ChannelListing",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="line_items",
    )
    channel_status = models.CharField(
        _("Channel Status"), max_length=255, blank=True, default=""
    )
    fulfillment_status = models.CharField(
        _("Fulfillment Status"),
        max_length=20,
        choices=FULFILLMENT_STATUS,
        blank=True,
        default="",
    )
    due_date = models.DateField(_("Ready By"), null=True, blank=True)
    # What `orders.inventory` took for this item. `fulfillment_planned` is set
    # while the item holds stock and work orders, so a re-import never takes
    # stock twice and a cancellation knows what to give back.
    stock_allocated = models.DecimalField(
        _("Stock Allocated"), max_digits=10, decimal_places=2, default=0
    )
    fulfillment_planned = models.BooleanField(_("Fulfillment Planned"), default=False)

    class Meta:
        verbose_name = "Order Line Item"
        verbose_name_plural = "Order Line Items"
        db_table = "order_line_item"
        ordering = ("sort_order",)
        # See the note on `Order.Meta`.
        indexes = [
            models.Index(fields=["org", "-created_at"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Inherit org from order if not set
        if not self.org_id and self.order_id:
            self.org_id = self.order.org_id
        elif self.org_id and self.order_id and self.order.org_id != self.org_id:
            raise ValueError("OrderLineItem.org must match its parent Order.org")

        # Compute total: quantity * unit_price - discount
        self.total = self.quantity * self.unit_price - self.discount_amount
        super().save(*args, **kwargs)


# =============================================================================
# CHANNEL LISTING
# =============================================================================


class ChannelListing(BaseOrgModel):
    """One channel's listing (and variant) of a catalog product.

    Lets several channels sell, and draw stock from, the same product. Point
    `product` elsewhere to merge a listing into an existing product; the next
    import moves its open order items over.
    """

    channel = models.CharField(_("Channel"), max_length=20, choices=SALES_CHANNELS)
    listing_id = models.CharField(_("Listing ID"), max_length=100)
    variant = models.CharField(_("Variant"), max_length=255, blank=True, default="")
    title = models.CharField(_("Title"), max_length=255, blank=True, default="")
    sku = models.CharField(_("Channel SKU"), max_length=100, blank=True, default="")
    product = models.ForeignKey(
        "invoices.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="channel_listings",
    )
    product_created = models.BooleanField(
        _("Product Created by Import"),
        default=False,
        help_text="The import made this listing's product: complete its stock, "
        "availability time and category.",
    )

    class Meta:
        verbose_name = "Channel Listing"
        verbose_name_plural = "Channel Listings"
        db_table = "order_channel_listing"
        ordering = ("channel", "listing_id", "variant")
        constraints = [
            models.UniqueConstraint(
                fields=["org", "channel", "listing_id", "variant"],
                name="order_channel_listing_unique",
            ),
        ]
        # See the note on `Order.Meta`.
        indexes = [
            models.Index(fields=["org", "-created_at"]),
        ]

    def __str__(self):
        label = f"{self.get_channel_display()} {self.listing_id}"
        return f"{label} ({self.variant})" if self.variant else label


# =============================================================================
# WORK ORDER
# =============================================================================


class WorkOrder(BaseOrgModel):
    """Work the store has to do before a sale can ship.

    One PACKAGING per sale, and one PREPARATION per product of the sale that
    stock does not cover, for the units short (see
    `orders.inventory.sync_work_orders`). An INSPECTION checks goods that came
    back after shipping and can take days; the import opens it and moves on.
    """

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="work_orders"
    )
    # Only for preparing an item with no catalog product, where the item is
    # the only thing that says what to make.
    line_item = models.ForeignKey(
        OrderLineItem,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="work_orders",
    )
    product = models.ForeignKey(
        "invoices.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
    )
    kind = models.CharField(_("Kind"), max_length=20, choices=WORK_ORDER_KINDS)
    status = models.CharField(
        _("Status"), max_length=20, choices=WORK_ORDER_STATUS, default="PENDING"
    )
    quantity = models.DecimalField(_("Quantity"), max_digits=10, decimal_places=2)
    due_date = models.DateField(_("Due Date"), null=True, blank=True)

    # INSPECTION only: the decision on goods that came back.
    restock_quantity = models.DecimalField(
        _("Quantity to Restock"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Units fit for sale. Added to the product's stock on accepting.",
    )
    outcome = models.CharField(
        _("Outcome"), max_length=20, choices=INSPECTION_OUTCOMES, blank=True, default=""
    )
    notes = models.TextField(_("Notes"), blank=True, default="")
    completed_at = models.DateTimeField(_("Completed At"), null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "Work Order"
        verbose_name_plural = "Work Orders"
        db_table = "order_work_order"
        ordering = ("due_date", "created_at")
        # See the note on `Order.Meta`.
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["org", "status", "kind"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["line_item"],
                condition=models.Q(kind="INSPECTION"),
                name="order_work_order_one_inspection_per_item",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} x{self.quantity} for {self.order}"

    def clean(self):
        if self.restock_quantity is not None and not (
            0 <= self.restock_quantity <= self.quantity
        ):
            raise ValidationError(
                {"restock_quantity": "Must be between 0 and the returned quantity."}
            )


# =============================================================================
# RETURN INSPECTION
# =============================================================================


class ReturnInspectionManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(kind="INSPECTION")


class ReturnInspection(WorkOrder):
    """The INSPECTION work orders: goods that came back after shipping,
    waiting to be checked. Nothing goes back in stock until someone accepts
    them; see `orders.inventory.accept_return`."""

    objects = ReturnInspectionManager()

    class Meta:
        proxy = True
        verbose_name = "Return Inspection"
        verbose_name_plural = "Return Inspections"

    def save(self, *args, **kwargs):
        self.kind = "INSPECTION"
        super().save(*args, **kwargs)


# =============================================================================
# IMPORT RUN
# =============================================================================


class ImportRun(BaseOrgModel):
    """One import of a channel report, from the admin or the command line,
    with what it did and what it could not do (`entries`)."""

    channel = models.CharField(_("Channel"), max_length=20, choices=SALES_CHANNELS)
    source = models.CharField(_("Source"), max_length=20, choices=IMPORT_SOURCES)
    file_name = models.CharField(_("File"), max_length=255)
    file_size = models.PositiveIntegerField(_("Size (bytes)"))
    file_sha256 = models.CharField(_("SHA-256"), max_length=64)
    started_at = models.DateTimeField(_("Started"))
    finished_at = models.DateTimeField(_("Finished"))
    outcome = models.CharField(_("Outcome"), max_length=20, choices=IMPORT_OUTCOMES)
    # Why nothing was imported, when the file as a whole could not be read.
    error = models.TextField(_("Error"), blank=True, default="")
    # `ImportResult`'s counters, as numbers and strings.
    counts = models.JSONField(_("Counts"), default=dict, blank=True)

    class Meta:
        verbose_name = "Import Run"
        verbose_name_plural = "Import Runs"
        db_table = "order_import_run"
        ordering = ("-started_at",)
        # See the note on `Order.Meta`.
        indexes = [
            models.Index(fields=["org", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.get_channel_display()} {self.file_name} ({self.started_at:%Y-%m-%d %H:%M})"


class ImportRunEntry(BaseOrgModel):
    """One line of an import's log."""

    run = models.ForeignKey(ImportRun, on_delete=models.CASCADE, related_name="entries")
    position = models.PositiveIntegerField(_("Position"))
    level = models.CharField(_("Level"), max_length=20, choices=IMPORT_LOG_LEVELS)
    sale = models.CharField(_("Sale"), max_length=100, blank=True, default="")
    message = models.TextField(_("Detail"))

    class Meta:
        verbose_name = "Import Log Entry"
        verbose_name_plural = "Import Log Entries"
        db_table = "order_import_run_entry"
        ordering = ("run", "position")
        # See the note on `Order.Meta`.
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["run", "level"]),
        ]

    def __str__(self):
        return f"{self.get_level_display()}: {self.message[:80]}"


# =============================================================================
# CHANNEL PREFERENCE
# =============================================================================


class ChannelPreference(BaseModel):
    """How a channel counts a product's availability time, for the whole
    instance: it is the channel's rule, the same for every org. A change
    applies to sales imported after it; orders already imported keep their
    due dates, since the channel remains their master."""

    channel = models.CharField(
        _("Channel"), max_length=20, choices=SALES_CHANNELS, unique=True
    )
    availability_day_count = models.CharField(
        _("Availability Time Counts"), max_length=20, choices=DAY_COUNTS
    )

    class Meta:
        verbose_name = "Channel Preference"
        verbose_name_plural = "Channel Preferences"
        db_table = "order_channel_preference"
        ordering = ("channel",)

    def __str__(self):
        return (
            f"{self.get_channel_display()}: {self.get_availability_day_count_display()}"
        )

    @classmethod
    def day_count(cls, channel: str) -> str:
        preference = cls.objects.filter(channel=channel).first()
        if preference is not None:
            return preference.availability_day_count
        return DEFAULT_DAY_COUNT.get(channel, "CALENDAR")
