"""Load a Mercado Libre bulk-edit report into the catalog.

ML exports a seller's catalog as two workbooks:

* characteristics: one sheet per category. Row 1 holds ML's attribute ids,
  row 2 each column's kind (FIXED, VARIATION, ATTRIBUTE, ATTRIBUTE_UNIT), row 4
  the labels (yellow fill for mandatory fields, blue for characteristics), and
  data starts on row 5, one row per variant.
* prices: one sheet, `Publicaciones`. A classic item has a header row and one
  row per variation; a "user product" item is a single row. Prices and sale
  conditions sit here, keyed by item and product number.

Both use formulas to repeat a value from an earlier row (`=M6`,
`=K11&"     "`). The cached results in the file are not trustworthy (ML
leaves them at 0), so references are resolved here instead.

Both files are required: a variant needs its characteristics and its price
and sale conditions. A row that has only one of them, or whose price (column
I, `PRICE`; every other price in the report is ignored) is missing or 0, is
rejected and reported, never loaded. Variants are grouped into one
`CatalogProduct` by ML's family id, or by item id for a classic item. Size
and stock go to the variant's own columns ("Unique" and 0 when the report has
none), the price is the variant's one price and its currency is the org's.
Two publications of one variant (same size and axis values) load once: from
the active one; when both or neither are active, from the one the channel
updated last (a `LAST_UPDATED` column, which ML's bulk-edit export does not
have), and failing that from the one later in the file. The other is ignored
with a warning. ML's variant labels (`Rojo 12-18 M
Plim Plim`) can name an axis the characteristics sheet leaves out; that part
is recovered as a size when it reads as one and as the design name otherwise,
so two variants never collapse into one. Re-running an import updates what it
created before, matched by ML ids, so fixing the org's currency and importing
again reprices nothing but the currency. See docs/architecture/catalog.md.
"""

import datetime
import io
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import openpyxl
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from openpyxl.utils import column_index_from_string

from catalog.models import (
    Attribute,
    AttributeValue,
    CatalogProduct,
    Category,
    CategoryAttribute,
    ChannelAttribute,
    Listing,
    ListingVariant,
    SalesChannel,
)
from catalog.services import (
    CatalogError,
    axis_links,
    coerce_values,
    save_variant,
    set_many,
    set_values,
)
from catalog.text import UNIQUE_SIZE, canonical_size, normalize_key, size_sort_order
from common.org_time import resolve_timezone_name
from common.spreadsheet import SpreadsheetError

CHANNEL_CODE = "mercadolibre"
YELLOW = "FFFFF158"
PRICE_SHEET = "Publicaciones"
SKIPPED_SHEETS = {"Ayuda", "hidden", "dropdown_options_hidden"}
# Per file: an upload's size, what its zip may unpack to, and a sheet's rows.
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 600 * 1024 * 1024
MAX_ZIP_ENTRIES = 1000
MAX_ROWS = 200_000
MAX_FORMULA_DEPTH = 25
ROW = "_sheet_row"
# Marks a row rejected for its price: it still decides which publication of
# its variant wins (see `_winners`), but is never loaded.
UNPRICED = "_unpriced"
# When the channel last changed a publication (ML's API calls it
# `last_updated`). Read from either file when present.
UPDATED_COLUMN = "LAST_UPDATED"
# Publications whose sizes are bare numbers of months, not years: the store's
# decision on the "Mi Primer Año Nuevo" baby sets, sized 3, 6, 9 and 12.
MONTH_SIZED_PUBLICATIONS = {"MLM4571938512", "MLM2646408225"}

OPT, TXT, NUM, YES_NO = (
    Attribute.OPTION,
    Attribute.TEXT,
    Attribute.NUMBER,
    Attribute.BOOLEAN,
)

