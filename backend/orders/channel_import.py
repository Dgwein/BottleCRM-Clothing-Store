"""Import a sales channel's report into orders, as often as it is received.

Reports are one-way: nothing is sent back to the channel. Each import is a
sync. A sale is matched on (org, channel, channel sale id) and each of its
items on its own channel id, so a report imported twice creates nothing the
second time, and a later report updates the status of what it already made.
Sales missing from a report are left alone, since a report may cover only a
date range.

Each sale is imported on its own: one that fails validation or raises is
rolled back and logged as not imported, with the reason, and the rest go on.
Everything the import did or could not do is in `ImportResult.entries`.

Status changes drive stock (see `orders.inventory`):

* an item first seen still to be shipped is planned: stock taken, and the
  sale's work orders opened;
* an item first seen already shipped, delivered, cancelled or returned is
  history, and touches no stock;
* a planned item that is cancelled, or refunded, before it shipped is
  released; one whose product or quantity changes before shipping is
  released and planned again;
* shipping closes the sale's open work orders;
* an item that shipped and then comes back (returned, or cancelled) restocks
  nothing by itself: it opens a `ReturnInspection`, and stock returns only
  when someone accepts it.

Due dates are set when a sale is first imported, counting the product's
availability time as the channel's `ChannelPreference` says, and are not
recalculated by later imports: the channel remains the order's master.

Items link to catalog products through `ChannelListing`: a known listing's
product, else the product with the item's SKU, else a new product.
"""

import dataclasses
import hashlib
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounts.models import Account
from common.models import Comment
from common.utils import CURRENCY_CODES
from invoices.models import Product
from orders import inventory
from orders.channels import PARSERS
from orders.channels.base import ChannelImportError, ChannelLine, ChannelOrder
from orders.models import (
    SALES_CHANNELS,
    ChannelListing,
    ImportRun,
    ImportRunEntry,
    Order,
    OrderLineItem,
)

logger = logging.getLogger(__name__)

# An order's status is its most active item's: one item still pending keeps
# the whole order open.
_STATUS_PRIORITY = ("PENDING", "SHIPPED", "DELIVERED", "RETURNED", "CANCELLED")
_ORDER_STATUS = {
    "PENDING": "ACTIVATED",
    "SHIPPED": "ACTIVATED",
    "DELIVERED": "COMPLETED",
    "RETURNED": "CANCELLED",
    "CANCELLED": "CANCELLED",
}
_CURRENCIES = {code for code, _ in CURRENCY_CODES}
MAX_REPORT_BYTES = 10 * 1024 * 1024
# The largest values the order columns hold (max_digits less decimal places).
_MAX_QUANTITY = Decimal("1e8")
_MAX_AMOUNT = Decimal("1e13")

# Log levels, most urgent first.
NOT_IMPORTED = "NOT_IMPORTED"
ACTION_REQUIRED = "ACTION_REQUIRED"
WARNING = "WARNING"
CHANGE = "CHANGE"
LEVELS = {
    NOT_IMPORTED: "Not imported",
    ACTION_REQUIRED: "Action required",
    WARNING: "Warning",
    CHANGE: "Change",
}


@dataclass
class LogEntry:
    level: str
    sale: str
    message: str


@dataclass
class ImportResult:
    orders_created: int = 0
    orders_updated: int = 0
    orders_not_imported: int = 0
    items_created: int = 0
    items_updated: int = 0
    products_created: int = 0
    listings_created: int = 0
    preparation_orders: int = 0
    packaging_orders: int = 0
    returns_to_inspect: int = 0
    stock_allocated: Decimal = Decimal("0")
    stock_released: Decimal = Decimal("0")
    entries: list[LogEntry] = field(default_factory=list)

    def log(self, level: str, sale: str, message: str) -> None:
        self.entries.append(LogEntry(level, sale, message))

    def of_level(self, level: str) -> list[LogEntry]:
        return [entry for entry in self.entries if entry.level == level]

    @property
    def warnings(self) -> list[str]:
        return [e.message for e in self.of_level(WARNING)]

    def counts(self) -> dict:
        return {
            f.name: str(getattr(self, f.name))
            if isinstance(getattr(self, f.name), Decimal)
            else getattr(self, f.name)
            for f in dataclasses.fields(self)
            if f.name != "entries"
        }


def summary(result: ImportResult) -> str:
    """The counts of one import, for the command and the admin."""
    return (
        f"Orders: {result.orders_created} created, {result.orders_updated} "
        f"updated, {result.orders_not_imported} not imported.\n"
        f"Items: {result.items_created} created, {result.items_updated} updated.\n"
        f"Catalog: {result.products_created} products and "
        f"{result.listings_created} listings created.\n"
        f"Work orders: {result.preparation_orders} preparation, "
        f"{result.packaging_orders} packaging.\n"
        f"Returns to inspect: {result.returns_to_inspect}.\n"
        f"Stock: {_units(result.stock_allocated)} units taken, "
        f"{_units(result.stock_released)} put back."
    )


