"""Mercado Libre's sales report (Ventas), the .xlsx that Seller Central exports.

Layout: a few lines of preamble, a row of section names (Ventas,
Publicaciones, Compradores, Envíos, ...), the column headers, then one row per
sale. A sale of several products is a row whose status reads "Paquete de N
productos", carrying the buyer, totals and shipment, followed by N rows with
one product each, each with its own sale id and status.

Headers repeat across sections (Estado is both the sale status and the buyer's
state), so a column is found by its section and header together.
"""

import datetime
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from common.spreadsheet import SpreadsheetError, read_table
from common.utils import COUNTRIES
from orders.channels.base import (
    ChannelImportError,
    ChannelLine,
    ChannelOrder,
    ChannelReport,
)

MAX_ROWS = 20_000

# key: (section, header) as the report spells them, currency suffix dropped.
COLUMNS = {
    "sale_id": ("Ventas", "# de venta"),
    "sale_date": ("Ventas", "Fecha de venta"),
    "status": ("Ventas", "Estado"),
    "status_detail": ("Ventas", "Descripción del estado"),
    "units": ("Ventas", "Unidades"),
    "product_revenue": ("Ventas", "Ingresos por productos"),
    "shipping_revenue": ("Ventas", "Ingresos por envío"),
    "sku": ("Publicaciones", "SKU"),
    "listing_id": ("Publicaciones", "# de publicación"),
    "title": ("Publicaciones", "Título de la publicación"),
    "variant": ("Publicaciones", "Variante"),
    "unit_price": ("Publicaciones", "Precio unitario de venta de la publicación"),
    "buyer": ("Compradores", "Comprador"),
    "address": ("Compradores", "Domicilio"),
    "city": ("Compradores", "Municipio/Alcaldía"),
    "state": ("Compradores", "Estado"),
    "postcode": ("Compradores", "Código postal"),
    "country": ("Compradores", "País"),
    "shipped_on": ("Envíos", "Fecha en camino"),
}
REQUIRED = (
    "sale_id",
    "sale_date",
    "status",
    "units",
    "listing_id",
    "title",
    "variant",
    "unit_price",
)

_HEADER_SEARCH_ROWS = 30
_CURRENCY_SUFFIX = re.compile(r"\s*\(([A-Za-z]{3})\)\s*$")
_PACK = re.compile(r"^paquete de (\d+) productos?$")
_SALE_DATE = re.compile(r"(\d{1,2}) de ([a-z]+) de (\d{4})")
_DAY_MONTH = re.compile(r"(\d{1,2}) de ([a-z]+)")
_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
_CENT = Decimal("0.01")


def _norm(text: str) -> str:
    """Lowercase, accents dropped, whitespace collapsed."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(bare.lower().split())


# Checked in order: "Devolución entregada" is a return, not a delivery, and
# "Te dimos el dinero del producto que te devolvieron" is a return too.
_STATUS_RULES = (
    ("CANCELLED", ("cancel",)),
    ("RETURNED", ("devol", "devuelto", "reembols", "no entregad")),
    ("DELIVERED", ("entregad", "te dimos el dinero")),
    ("SHIPPED", ("en camino", "enviad", "despachad", "en punto de retiro")),
    (
        "PENDING",
        (
            "etiqueta",
            "esperando",
            "listo para",
            "lista para",
            "prepar",
            "pendiente",
            "acordar",
            "por enviar",
        ),
    ),
)


def classify_status(text: str) -> str | None:
    """Map a Mercado Libre status to FULFILLMENT_STATUS, None if unknown."""
    normalized = _norm(text)
    if not normalized:
        return None
    for status, needles in _STATUS_RULES:
        if any(needle in normalized for needle in needles):
            return status
    return None


def _sale_date(text: str) -> datetime.date | None:
    """'15 de noviembre de 2026 10:40 hs.', or ISO when the cell held a date."""
    match = _SALE_DATE.search(_norm(text))
    try:
        if match:
            day, month, year = match.groups()
            if month not in _MONTHS:
                return None
            return datetime.date(int(year), _MONTHS[month], int(day))
        return datetime.date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None


def _date_after(text: str, reference: datetime.date | None) -> datetime.date | None:
    """'27 de marzo | 20:50' carries no year: take the first one on or after
    the sale date."""
    match = _DAY_MONTH.search(_norm(text))
    if not match or reference is None or match.group(2) not in _MONTHS:
        return None
    day, month = int(match.group(1)), _MONTHS[match.group(2)]
    try:
        found = datetime.date(reference.year, month, day)
        if found < reference:
            found = datetime.date(reference.year + 1, month, day)
    except ValueError:
        return None
    return found


def _decimal(text: str) -> Decimal | None:
    if not text:
        return None
    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
    return value.quantize(_CENT) if value.is_finite() else None


_COUNTRY_CODES = {_norm(str(name)): code for code, name in COUNTRIES}


class _Sheet:
    """The report's rows with its columns resolved."""

    def __init__(self, rows: list[list[str]]):
        header_at = next(
            (
                i
                for i, row in enumerate(rows[:_HEADER_SEARCH_ROWS])
                if row and _norm(row[0]) == "# de venta"
            ),
            None,
        )
        if header_at is None:
            raise ChannelImportError(
                "This does not look like a Mercado Libre sales report: "
                "no '# de venta' column was found."
            )
        headers = rows[header_at]
        sections = rows[header_at - 1] if header_at else []
        self.currency = ""
        found = {}
        section = ""
        for index, header in enumerate(headers):
            if index < len(sections) and sections[index]:
                section = _norm(sections[index])
            currency = _CURRENCY_SUFFIX.search(header)
            if currency and not self.currency:
                self.currency = currency.group(1).upper()
            key = (section, _norm(_CURRENCY_SUFFIX.sub("", header)))
            found.setdefault(key, index)
        self.columns = {}
        for name, (section, header) in COLUMNS.items():
            index = found.get((_norm(section), _norm(header)))
            if index is not None:
                self.columns[name] = index
        missing = [COLUMNS[name][1] for name in REQUIRED if name not in self.columns]
        if missing:
            raise ChannelImportError(
                "This does not look like a Mercado Libre sales report: "
                f"missing the columns {', '.join(missing)}."
            )
        self.rows = rows[header_at + 1 :]

    def get(self, row: list[str], name: str) -> str:
        index = self.columns.get(name)
        if index is None or index >= len(row):
            return ""
        return " ".join(row[index].split())