# ML attribute id -> (code, name, data type, multi-valued). SIZE is absent on
# purpose: a size is a column on the variant, not an attribute. Ids that mean the
# same thing in different categories share a code, so "material" is one
# attribute whether ML calls it MAIN_MATERIAL, MATERIAL or MATERIALS. An id
# missing here still imports, under its own lower-cased id and label.
ML_ATTRIBUTES = {
    "COLOR": ("color", "Color", OPT, False),
    "FABRIC_DESIGN": ("fabric_design", "Diseño de la tela", OPT, False),
    "PATTERN_NAME": ("pattern_name", "Nombre del diseño", OPT, False),
    "MAIN_COLOR": ("main_color", "Color principal", OPT, False),
    "BRAND": ("brand", "Marca", OPT, False),
    "MODEL": ("model", "Modelo", TXT, False),
    "LINE": ("line", "Línea", OPT, False),
    "GENDER": ("gender", "Género", OPT, False),
    "CHARACTER": ("character", "Personaje", OPT, False),
    "OCCASIONS": ("occasions", "Ocasión", OPT, True),
    "STYLES": ("styles", "Estilos", OPT, True),
    "RECOMMENDED_USES": ("recommended_uses", "Usos recomendados", OPT, True),
    "SLEEVE_TYPE": ("sleeve_type", "Tipo de manga", OPT, False),
    "LENGTH_TYPE": ("length_type", "Tipo de largo", OPT, False),
    "NECK_TYPE": ("neck_type", "Tipo de cuello", OPT, False),
    "SWEATSHIRTS_AND_HOODIES_COLLAR_TYPE": ("neck_type", "Tipo de cuello", OPT, False),
    "LEG_TYPE": ("leg_type", "Tipo de pierna", OPT, False),
    "HEM_TYPE": ("hem_type", "Tipo de terminación", OPT, False),
    "DESIGN_DETAILS": ("design_details", "Detalles de diseño", OPT, True),
    "CLOSURE_TYPES": ("closure_types", "Tipos de cierre", OPT, True),
    "DRESS_TYPE": ("dress_type", "Tipo de vestido", OPT, False),
    "GARMENT_TYPE": ("garment_type", "Tipo de prenda", OPT, False),
    "WEDGE_SHAPE": ("fit", "Forma del calce", OPT, False),
    "KITCHEN_APRON_TYPE": ("apron_type", "Tipo de delantal", OPT, False),
    "MAIN_MATERIAL": ("material", "Material", OPT, False),
    "MATERIAL": ("material", "Material", OPT, False),
    "MATERIALS": ("material", "Material", OPT, False),
    "BABY_BODYSUIT_MATERIAL": ("material", "Material", OPT, False),
    "COMPOSITION": ("composition", "Composición", TXT, False),
    "RELEASE_SEASON": ("release_season", "Temporada de lanzamiento", OPT, False),
    "RELEASE_YEAR": ("release_year", "Año de lanzamiento", NUM, False),
    "RECOMMENDED_AGE_GROUP": ("age_group", "Edad recomendada", OPT, False),
    "SALE_FORMAT": ("sale_format", "Formato de venta", OPT, False),
    "UNITS_PER_PACK": ("units_per_pack", "Unidades por paquete", NUM, False),
    "BABY_BODYSUITS_NUMBER_BY_PACK": (
        "units_per_pack",
        "Unidades por paquete",
        NUM,
        False,
    ),
    "COSTUMES_NUMBER": ("units_per_pack", "Unidades por paquete", NUM, False),
    "PIECES_NUMBER": ("pieces_number", "Cantidad de piezas", NUM, False),
    "PIECES_INCLUDED": ("pieces_included", "Piezas incluidas", OPT, True),
    "ACCESSORIES_INCLUDED": ("accessories_included", "Accesorios incluidos", OPT, True),
    "POCKETS_NUMBER": ("pockets_number", "Cantidad de bolsillos", NUM, False),
    "WIDTH": ("width", "Ancho", NUM, False),
    "HEIGHT": ("height", "Altura", NUM, False),
    "IS_SPORTIVE": ("is_sportive", "Es deportiva", YES_NO, False),
    "IS_SUITABLE_FOR_PREGNANCY": ("for_pregnancy", "Apta para embarazo", YES_NO, False),
    "WITH_RECYCLED_MATERIALS": (
        "recycled_materials",
        "Materiales reciclados",
        YES_NO,
        False,
    ),
    "IS_OVERSIZED_FIT": ("is_oversized", "Es oversize", YES_NO, False),
    "IS_OVERSIZE": ("is_oversized", "Es oversize", YES_NO, False),
    "WITH_EMBROIDERY": ("with_embroidery", "Con bordado", YES_NO, False),
    "WITH_HOOD": ("with_hood", "Con capucha", YES_NO, False),
    "IS_A_NEWBORN_SET": ("is_newborn_set", "Es set de nacimiento", YES_NO, False),
    "INCLUDES_ACCESSORIES": (
        "includes_accessories",
        "Incluye accesorios",
        YES_NO,
        False,
    ),
    "WITH_NECK_ADJUSTMENT": ("neck_adjustment", "Ajuste en el cuello", YES_NO, False),
    "WITH_WAIST_ADJUSTMENT": (
        "waist_adjustment",
        "Ajuste en la cintura",
        YES_NO,
        False,
    ),
    "IS_SUITABLE_FOR_WASHING_MACHINE": (
        "machine_washable",
        "Apto lavadora",
        YES_NO,
        False,
    ),
    "IS_HYPOALLERGENIC_PRODUCT": ("hypoallergenic", "Hipoalergénico", YES_NO, False),
}

# Columns that identify a row rather than describe the product.
FIXED = {
    "FAMILY_ID",
    "ID",
    "PRODUCT_NUMBER",
    "SKU",
    "VARIATION_ID",
    "DOMAIN_ID",
    "CATEGORY_ID",
    "PARENT_CATEGORY_ID",
    "TITLE",
}
# Only a real code is stored; ML's prompt or a reason for having none is not.
GTIN_CODE = re.compile(r"^\d{8,14}$")
VARIATION_SUFFIX = "_VARIATION-COLUMN"
UNIT_SUFFIX = "_UNIT"

_REF = re.compile(r"\$?([A-Z]{1,3})\$?(\d+)")
_STRING = re.compile(r'"((?:[^"]|"")*)"')


# Reading ---------------------------------------------------------------------


class _Sheet:
    """A worksheet read once, in openpyxl's streaming mode: its values as a
    grid (formulas as written), and the fill of each cell of the label row.
    A 20 MB report loaded whole would take gigabytes; this keeps only the
    values."""

    def __init__(self, ws, shown):
        self.title = ws.title
        self.state = getattr(ws, "sheet_state", "visible")
        # A sheet's declared size is not trusted (see common/spreadsheet.py).
        ws.reset_dimensions()
        self.rows = []
        for number, values in enumerate(ws.iter_rows(values_only=True), 1):
            if number > MAX_ROWS:
                raise SpreadsheetError(
                    f"{shown}: sheet {ws.title} has more than {MAX_ROWS:,} rows."
                )
            self.rows.append(values)
        while self.rows and not any(v is not None for v in self.rows[-1]):
            self.rows.pop()
        self.max_row = len(self.rows)
        self.max_column = max((len(r) for r in self.rows), default=0)
        self.fills = {}
        for row in ws.iter_rows(min_row=4, max_row=4):
            for col, cell in enumerate(row, 1):
                fill = getattr(cell, "fill", None)
                if fill is not None and fill.fill_type:
                    self.fills[col] = fill.fgColor.rgb

    def value(self, row, col):
        if row > len(self.rows):
            return None
        values = self.rows[row - 1]
        return values[col - 1] if col <= len(values) else None