def _units(value: Decimal) -> str:
    """2.00 as '2', 2.50 as '2.5'."""
    return f"{value.normalize():f}"


def _fit(model, name: str, value: str) -> str:
    return (value or "")[: model._meta.get_field(name).max_length]


def channel_label(channel: str) -> str:
    return dict(SALES_CHANNELS)[channel]


def import_channel_report(org, channel: str, file_name: str, data: bytes):
    """Parse one channel report and sync it into `org`'s orders. Raises
    `ChannelImportError` when the file as a whole cannot be read."""
    parser = PARSERS.get(channel)
    if parser is None:
        label = dict(SALES_CHANNELS).get(channel, channel)
        raise ChannelImportError(f"There is no report importer for {label} yet.")
    report = parser(file_name, data)
    result = sync_channel_orders(org, channel, report.orders)
    result.entries[:0] = [
        LogEntry(WARNING, sale, message) for sale, message in report.warnings
    ]
    return result


def record_import(org, channel: str, file_name: str, data: bytes, source: str):
    """Import a report and keep the run and its whole log. Returns the
    `ImportRun` and the `ImportResult`, which is None when the file could not
    be read at all (the run then says why)."""
    started = timezone.now()
    result, error = None, ""
    try:
        result = import_channel_report(org, channel, file_name, data)
    except ChannelImportError as exc:
        error = str(exc)
    if result is None:
        outcome = "FAILED"
    elif result.of_level(NOT_IMPORTED) or result.of_level(WARNING):
        outcome = "WITH_ISSUES"
    else:
        outcome = "SUCCESS"
    with transaction.atomic():
        run = ImportRun.objects.create(
            org=org,
            channel=channel,
            source=source,
            file_name=_fit(ImportRun, "file_name", file_name),
            file_size=len(data),
            file_sha256=hashlib.sha256(data).hexdigest(),
            started_at=started,
            finished_at=timezone.now(),
            outcome=outcome,
            error=error,
            counts=result.counts() if result else {},
        )
        ImportRunEntry.objects.bulk_create(
            ImportRunEntry(
                org=org,
                run=run,
                position=position,
                level=entry.level,
                sale=_fit(ImportRunEntry, "sale", entry.sale),
                message=entry.message,
            )
            for position, entry in enumerate(result.entries if result else [])
        )
    return run, result


def problem_with(data: ChannelOrder) -> str | None:
    """Why a sale cannot be imported as it stands, or None."""
    if not data.lines:
        return "It has no items."
    seen = set()
    for line in data.lines:
        name = line.external_id or "without a number"
        if not line.external_id:
            return "An item has no sale number."
        if line.external_id in seen:
            return f"Item {name} appears twice."
        seen.add(line.external_id)
        if not 0 < line.quantity < _MAX_QUANTITY:
            return f"Item {name}: the quantity must be above 0 and below 100,000,000."
        if line.unit_price is None:
            return (
                f"Item {name}: the unit price is empty. The price is mandatory, "
                "so neither the order nor its products were created."
            )
        if not 0 <= line.unit_price < _MAX_AMOUNT:
            return f"Item {name}: the unit price is out of range."
    if (
        data.total_amount is not None
        and not -_MAX_AMOUNT < data.total_amount < _MAX_AMOUNT
    ):
        return "The sale's total is out of range."
    return None


def sync_channel_orders(org, channel: str, orders: list[ChannelOrder]):
    syncer = _Sync(org, channel)
    seen = set()
    for channel_order in orders:
        sale = channel_order.external_id
        problem = problem_with(channel_order)
        if problem:
            syncer.result.orders_not_imported += 1
            syncer.result.log(NOT_IMPORTED, sale, problem)
            continue
        if sale in seen:
            syncer.result.log(
                WARNING, sale, "The sale appears more than once; the last one wins."
            )
        seen.add(sale)
        syncer.sync_safely(channel_order)
    return syncer.result


