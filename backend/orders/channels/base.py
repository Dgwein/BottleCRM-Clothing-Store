"""What every channel report parser hands to `orders.channel_import`.

A parser reads one channel's file format and returns `ChannelOrder`s. Nothing
after it knows which channel the data came from, so a new channel is a new
parser registered in `orders.channels.PARSERS`.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal


class ChannelImportError(Exception):
    """Raised with a sentence that is safe to show to the user."""


@dataclass
class ChannelLine:
    external_id: str
    quantity: Decimal
    # None when the report left it empty (or unreadable): the sale is then
    # refused, since a price is mandatory for the item and any product made.
    unit_price: Decimal | None
    title: str = ""
    listing_id: str = ""
    variant: str = ""
    sku: str = ""
    status_text: str = ""
    # One of FULFILLMENT_STATUS, or None when the parser did not recognise
    # `status_text`.
    status: str | None = None


@dataclass
class ChannelOrder:
    external_id: str
    lines: list[ChannelLine]
    order_date: datetime.date | None = None
    status_text: str = ""
    status_detail: str = ""
    buyer_name: str = ""
    currency: str = ""
    # What the buyer paid, when the report says; else the lines' sum is used.
    total_amount: Decimal | None = None
    shipped_date: datetime.date | None = None
    shipping_address_line: str = ""
    shipping_city: str = ""
    shipping_state: str = ""
    shipping_postcode: str = ""
    shipping_country: str = ""


@dataclass
class ChannelReport:
    orders: list[ChannelOrder]
    # (sale id, sentence) for each row the parser skipped or read only in part.
    warnings: list[tuple[str, str]] = field(default_factory=list)