class _Resolver:
    """Cell values with simple formulas worked out: a reference (`=M6`) or a
    concatenation of references and string literals (`=K11&"  "`). Anything
    else (ML's fee formula) resolves to None, as an empty cell would."""

    def __init__(self, sheet):
        self.sheet = sheet

    def value(self, row, col, depth=0):
        return self._evaluate(self.sheet.value(row, col), depth)

    def _evaluate(self, raw, depth):
        if not (isinstance(raw, str) and raw.startswith("=")):
            return raw
        if depth > MAX_FORMULA_DEPTH:
            return None
        parts = []
        for term in raw[1:].split("&"):
            term = term.strip()
            if m := _STRING.fullmatch(term):
                parts.append(m[1].replace('""', '"'))
            elif m := _REF.fullmatch(term):
                parts.append(
                    self.value(int(m[2]), column_index_from_string(m[1]), depth + 1)
                )
            else:
                return None
        if len(parts) == 1:
            return parts[0]
        return "".join("" if p is None else _text(p) for p in parts)


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _check_zip(data, shown):
    """An .xlsx is a zip. What it unpacks to is capped, so a crafted file
    cannot exhaust the worker's memory; a real report unpacks to about ten
    times its size."""
    buffer = io.BytesIO(data)
    if not zipfile.is_zipfile(buffer):
        raise SpreadsheetError(f"{shown} is not an .xlsx workbook.")
    try:
        with zipfile.ZipFile(buffer) as archive:
            entries = archive.infolist()
    except (zipfile.BadZipFile, OSError):
        raise SpreadsheetError(f"{shown} is not an .xlsx workbook.") from None
    if (
        len(entries) > MAX_ZIP_ENTRIES
        or sum(e.file_size for e in entries) > MAX_UNPACKED_BYTES
    ):
        raise SpreadsheetError(f"{shown} is too large to read.")


def check_upload(source, what):
    """The bytes of an uploaded `what` file, or the reason it is refused: the
    checks that need no workbook parsing, so a request can answer at once.
    An upload's errors never quote its name, which the sender chose."""
    if not source:
        raise SpreadsheetError(f"The {what} file is required.")
    if not (getattr(source, "name", "") or "").lower().endswith(".xlsx"):
        raise SpreadsheetError(f"The {what} file must be an .xlsx workbook.")
    data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise SpreadsheetError(
            f"The {what} file exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MB limit."
        )
    if not data:
        raise SpreadsheetError(f"The {what} file is empty.")
    _check_zip(data, f"The {what} file")
    return data


def _open(source, what):
    """The sheets of the workbook in `source`: a path (the command) or an
    uploaded file (the web)."""
    if not source:
        raise SpreadsheetError(f"The {what} file is required.")
    if hasattr(source, "read"):
        data = check_upload(source, what)
        shown = f"The {what} file"
    else:
        path = Path(source)
        if not path.is_file():
            raise SpreadsheetError(f"The {what} file {path.name} was not found.")
        data = path.read_bytes()
        shown = path.name
        if not data:
            raise SpreadsheetError(f"The {what} file is empty.")
        _check_zip(data, shown)
    try:
        wb = openpyxl.load_workbook(
            io.BytesIO(data), read_only=True, data_only=False, keep_links=False
        )
    except Exception:
        raise SpreadsheetError(f"{shown} could not be opened.") from None
    try:
        return [_Sheet(ws, shown) for ws in wb.worksheets]
    except SpreadsheetError:
        raise
    except Exception:
        raise SpreadsheetError(f"{shown} could not be read.") from None
    finally:
        wb.close()


@dataclass
class Column:
    index: int
    code: str
    kind: str
    label: str
    mandatory: bool


@dataclass
class CategorySheet:
    name: str
    columns: list
    rows: list
    domain: str = ""
    unpriced: list = field(default_factory=list)
    rejected: int = 0


def read_characteristics(path):
    """One `CategorySheet` per category sheet: a visible sheet whose row 1
    names ML's `ID` and `PRODUCT_NUMBER` columns. A file with none is not a
    characteristics report (the prices file given by mistake, for one)."""
    sheets = []
    for ws in _open(path, "characteristics"):
        if ws.title in SKIPPED_SHEETS or ws.state != "visible":
            continue
        columns = []
        for col in range(1, ws.max_column + 1):
            code = _text(ws.value(1, col))
            if not code:
                continue
            columns.append(
                Column(
                    index=col,
                    code=code,
                    kind=_text(ws.value(2, col)),
                    label=_text(ws.value(4, col)).split("\n")[0].strip(),
                    mandatory=ws.fills.get(col) == YELLOW,
                )
            )
        if not {"ID", "PRODUCT_NUMBER"} <= {c.code for c in columns}:
            continue
        resolver = _Resolver(ws)
        rows = []
        for r in range(5, ws.max_row + 1):
            row = {c.code: _text(resolver.value(r, c.index)) for c in columns}
            if row.get("ID"):
                row[ROW] = r
                rows.append(row)
        sheets.append(CategorySheet(name=ws.title, columns=columns, rows=rows))
    if not sheets:
        raise SpreadsheetError("The characteristics file has no category sheets.")
    return sheets