def _line(sheet: _Sheet, row: list[str]) -> ChannelLine:
    status_text = sheet.get(row, "status")
    # Blank (or unreadable) units mean the report did not say, so one; a 0 is
    # kept, so the import can refuse it. A blank price stays None and is
    # refused (see `orders.channel_import.problem_with`).
    units = _decimal(sheet.get(row, "units"))
    return ChannelLine(
        external_id=sheet.get(row, "sale_id"),
        quantity=Decimal("1") if units is None else units,
        unit_price=_decimal(sheet.get(row, "unit_price")),
        title=sheet.get(row, "title"),
        listing_id=sheet.get(row, "listing_id"),
        variant=sheet.get(row, "variant"),
        sku=sheet.get(row, "sku"),
        status_text=status_text,
        status=classify_status(status_text),
    )


def _order(
    sheet: _Sheet,
    head: list[str],
    items: list[list[str]],
    warnings: list[tuple[str, str]],
) -> ChannelOrder:
    sale_id = sheet.get(head, "sale_id")
    order_date = _sale_date(sheet.get(head, "sale_date"))
    if order_date is None:
        warnings.append((sale_id, "The sale date could not be read."))
    lines = [_line(sheet, row) for row in items]
    if items == [head]:
        status_text = sheet.get(head, "status")
        detail = sheet.get(head, "status_detail")
    else:
        status_text = " / ".join(
            dict.fromkeys(x.status_text for x in lines if x.status_text)
        )
        detail = " / ".join(
            dict.fromkeys(
                d for d in (sheet.get(row, "status_detail") for row in items) if d
            )
        )
    revenue = _decimal(sheet.get(head, "product_revenue"))
    shipping = _decimal(sheet.get(head, "shipping_revenue")) or Decimal("0")
    return ChannelOrder(
        external_id=sale_id,
        lines=lines,
        order_date=order_date,
        status_text=status_text,
        status_detail=detail,
        buyer_name=sheet.get(head, "buyer"),
        currency=sheet.currency,
        total_amount=None if revenue is None else revenue + shipping,
        shipped_date=_date_after(sheet.get(head, "shipped_on"), order_date),
        shipping_address_line=sheet.get(head, "address"),
        shipping_city=sheet.get(head, "city"),
        shipping_state=sheet.get(head, "state"),
        shipping_postcode=sheet.get(head, "postcode"),
        shipping_country=_COUNTRY_CODES.get(_norm(sheet.get(head, "country")), ""),
    )


def parse(file_name: str, data: bytes) -> ChannelReport:
    try:
        table = read_table(file_name, data, MAX_ROWS)
    except SpreadsheetError as exc:
        raise ChannelImportError(str(exc)) from None
    sheet = _Sheet([table.headers, *table.rows])

    orders, warnings = [], []
    rows = sheet.rows
    i = 0
    last_sale = ""
    while i < len(rows):
        head = rows[i]
        i += 1
        if not sheet.get(head, "sale_id"):
            where = f"after sale {last_sale}" if last_sale else "before the first sale"
            warnings.append(("", f"A row with no sale number {where} was skipped."))
            continue
        last_sale = sheet.get(head, "sale_id")
        pack = _PACK.match(_norm(sheet.get(head, "status")))
        if not pack:
            orders.append(_order(sheet, head, [head], warnings))
            continue
        expected = int(pack.group(1))
        items = []
        while len(items) < expected and i < len(rows):
            row = rows[i]
            # A pack's product rows carry no revenue of their own; a row that
            # does is the next sale, so the pack is short.
            if (
                not sheet.get(row, "sale_id")
                or _PACK.match(_norm(sheet.get(row, "status")))
                or sheet.get(row, "product_revenue")
            ):
                break
            items.append(row)
            i += 1
        sale_id = sheet.get(head, "sale_id")
        if items:
            last_sale = sheet.get(items[-1], "sale_id")
        if 0 < len(items) < expected:
            warnings.append(
                (sale_id, f"Expected {expected} products, found {len(items)}.")
            )
        # A pack with no products still goes on, to be refused with a reason.
        orders.append(_order(sheet, head, items, warnings))
    return ChannelReport(orders=orders, warnings=warnings)