class _Sync:
    def __init__(self, org, channel: str):
        self.org = org
        self.channel = channel
        self.label = channel_label(channel)
        self.result = ImportResult()
        self.add_days = inventory.day_counter(org.id, channel)
        self._account = None
        self._products = {}
        # New products are priced in the currency of the sale that made them.
        self._currency = None

    def sync_safely(self, data: ChannelOrder) -> None:
        counters = {
            f.name: getattr(self.result, f.name)
            for f in dataclasses.fields(self.result)
            if f.name != "entries"
        }
        logged = len(self.result.entries)
        try:
            with transaction.atomic():
                self.sync_order(data)
        except Exception as exc:
            logger.exception("Channel sale %s was not imported", data.external_id)
            # The sale's rows are rolled back, so is everything it counted,
            # logged or cached.
            for name, value in counters.items():
                setattr(self.result, name, value)
            del self.result.entries[logged:]
            self._account = None
            self._products = {}
            self.result.orders_not_imported += 1
            reason = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else ""
            self.result.log(
                NOT_IMPORTED,
                data.external_id,
                f"Failed while saving ({type(exc).__name__}"
                + (f": {reason}" if reason else "")
                + "). Nothing of this sale was saved.",
            )

    def account(self) -> Account:
        """Channel sales are booked to one account named after the channel."""
        if self._account is None:
            self._account = (
                Account.objects.filter(org=self.org, name=self.label)
                .order_by("created_at")
                .first()
            ) or Account.objects.create(org=self.org, name=self.label)
        return self._account

    def sync_order(self, data: ChannelOrder) -> Order:
        sale = data.external_id
        order = (
            Order.objects.select_for_update()
            .filter(org=self.org, channel=self.channel, external_id=sale)
            .first()
        )
        created = order is None
        if created:
            order = Order(
                org=self.org,
                channel=self.channel,
                external_id=sale,
                account=self.account(),
            )
            self.result.orders_created += 1
            previous_status = None
        else:
            self.result.orders_updated += 1
            previous_status = order.fulfillment_status

        order.name = _fit(Order, "name", f"{self.label} {sale}")
        order.order_number = _fit(Order, "order_number", sale)
        order.order_date = data.order_date or order.order_date
        order.currency = data.currency or order.currency
        order.channel_status = _fit(Order, "channel_status", data.status_text)
        order.channel_status_detail = data.status_detail
        order.buyer_name = _fit(Order, "buyer_name", data.buyer_name)
        order.shipped_date = data.shipped_date or order.shipped_date
        for name in (
            "shipping_address_line",
            "shipping_city",
            "shipping_state",
            "shipping_postcode",
            "shipping_country",
        ):
            setattr(order, name, _fit(Order, name, getattr(data, name)))
        order.last_synced_at = timezone.now()
        order.save()
        self._currency = data.currency if data.currency in _CURRENCIES else None

        existing = {item.external_id: item for item in order.line_items.all()}
        for position, line in enumerate(data.lines):
            self.sync_line(order, existing.get(line.external_id), line, position)

        items = list(order.line_items.all())
        statuses = {item.fulfillment_status for item in items}
        order.fulfillment_status = next(
            (s for s in _STATUS_PRIORITY if s in statuses), "PENDING"
        )
        order.status = _ORDER_STATUS[order.fulfillment_status]
        if order.status != "CANCELLED" and not order.activated_date:
            order.activated_date = order.order_date
        order.subtotal = sum((item.total for item in items), Decimal("0"))
        order.total_amount = (
            order.subtotal if data.total_amount is None else data.total_amount
        )
        if order.due_date is None:
            order.due_date = inventory.order_due_date(items)
        order.save()

        if created:
            self.result.log(
                CHANGE,
                sale,
                f"Created with {len(items)} item(s), "
                f"{order.get_fulfillment_status_display().lower()}"
                + (f", due {order.due_date}." if order.due_date else "."),
            )
        elif previous_status != order.fulfillment_status:
            self.result.log(
                CHANGE,
                sale,
                "Status changed from "
                f"{(previous_status or 'none').lower()} to "
                f"{order.fulfillment_status.lower()}.",
            )

        for work_order in inventory.sync_work_orders(order):
            if work_order.kind == "PREPARATION":
                self.result.preparation_orders += 1
                what = work_order.product or work_order.line_item
                self.result.log(
                    CHANGE,
                    sale,
                    f"Preparation order: {_units(work_order.quantity)} x {what}"
                    + (f", due {work_order.due_date}." if work_order.due_date else "."),
                )
            else:
                self.result.packaging_orders += 1
                self.result.log(
                    CHANGE,
                    sale,
                    f"Packaging order: {_units(work_order.quantity)} unit(s)"
                    + (f", due {work_order.due_date}." if work_order.due_date else "."),
                )
        return order

    def sync_line(self, order: Order, item, data: ChannelLine, position: int):
        sale = order.external_id
        if data.status is None:
            self.result.log(
                WARNING,
                sale,
                f"Item {data.external_id}: status '{data.status_text}' is not "
                "recognised; the item keeps its previous status (a new item is "
                "treated as pending).",
            )
        listing, product = self.product_for(data, sale)
        if product is None:
            self.result.log(
                ACTION_REQUIRED,
                sale,
                f"Item {data.external_id} has no listing or SKU, so it is not linked "
                "to a product: its stock cannot be tracked.",
            )
        product_id = product.pk if product else None

        if item is None:
            item = OrderLineItem(
                org=self.org, order=order, external_id=data.external_id
            )
            previous = None
            changed = False
            self.result.items_created += 1
        else:
            previous = item.fulfillment_status
            changed = item.product_id != product_id or item.quantity != data.quantity
            self.result.items_updated += 1
        status = data.status or previous or "PENDING"

        shipped_before = previous in ("SHIPPED", "DELIVERED")
        undone = status in ("CANCELLED", "RETURNED")
        if undone and shipped_before:
            _inspection, opened = inventory.open_return(item)
            if opened:
                self.result.returns_to_inspect += 1
                self.result.log(
                    ACTION_REQUIRED,
                    sale,
                    f"Return inspection opened: {_units(item.quantity)} x "
                    f"{item.product or item}. Accept or reject it in Return "
                    "inspections when the goods are checked.",
                )
        elif item.fulfillment_planned and (undone or (status == "PENDING" and changed)):
            units = inventory.release_line(item)
            self.result.stock_released += units
            if units:
                self.result.log(
                    CHANGE,
                    sale,
                    f"Put {_units(units)} x {item.product} back in stock "
                    f"({'changed' if status == 'PENDING' else status.lower()} "
                    "before shipping).",
                )

        # A planned item keeps the product its stock came from, so a listing
        # remapped after the item shipped cannot make a later release restock
        # the wrong product.
        if not item.fulfillment_planned or item.product_id == product_id:
            item.product = product
            item.listing = listing
        item.name = _fit(OrderLineItem, "name", data.title or data.listing_id)
        item.description = data.variant
        item.quantity = data.quantity
        item.unit_price = data.unit_price
        item.sort_order = position
        item.channel_status = _fit(OrderLineItem, "channel_status", data.status_text)
        item.fulfillment_status = status
        if item.due_date is None:
            item.due_date = inventory.ready_date(
                order.order_date, item.product, self.add_days
            )
        item.save()

        if status == "PENDING" and not item.fulfillment_planned:
            taken = inventory.plan_line(item)
            self.result.stock_allocated += taken
            if taken:
                self.result.log(
                    CHANGE, sale, f"Took {_units(taken)} x {item.product} from stock."
                )

    def product_for(self, data: ChannelLine, sale: str):
        """The listing and catalog product for an item, making either if new."""
        key = (data.listing_id, data.variant, data.sku)
        if key in self._products:
            return self._products[key]
        listing = None
        if data.listing_id:
            listing, created = ChannelListing.objects.get_or_create(
                org=self.org,
                channel=self.channel,
                listing_id=_fit(ChannelListing, "listing_id", data.listing_id),
                variant=_fit(ChannelListing, "variant", data.variant),
                defaults={
                    "title": _fit(ChannelListing, "title", data.title),
                    "sku": _fit(ChannelListing, "sku", data.sku),
                },
            )
            self.result.listings_created += created
        product = listing.product if listing else None
        if product is None and (listing or data.sku):
            product, made = self._catalog_product(data)
            if made:
                self.result.log(
                    ACTION_REQUIRED,
                    sale,
                    f"Product '{product}' was created from "
                    + (f"listing {data.listing_id}" if data.listing_id else "its SKU")
                    + ": complete its stock, availability time and category, or "
                    "point the listing at an existing product.",
                )
            if listing:
                listing.product = product
                listing.product_created = made
                listing.save(update_fields=["product", "product_created", "updated_at"])
        self._products[key] = (listing, product)
        return listing, product

    def _catalog_product(self, data: ChannelLine) -> tuple[Product, bool]:
        """The product with the item's SKU, else a new one made from what the
        report says. Its price and description are mandatory: the price is
        the sale's (a sale without one never gets here) and the description
        the listing title and variant. See the docs page."""
        sku = _fit(Product, "sku", data.sku)
        if sku:
            product = Product.objects.filter(org=self.org, sku=sku).first()
            if product:
                return product, False
        title = data.title or data.listing_id or sku
        name = f"{title} ({data.variant})" if data.variant else title
        description = f"{title}. {data.variant}" if data.variant else title
        source = (f"listing {data.listing_id}" if data.listing_id else f"SKU {sku}") + (
            f", variant {data.variant}" if data.variant else ""
        )
        self.result.products_created += 1
        product = Product.objects.create(
            org=self.org,
            name=_fit(Product, "name", name),
            description=description,
            sku=sku or None,
            price=data.unit_price,
            currency=self._currency,
        )
        Comment.objects.create(
            org=self.org,
            content_object=product,
            comment=f"Created by the {self.label} import from {source}, priced at "
            "the unit price of the sale that created it.",
            is_internal=True,
        )
        return product, True