def read_prices(path):
    """`(by_product_number, headers_by_item)`: the row of each variant, and
    the header row of each classic item (whose variations inherit from it)."""
    ws = next((s for s in _open(path, "prices") if s.title == PRICE_SHEET), None)
    if ws is None:
        raise SpreadsheetError(f"The prices file has no {PRICE_SHEET} sheet.")
    codes = {col: _text(ws.value(1, col)) for col in range(1, ws.max_column + 1)}
    resolver = _Resolver(ws)
    by_product, headers = {}, {}
    for r in range(7, ws.max_row + 1):
        row = {code: resolver.value(r, col) for col, code in codes.items() if code}
        item = _text(row.get("ITEM_ID"))
        if not item:
            continue
        row[ROW] = r
        product_number = _text(row.get("PRODUCT_NUMBER"))
        if product_number:
            by_product[product_number] = row
        else:
            headers[item] = row
    return by_product, headers


# Values ----------------------------------------------------------------------


def _decimal(value):
    text = _text(value)
    if not text or text == "-":
        return None
    try:
        number = Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def _int(value):
    number = _decimal(value)
    return int(number) if number is not None else None


def _ml_code(column):
    code = column.code
    if code.endswith(VARIATION_SUFFIX):
        code = code[: -len(VARIATION_SUFFIX)]
    return code


def _infer_type(values):
    keys = {normalize_key(v) for v in values}
    if keys <= {"si", "no"}:
        return YES_NO
    try:
        for v in values:
            Decimal(v.replace(",", "."))
        return NUM
    except InvalidOperation:
        return TXT


def _split(attribute, text):
    if attribute.is_multivalued:
        return [part.strip() for part in text.split(",") if part.strip()]
    return text


def _label_parts(label, classic):
    label = _text(label)
    if not label or label == "-":
        return []
    if classic:
        return [p.strip() for p in label.split(" / ") if p.strip()]
    return [label]


def _leftover(label, classic, axis_texts):
    """What the channel's variant label says beyond the known axis values."""
    known = [normalize_key(t) for t in axis_texts if t]
    rest = []
    for part in _label_parts(label, classic):
        key = normalize_key(part)
        if classic:
            if key not in known:
                rest.append(part)
            continue
        for value in sorted(known, key=len, reverse=True):
            key = re.sub(rf"(?<!\S){re.escape(value)}(?!\S)", " ", key, count=1)
        if key.strip():
            # Read the leftover back out of the original, to keep its case.
            words = [w for w in part.split() if normalize_key(w) in key.split()]
            rest.append(" ".join(words) or key.strip())
    return rest


# Loading ---------------------------------------------------------------------


@dataclass
class ImportReport:
    created: Counter = field(default_factory=Counter)
    updated: Counter = field(default_factory=Counter)
    notes: Counter = field(default_factory=Counter)
    warnings: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    empty_categories: list = field(default_factory=list)

    def as_dict(self):
        return {
            "created": dict(self.created),
            "updated": dict(self.updated),
            "notes": dict(self.notes),
            "empty_categories": list(self.empty_categories),
            "warnings": list(self.warnings),
            "rejected": list(self.rejected),
        }


class _Loader:
    def __init__(self, org, report):
        self.org = org
        self.report = report
        self.attributes = {}
        self.mapped = {}
        # A listing publishing several variants is met once per variant row:
        # written the first time, by id.
        self.listings_seen = {}
        self._axes = {}

    def channel(self, site):
        channel, created = SalesChannel.objects.get_or_create(
            org=self.org,
            code=CHANNEL_CODE,
            defaults={
                "name": "Mercado Libre",
                "kind": SalesChannel.ONLINE,
                "site": site,
            },
        )
        if channel.kind != SalesChannel.ONLINE:
            raise SpreadsheetError(
                f"The sales channel with code {CHANNEL_CODE} is a physical store, "
                "so it cannot hold Mercado Libre listings. Give it another code."
            )
        self._count("channels", created)
        self.channel_obj = channel
        # What earlier imports left on this channel, read once: the variant
        # each product number publishes, and each listing by its id.
        self.published = {
            link.external_product_id: link.variant
            for link in ListingVariant.objects.filter(listing__channel=channel)
            .exclude(external_product_id="")
            .select_related("variant")
        }
        self.listings = {
            listing.external_id: listing
            for listing in Listing.objects.filter(channel=channel)
        }
        return channel

    def _count(self, kind, created):
        (self.report.created if created else self.report.updated)[kind] += 1

    def attribute_for(self, ml_code, domain, label, values, *, unit=""):
        """The shared attribute ML's `ml_code` feeds in `domain`, created on
        first use, and the mapping row that records the link."""
        cache_key = (ml_code, domain)
        if cache_key in self.mapped:
            return self.mapped[cache_key]
        mapping = (
            ChannelAttribute.objects.filter(
                channel=self.channel_obj,
                external_code=ml_code,
                external_domain__in=(domain, ""),
            )
            .select_related("attribute")
            .order_by("-external_domain")
            .first()
        )
        if mapping is not None:
            attribute = mapping.attribute
        else:
            code, name, data_type, multi = ML_ATTRIBUTES.get(
                ml_code,
                (ml_code.lower()[:64], label or ml_code, _infer_type(values), False),
            )
            attribute = self.attributes.get(code)
            if attribute is None:
                attribute, created = Attribute.objects.get_or_create(
                    org=self.org,
                    code=code,
                    defaults={
                        "name": name[:150],
                        "data_type": data_type,
                        "is_multivalued": multi,
                        "unit": unit,
                    },
                )
                self._count("attributes", created)
            ChannelAttribute.objects.create(
                org=self.org,
                channel=self.channel_obj,
                attribute=attribute,
                external_code=ml_code,
                external_domain=domain,
            )
            self.report.created["channel_attributes"] += 1
        attribute = (
            Attribute.objects.filter(pk=attribute.pk).prefetch_related("options").get()
        )
        self.attributes[attribute.code] = attribute
        self.mapped[cache_key] = attribute
        return attribute

    def link(self, category, attribute, role, required, order):
        link, created = CategoryAttribute.objects.get_or_create(
            org=self.org,
            category=category,
            attribute=attribute,
            defaults={"role": role, "is_required": required, "sort_order": order},
        )
        if created:
            self.report.created["category_attributes"] += 1
        elif role == CategoryAttribute.VARIANT_AXIS and link.role != role:
            link.role = role
            link.save(update_fields=["role", "updated_at"])
        else:
            return link
        self._axes.pop(category.pk, None)
        return link

    def axis_links(self, category):
        """The category's axis links, read once until a link changes."""
        if category.pk not in self._axes:
            self._axes[category.pk] = axis_links(category)
        return self._axes[category.pk]


def _site_of(item_ids):
    for item in item_ids:
        m = re.match(r"^([A-Z]{3})\d", item)
        if m:
            return m[1]
    return ""


def import_mercadolibre(org, characteristics_path, prices_path, *, dry_run=False):
    """Load both ML reports into `org`'s catalog and return an `ImportReport`.
    Both files are required. With `dry_run` everything is rolled back after
    the report is built."""
    sheets = read_characteristics(characteristics_path)
    prices, headers = read_prices(prices_path)
    report = ImportReport()
    try:
        with transaction.atomic():
            _load(org, sheets, prices, headers, report)
            if dry_run:
                raise _DryRun
    except _DryRun:
        pass
    return report


class _DryRun(Exception):
    pass


def _rejection(row, price_row):
    """Why `row` cannot be loaded, or None. Every variant has a price."""
    if price_row is None:
        return "it is not in the prices file, so it has no price or sale conditions"
    price = _decimal(price_row.get("PRICE"))
    if price is None:
        return "it has no price"
    if price <= 0:
        return "its price is 0"
    return None


def _accepted(sheets, prices, report):
    """Every sheet with only its loadable rows; each rejected row is named.
    So is a prices row with no characteristics row."""
    kept, seen = [], set()
    for sheet in sheets:
        rows, unpriced = [], []
        for row in sheet.rows:
            seen.add(row["PRODUCT_NUMBER"])
            price_row = prices.get(row["PRODUCT_NUMBER"])
            reason = _rejection(row, price_row)
            if reason is None:
                rows.append(row)
                continue
            report.rejected.append(
                f"{sheet.name}, row {row[ROW]}: publication {row['ID']}, "
                f"product {row['PRODUCT_NUMBER']} was not loaded: {reason}."
            )
            if price_row is not None:
                unpriced.append({**row, UNPRICED: True})
        domain = next((r["DOMAIN_ID"] for r in sheet.rows if r.get("DOMAIN_ID")), "")
        kept.append(
            CategorySheet(
                sheet.name,
                sheet.columns,
                rows,
                domain,
                unpriced,
                rejected=len(sheet.rows) - len(rows),
            )
        )
    for number, price_row in prices.items():
        if number not in seen:
            report.rejected.append(
                f"{PRICE_SHEET}, row {price_row[ROW]}: publication "
                f"{_text(price_row.get('ITEM_ID'))}, product {number} was not loaded: "
                "it is not in the characteristics file, so it has no characteristics."
            )
    return kept


def _load(org, sheets, prices, headers, report):
    sheets = _accepted(sheets, prices, report)
    loader = _Loader(org, report)
    all_items = [row["ID"] for sheet in sheets for row in sheet.rows]
    channel = loader.channel(_site_of(all_items))

    # Which ML columns hold anything at all in the rows being loaded. A column
    # nobody filled in gets no attribute, no link and no mapping.
    filled = defaultdict(list)
    for sheet in sheets:
        for row in sheet.rows:
            for column in sheet.columns:
                text = row.get(column.code, "")
                if text and column.kind != "FIXED":
                    filled[_ml_code(column)].append(text)
    filled.pop("GTIN", None)

    tz = ZoneInfo(resolve_timezone_name(org))
    for sheet in sheets:
        category, loaded = _load_sheet(
            loader, channel, sheet, filled, prices, headers, tz
        )
        if not loaded:
            report.empty_categories.append(
                {
                    "category": category.name,
                    "sheet": sheet.name,
                    "rows": sheet.rejected + len(sheet.rows),
                    "rejected": sheet.rejected,
                }
            )


def _category_name(sheet, prices):
    labels = Counter(
        _text((prices.get(row["PRODUCT_NUMBER"]) or {}).get("CATEGORY"))
        for row in sheet.rows
    )
    labels.pop("", None)
    return (labels.most_common(1)[0][0] if labels else sheet.name)[:150]


def _load_sheet(loader, channel, sheet, filled, prices, headers, tz):
    """A category per sheet, even one none of whose rows could be loaded,
    linked to the shared attribute of each column some loaded row filled in;
    then its products. Returns the category and how many products loaded."""
    org = loader.org
    category, created = Category.objects.get_or_create(
        org=org, name=_category_name(sheet, prices)
    )
    loader._count("categories", created)
    domain = sheet.domain

    units = {
        c.code[: -len(UNIT_SUFFIX)]: c.code
        for c in sheet.columns
        if c.kind == "ATTRIBUTE_UNIT"
    }
    size_column = None
    axes, traits = [], []
    for order, column in enumerate(sheet.columns):
        ml_code = _ml_code(column)
        if column.kind == "VARIATION" and ml_code == "SIZE":
            size_column = column
            continue
        if column.kind not in ("VARIATION", "ATTRIBUTE") or ml_code not in filled:
            continue
        values = filled[ml_code]
        unit_values = (
            [r.get(units[ml_code], "") for r in sheet.rows] if ml_code in units else []
        )
        unit = next((u for u in unit_values if u), "")
        attribute = loader.attribute_for(
            ml_code, domain, column.label, values, unit=unit
        )
        if column.kind == "VARIATION":
            loader.link(
                category,
                attribute,
                CategoryAttribute.VARIANT_AXIS,
                column.mandatory,
                order,
            )
            axes.append((column, attribute))
        else:
            loader.link(
                category, attribute, CategoryAttribute.CHARACTERISTIC, False, order
            )
            traits.append((column, attribute))

    groups = defaultdict(list)
    for row in sheet.rows + sheet.unpriced:
        key = (
            f"family:{row['FAMILY_ID']}"
            if row.get("FAMILY_ID")
            else f"item:{row['ID']}"
        )
        groups[key].append(row)

    context = SimpleNamespace(
        sheet=sheet.name,
        loader=loader,
        channel=channel,
        category=category,
        size_column=size_column,
        axes=axes,
        traits=traits,
        units=units,
        prices=prices,
        headers=headers,
        tz=tz,
    )
    loaded = 0
    for rows in groups.values():
        loaded += _load_group(context, rows)
    return category, loaded


def _row_characteristics(row, traits, units):
    """`{attribute: rows}` for one spreadsheet row, empty cells left out."""
    values = {}
    for column, attribute in traits:
        text = row.get(column.code, "")
        if not text:
            continue
        raw = _split(attribute, text)
        unit_code = units.get(column.code)
        if attribute.data_type == NUM and unit_code and row.get(unit_code):
            raw = {"value": raw, "unit": row[unit_code]}
        values[attribute] = coerce_values(attribute, raw, create_options=True)
    return values


def _same(rows_a, rows_b):
    def marker(rows):
        return sorted(
            (
                str(r["option"].pk) if r.get("option") else "",
                r.get("value_text", ""),
                str(r["value_number"].normalize())
                if r.get("value_number") is not None
                else "",
                str(r.get("value_boolean", "")),
                r.get("unit", ""),
            )
            for r in rows
        )

    return marker(rows_a) == marker(rows_b)


def _find_product(channel, item_ids):
    listing = (
        Listing.objects.filter(channel=channel, external_id__in=item_ids)
        .select_related("product")
        .first()
    )
    return listing.product if listing else None


def _read_axes(ctx, row, price_row):
    """`(size, axes, recovered)` read from a row without writing anything:
    its size in the store's spelling ("Unique" when none), `{ml_code: text}`
    of its axis values, and what ML's label adds (`[(kind, text)]`, kind
    "size" or "design")."""
    size, texts, axes, recovered = "", [], {}, []
    if ctx.size_column is not None and row.get(ctx.size_column.code):
        raw = row[ctx.size_column.code]
        texts.append(raw)
        if row["ID"] in MONTH_SIZED_PUBLICATIONS and raw.strip().isdigit():
            raw = f"{raw.strip()} meses"
        size = canonical_size(raw)
    for column, _ in ctx.axes:
        if text := row.get(column.code, ""):
            axes[_ml_code(column)] = text
            texts.append(text)
    classic = bool(row.get("VARIATION_ID"))
    for part in _leftover(price_row.get("VARIATIONS"), classic, texts):
        if not size and size_sort_order(canonical_size(part)) < 800_000:
            size = canonical_size(part)
            recovered.append(("size", part))
        elif "PATTERN_NAME" not in axes:
            axes["PATTERN_NAME"] = part
            recovered.append(("design", part))
        else:
            recovered.append(("unmatched", part))
    return size or UNIQUE_SIZE, axes, recovered


def _identity(size, axes):
    return (
        normalize_key(size),
        tuple(sorted((code, normalize_key(text)) for code, text in axes.items())),
    )


def _status_of(ctx, row, price_row):
    return _status((ctx.headers.get(row["ID"]) or price_row).get("STATUS"))


def _channel_date(value, tz):
    """A date the channel wrote, as an aware datetime, or None. A date or time
    without a zone is read in the org's timezone."""
    if isinstance(value, datetime.datetime):
        when = value
    elif isinstance(value, datetime.date):
        when = datetime.datetime.combine(value, datetime.time())
    else:
        text = _text(value)
        if not text:
            return None
        when = None
        try:
            when = parse_datetime(text.replace("Z", "+00:00"))
            if when is None and (day := parse_date(text)):
                when = datetime.datetime.combine(day, datetime.time())
        except ValueError:
            return None
        if when is None:
            for pattern in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
                try:
                    when = datetime.datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
        if when is None:
            return None
    if timezone.is_naive(when):
        when = timezone.make_aware(when, tz)
    return when


def _updated_on_channel(ctx, row, price_row):
    for source in (price_row, ctx.headers.get(row["ID"]), row):
        if source and (when := _channel_date(source.get(UPDATED_COLUMN), ctx.tz)):
            return when
    return None


def _winners(ctx, rows):
    """The rows to load: one per variant. Two publications of one variant
    (same size and axis values) load once: from the active one; when both or
    neither are active, from the one the channel updated last if every one of
    them has that date, and otherwise from the one later in the file. The
    others are ignored with a warning, and any link an earlier import made to
    them is removed, so only the winner's information remains. A winner
    rejected for its price loads nothing: its variant keeps what it had, and
    its twins are still ignored."""
    report = ctx.loader.report
    by_variant = defaultdict(list)
    for row in rows:
        price_row = ctx.prices[row["PRODUCT_NUMBER"]]
        size, axes, _ = _read_axes(ctx, row, price_row)
        by_variant[_identity(size, axes)].append(row)
    winners = []
    for twins in by_variant.values():
        winners.append(twins[0])
        if len(twins) == 1:
            continue
        facts = {}
        for row in twins:
            price_row = ctx.prices[row["PRODUCT_NUMBER"]]
            facts[row[ROW]] = (
                _status_of(ctx, row, price_row) == Listing.ACTIVE,
                _updated_on_channel(ctx, row, price_row),
            )
        active = [r for r in twins if facts[r[ROW]][0]]
        pool = active or twins
        dated = all(facts[r[ROW]][1] for r in pool)
        winner = max(
            pool,
            key=lambda r: (facts[r[ROW]][1], r[ROW]) if dated else r[ROW],
        )
        winners[-1] = winner
        for loser in twins:
            if loser is winner:
                continue
            if active and not facts[loser[ROW]][0]:
                why = "that one is active"
            elif dated:
                why = f"{ctx.channel.name} updated that one last"
            else:
                why = "that one comes later in the file"
            report.notes["duplicate_publications_ignored"] += 1
            report.warnings.append(
                f"{ctx.sheet}, row {loser[ROW]}: publication {loser['ID']}, product "
                f"{loser['PRODUCT_NUMBER']} is the same variant as publication "
                f"{winner['ID']}, product {winner['PRODUCT_NUMBER']}, and was "
                f"ignored: {why}."
            )
            ListingVariant.objects.filter(
                listing__channel=ctx.channel,
                external_product_id=loser["PRODUCT_NUMBER"],
            ).delete()
            ctx.loader.published.pop(loser["PRODUCT_NUMBER"], None)
            Listing.objects.filter(
                channel=ctx.channel, external_id=loser["ID"], variants__isnull=True
            ).delete()
            cached = ctx.loader.listings.get(loser["ID"])
            if cached and not Listing.objects.filter(pk=cached.pk).exists():
                del ctx.loader.listings[loser["ID"]]
    return sorted((r for r in winners if not r.get(UNPRICED)), key=lambda r: r[ROW])


def _load_group(ctx, rows):
    org, report = ctx.loader.org, ctx.loader.report
    rows = _winners(ctx, rows)
    if not rows:
        return 0
    item_ids = list(dict.fromkeys(row["ID"] for row in rows))
    first = rows[0]
    priced = [ctx.prices[row["PRODUCT_NUMBER"]] for row in rows]
    # A classic item's variation rows may leave the description to its header.
    sources = priced + [ctx.headers.get(item) for item in item_ids]
    descriptions = Counter(
        _text(p.get("DESCRIPTION"))
        for p in sources
        if p and _text(p.get("DESCRIPTION"))
    )

    product = _find_product(ctx.channel, item_ids)
    if product is None:
        product = CatalogProduct.objects.create(
            org=org,
            name=first["TITLE"][:255],
            category=ctx.category,
            description=descriptions.most_common(1)[0][0] if descriptions else "",
        )
        report.created["products"] += 1
    else:
        product.name = first["TITLE"][:255]
        if descriptions:
            product.description = descriptions.most_common(1)[0][0]
        product.save(update_fields=["name", "description", "updated_at"])
        report.updated["products"] += 1
        if product.category_id != ctx.category.id:
            report.warnings.append(
                f"{product.name}: kept its category {product.category.name}, "
                f"the report files it under {ctx.category.name}."
            )

    # Characteristics every loaded row agrees on belong to the product, once;
    # the rest to the variants that have them, once each. Re-importing
    # replaces what ML supplied: the product's rows here, each loaded
    # variant's own in `_load_variant`. A rejected row's variant keeps what
    # it had.
    per_row = [_row_characteristics(r, ctx.traits, ctx.units) for r in rows]
    ml_attributes = {a for _, a in ctx.traits}
    AttributeValue.objects.filter(
        product=product, variant__isnull=True, attribute__in=ml_attributes
    ).delete()
    shared = set()
    for attribute in ml_attributes:
        present = [values.get(attribute) for values in per_row]
        if all(present) and all(_same(present[0], p) for p in present[1:]):
            set_values(product, None, attribute, present[0])
            shared.add(attribute)

    for row, characteristics, price_row in zip(rows, per_row, priced, strict=True):
        _load_variant(
            ctx,
            product,
            row,
            price_row,
            {a: v for a, v in characteristics.items() if a not in shared},
            ml_attributes,
        )
    return 1


def _recovered_attribute(ctx, ml_code, domain):
    attribute = ctx.loader.attribute_for(ml_code, domain, "", ["x"])
    ctx.loader.link(ctx.category, attribute, CategoryAttribute.VARIANT_AXIS, False, 999)
    return attribute


def _axes_payload(ctx, row, axes, recovered):
    """`{attribute code: text}` for the row's axes, creating the design axis
    when only ML's label names it."""
    report = ctx.loader.report
    by_ml_code = {_ml_code(c): a for c, a in ctx.axes}
    for kind, part in recovered:
        if kind == "size":
            report.notes["sizes_recovered_from_label"] += 1
        elif kind == "design":
            report.notes["designs_recovered_from_label"] += 1
            if "PATTERN_NAME" not in by_ml_code:
                by_ml_code["PATTERN_NAME"] = _recovered_attribute(
                    ctx, "PATTERN_NAME", row.get("DOMAIN_ID", "")
                )
        else:
            report.warnings.append(
                f"{row['ID']}: the label part '{part}' matches no axis and was left out."
            )
    return {by_ml_code[code].code: text for code, text in axes.items()}


def _load_variant(ctx, product, row, price_row, characteristics, ml_attributes):
    org, report = ctx.loader.org, ctx.loader.report
    product_number = row["PRODUCT_NUMBER"]
    variant = ctx.loader.published.get(product_number)

    size, axes, recovered = _read_axes(ctx, row, price_row)
    payload = _axes_payload(ctx, row, axes, recovered)
    fields = {
        "price": _decimal(price_row.get("PRICE")),
        "currency": org.default_currency or None,
        "size": size,
        "stock": _int(price_row.get("QUANTITY")) or 0,
    }
    gtin = row.get("GTIN", "")
    if GTIN_CODE.match(gtin):
        fields["gtin"] = gtin
    sku = row.get("SKU") or _text(price_row.get("SKU"))
    if sku:
        from invoices.models import Product

        taken = Product.objects.filter(org=org, sku=sku)
        if variant is not None:
            taken = taken.exclude(pk=variant.pk)
        if taken.exists():
            report.warnings.append(f"{product_number}: SKU {sku} is taken, left blank.")
        else:
            fields["sku"] = sku[:100]
    for column, target in (
        ("SHIPPING_HEIGHT", "package_height_cm"),
        ("SHIPPING_WIDTH", "package_width_cm"),
        ("SHIPPING_DEPTH", "package_depth_cm"),
        ("SHIPPING_WEIGHT", "package_weight_kg"),
    ):
        number = _decimal(price_row.get(column))
        if number is not None:
            fields[target] = number

    def save(target):
        return save_variant(
            product,
            target,
            payload,
            fields,
            create_options=True,
            require_axes=False,
            known=ctx.loader.attributes,
            links=ctx.loader.axis_links(product.category),
        )

    try:
        variant_obj = save(variant)
    except CatalogError as exc:
        # This variant exists already under another publication (one an
        # earlier import loaded, and that this report ignores or no longer
        # has): this publication's information now describes it.
        if exc.existing is None:
            raise
        variant = exc.existing
        variant_obj = save(variant)
    ctx.loader._count("variants", variant is None)
    set_many(
        product,
        variant_obj,
        characteristics,
        clear=ml_attributes,
        fresh=variant is None,
    )

    _load_listing(ctx, product, variant_obj, row, price_row)


def _status(text):
    key = normalize_key(_text(text))
    return {"activa": Listing.ACTIVE, "inactiva": Listing.INACTIVE}.get(key, "")


def _condition(text):
    key = normalize_key(_text(text))
    return {"nuevo": Listing.NEW, "usado": Listing.USED}.get(key, "")


def _warranty(source):
    kind = _text(source.get("WARRANTY_TYPE"))
    time = _int(source.get("WARRANTY_TIME"))
    unit = _text(source.get("WARRANTY_TIME_UNIT"))
    if kind and time:
        return f"{kind}, {time} {unit}".strip()[:150]
    return kind[:150]


def _load_listing(ctx, product, variant, row, price_row):
    """The row's listing (written the first time this import meets it) and
    its link to the variant."""
    loader, org = ctx.loader, ctx.loader.org
    seen = loader.listings_seen.get(row["ID"])
    if seen is None:
        source = ctx.headers.get(row["ID"]) or price_row
        shipping = _text(source.get("SHIPPING_METHOD"))
        fields = {
            "org": org,
            "product": product,
            "external_group_id": row.get("FAMILY_ID", "")[:100],
            "external_category_id": row.get("CATEGORY_ID", "")[:100],
            "external_domain": row.get("DOMAIN_ID", "")[:100],
            "title": row["TITLE"][:255],
            "status": _status(source.get("STATUS")),
            "condition": _condition(source.get("CONDITION")),
            "listing_type": _text(source.get("LISTING_TYPE"))[:50],
            "shipping_mode": "" if shipping == "No disponible" else shipping[:100],
            "free_shipping": _text(source.get("SHIPPING_COST_MARKETPLACE"))
            == "Ofreces envío gratis",
            "warranty": _warranty(source),
            "handling_days": _int(source.get("MANUFACTURING_TIME")),
            "channel_updated_at": _updated_on_channel(ctx, row, price_row),
        }
        listing = loader.listings.get(row["ID"])
        created = listing is None
        if created:
            listing = Listing.objects.create(
                channel=ctx.channel, external_id=row["ID"], **fields
            )
            loader.listings[row["ID"]] = listing
        else:
            for name, value in fields.items():
                setattr(listing, name, value)
            listing.save()
        seen = loader.listings_seen[row["ID"]] = (listing, created)
        loader._count("listings", created)
    listing, new = seen
    link = {
        "org": org,
        "external_variation_id": row.get("VARIATION_ID", "")[:100],
        "external_product_id": row["PRODUCT_NUMBER"][:100],
        "external_sku": _text(price_row.get("SKU"))[:100],
    }
    loader.published[row["PRODUCT_NUMBER"]] = variant
    if new:
        # A listing this import created has no links to look for.
        ListingVariant.objects.create(listing=listing, variant=variant, **link)
        loader._count("listing_variants", True)
        return
    # A variant is published once per listing: drop a link this product
    # number had to another variant before (its axes changed since).
    ListingVariant.objects.filter(
        listing=listing, external_product_id=row["PRODUCT_NUMBER"]
    ).exclude(variant=variant).delete()
    _, created = ListingVariant.objects.update_or_create(
        listing=listing, variant=variant, defaults=link
    )
    loader._count("listing_variants", created)
