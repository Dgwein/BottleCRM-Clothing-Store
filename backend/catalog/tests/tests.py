"""Catalog tests: the data model, the Mercado Libre import, the API, sales
channels, deleting organizations and the behaviour the CRM already had. (+) marks a positive case (something that must
work), (-) a negative one (something that must be refused or must not happen).

A. Reading the Mercado Libre workbooks
   A1  (+) formulas that repeat a value (=M5, =K7&"  ") resolve
   A2  (-) a formula the importer does not evaluate reads empty, not as its cached 0
   A3  (+) columns keep ML id, kind, label and the yellow "mandatory" fill
   A4  (+) prices split classic header rows from variant rows
   A5  (-) a file that is not a workbook is refused with a readable error
   A6  (-) a prices file without its Publicaciones sheet is refused
   A7  (-) the characteristics file is required: missing, or not found, nothing loads
   A8  (-) the prices file is required: missing, or not found, nothing loads
   A9  (-) the prices file given as the characteristics file is refused
   A10 (+) the workbooks are read in streaming mode: bounded memory
B. Sizes
   B1  (+) spellings of one size become the store's spelling
   B2  (+) the store's decisions: 0 meses=RN, 1A=12 meses, bare 1-16 = years,
           bare ranges = months, 24/36 meses = 2A/3A
   B3  (+) no size at all is "Unique"
   B4  (-) heights and other labels with no readable unit are left as written
   B5  (+) sizes sort: Unique, RN, by age, then cm, then bare numbers
   B6  (+) a variant stores its size in the store's spelling, and its sort key
C. Importing a small report built here
   C1  (+) rows group into products by family id, or by item id
   C2  (+) what every variant agrees on is stored once on the product
   C3  (-) an empty cell stores nothing; a column empty everywhere creates nothing
   C4  (+) lists split, numbers keep their unit, yes/no is a boolean
   C5  (+) an ML id the importer does not know imports with an inferred type
   C6  (+) classic variations inherit price and sale conditions from their header
   C7  (+) size and stock are the variant's columns; none given is Unique and 0
   C8  (+) each variant has one price (column I), in the org's currency
   C9  (-) every other price is ignored: a sale price does not stand in for PRICE
   C10 (+) an axis the sheet leaves out is recovered from ML's variant label
   C11 (+) a size the sheet leaves out is recovered from ML's variant label
   C12 (+) a barcode is stored only when it is a real code
   C13 (-) a barcode prompt or a reason for having none stores nothing at all
   C14 (-) a row with no price is rejected, named in the report, and not loaded
   C15 (-) a row priced 0 is rejected
   C16 (-) a row missing from the prices file is rejected
   C17 (-) a prices row missing from the characteristics file is rejected
   C18 (-) a product with no loadable row is not created
   C19 (+) of two publications of one variant, the active one is loaded
   C20 (-) the inactive twin is ignored, with a warning, wherever it sits in the file
   C21 (+) of two inactive publications of one variant with no update date, the
           later in the file is loaded
   C22 (-) a characteristic is never stored twice: the loaded publication's value remains
   C23 (+) re-importing changes nothing
   C24 (+) correcting the org's currency and re-importing changes only the currency
   C25 (-) re-importing a row that lost its price rejects it and leaves the variant as it was
   C26 (+) a re-import with a new characteristic value replaces it; one value remains
   C27 (+) a dry run reports and saves nothing
   C28 (+) the command loads the report and prints what it rejected
   C29 (-) the command refuses an unknown org, and a missing file or argument
   C30 (+) of two active publications with no update date, the later in the file is loaded
   C31 (+) with the channel's update dates, the publication updated last is loaded
   C32 (-) a later update date does not beat the active publication
   C33 (-) a date on only one of the twins is not compared: the file order decides
   C34 (+) the channel's update date is kept on the listing, in any of its formats
   C35 (+) a category whose rows were all rejected is created and reported as empty
   C36 (+) the command names each category with no products
   C37 (-) a physical store holding Mercado Libre's code stops the import, nothing saved
D. Importing the real report (catalog/tests/mlInfo)
   D1  (+) the base catalog: 8 categories, 89 products, 939 variants, 784 listings
   D2  (-) 33 rows are rejected (15 not in the prices file, 18 with no price)
   D3  (+) no two variants of a product share size and axis values
   D4  (-) 2 duplicate publications are ignored: the later in the file is loaded
   D5  (+) shared characteristics are one attribute across categories
   D6  (-) columns empty in every loaded row have no representation
   D7  (+) a classic item: MLM4549556180, four sizes, each with its stock
   D8  (+) a user-product family: 7441400827737232 is one product
   D9  (+) variants of one product carry different prices
   D10 (+) missing axes are recovered from ML's labels
   D11 (+) the store's size decisions hold on the real data
   D12 (+) filters by size, stock and attribute on the real data
   D13 (+) every price is in the org's currency
   D14 (+) re-importing the real report creates nothing, and counts each listing once
   D15 (-) no barcode is made up
   D16 (+) after a currency fix, re-importing changes only the currency
   D17 (+) "Sudaderas y hoodies" is reported as a category with no products
   D18 (+) "Mi Primer Año Nuevo" is sized in months: 3, 6, 9 and 12 meses
E. Model invariants and deletion (of variants, products and organizations)
   E1  (-) a variant's size and axis values are unique within its product
   E2  (-) one value row per owner and attribute (per option for lists)
   E3  (-) only option lists take several values
   E4  (-) stock cannot be negative
   E5  (-) an org with sales (invoices, orders, recurring invoices) is not deleted, by any path
   E6  (-) a product sold on a deal, order, invoice, estimate or recurring invoice is not deleted
   E7  (-) a variant sold on a deal is not deleted, through either API
   E8  (+) an unsold product is deleted with its variants
   E9  (+) a standalone product sold on an invoice deletes as it always has
   E10 (+) an org without sales is deleted with its whole catalog
   E11 (-) only an active superuser may delete an org together with its sales
   E12 (+) a superuser deletes an org with its sales; the audit log records it
   E13 (+) the Django admin says it cannot be undone, names the sales, asks to confirm
   E14 (-) the Django admin deletes nothing without that confirmation
   E15 (+) confirmed, the Django admin deletes the org with its sales and catalog
   E16 (-) staff who are not superusers cannot delete an org; no bulk delete
   E17 (+) an unsold variant of a product with sales is deleted; the product stays
   E18 (-) a product's refusal names its variants with sales
   E19 (-) an org whose only sales are recurring invoices is not deleted
   E20 (-) an org with deals is not deleted until the user agrees they go too
   E21 (+) once agreed, the org is deleted with its deals; the audit log counts them
   E22 (+) the Django admin warns about the deals and asks for their own tick
   E23 (-) the Django admin deletes nothing until the deals are agreed
   E24 (+) with the deals agreed, the Django admin deletes the org
F. Values and variants
   F1  (+) option lookup ignores case and accents and follows aliases
   F2  (-) bad values are refused with the attribute's code
   F3  (+) good values are stored in their type
   F4  (-) a variant needs its required axes and a combination of its own
   F5  (+) a product comes in only the sizes its variants have
   F6  (+) a variant's name is built from product, size and axes
G. Querying
   G1  (+) a product-wide value counts for its variants, unless overridden
   G2  (+) one code's values are alternatives, different codes must all match
   G3  (-) an unknown code, a malformed filter or too many filters is a 400
   G4  (+) size (any spelling) and stock filters on variants
   G5  (-) on products, every filter must hold for the same variant
   G6  (+) the sizes endpoint lists the sizes in use, youngest first
   G7  (+) the product list pages: count, next and previous links
H. API
   H1  (+/-) any member reads; only an admin writes (every write endpoint)
   H2  (-) another org's rows are 404, and its ids in a payload are 400
   H3  (-) no credentials, no catalog
   H4  (+/-) sales channels
   H5  (+/-) attributes and options
   H6  (+/-) categories and their attributes
   H7  (+/-) products
   H8  (+/-) variants: price above 0 required, size and stock, org currency
   H9  (+/-) listings and the variants they publish
   H10 (+) renaming an option renames and re-keys the variants using it
   H11 (+/-) the catalog is on while Invoices or Deals is, like the products
I. The behaviour the CRM already had
   I1  (+) a standalone product still works as before
   I2  (+) the products list shows which rows are catalog variants
   I3  (-) a variant is not renamed through /api/invoices/products/
   I4  (+) a variant is repriced through /api/invoices/products/
   I5  (+) a deal line item picks a variant and takes its one price
   I6  (+) an invoice line sells a variant at its one price
   I7  (-) an invoice line cannot sell another org's variant
J. Row-level security
   J1  (+) every catalog table is registered for RLS
   J2  (+) every catalog table has its policies (PostgreSQL)
   J3  (-) another org's catalog and import runs are invisible and unwritable (PostgreSQL)
K. Importing from the web: sources, background runs and emails (admins)
   K1  (+) a preview runs in the background, reports everything and saves nothing
   K2  (+) an import loads the catalog and drops its files
   K3  (+) a preview's files are imported without sending them again
   K4  (-) a member can neither see nor start imports
   K5  (-) both files are required
   K6  (-) a file that is not an .xlsx workbook (by name, by content, empty) is refused
   K7  (-) a file over 20 MB is refused
   K8  (-) swapped files fail the run with the reason; nothing is saved
   K9  (-) no credentials, no import
   K10 (-) an import lands in the caller's org only; another org cannot open its run
   K11 (+) a file above the old 5 MB limit (under 20 MB) is imported
   K12 (+) sources: Mercado Libre available; every other channel listed, not available
   K13 (-) a channel with no importer takes no files
   K14 (-) one run at a time, unless the other was lost (two hours)
   K15 (+) the report is emailed when asked
   K16 (+) a run longer than a minute is emailed anyway
   K17 (-) a quick run nobody asked about sends no email
   K18 (-) a failed run's email says why and that nothing was saved
   K19 (-) a preview's files are forgotten after a day; importing it is then refused
   K20 (+) the task carries out a queued run once
   K21 (-) an email that cannot be sent leaves the run done
L. Sales channels: online channels and physical stores
   L1  (+) online channels and physical stores are created; a store keeps its address
   L2  (-) a physical store has no market id
   L3  (-) an online channel has no address or phone
   L4  (-) a channel with listings stays online
   L5  (-) a physical store has no listings
   L6  (-) a channel with listings, invoices or orders is not deleted
   L7  (+) a store with no sales is deleted
   L8  (+) an invoice records its channel; its account stays the customer; list filter
   L9  (-) an invoice cannot record another org's channel
   L10 (-) a switched-off channel takes no new sales
   L11 (+) an invoice keeps a channel that was switched off later
   L12 (+) every channel sells the whole catalog at the variant's one price
   L13 (+) an order records its channel, which then cannot be deleted
   L14 (+) a duplicated invoice keeps its channel
   L15 (-) a duplicate takes no switched-off channel; the original keeps it
   L16 (-) with Invoices switched off, a channel its invoices record is kept (409)

The web interface has its own cases: W (form validation and data shaping,
frontend/src/lib/server/v2/catalog.test.ts), R (every catalog page's load and
actions, frontend/src/routes/(app)/invoices/catalog/catalog-routes.test.ts)
and V (a browser driving the running app, frontend/e2e/catalog.e2e.ts).
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import openpyxl
import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import Client
from django.utils import timezone
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter

from accounts.models import Account
from catalog.importers.mercadolibre import (
    import_mercadolibre,
    read_characteristics,
    read_prices,
)
from catalog.models import (
    Attribute,
    AttributeOption,
    AttributeValue,
    CatalogProduct,
    Category,
    CategoryAttribute,
    ChannelAttribute,
    ImportFile,
    ImportRun,
    Listing,
    ListingVariant,
    SalesChannel,
)
from catalog.queries import filter_variants, narrow_variants
from catalog.services import (
    CatalogError,
    coerce_values,
    find_option,
    save_variant,
    set_values,
)
from catalog.tasks import run_catalog_import
from catalog.text import UNIQUE_SIZE, canonical_size, size_sort_order
from common.audit_log import SecurityAuditLog
from common.models import Org, User
from common.org_deletion import OrgHasDeals, OrgHasSales, delete_org
from common.rls import ORG_SCOPED_TABLES
from common.spreadsheet import SpreadsheetError
from common.testing import (
    org_tables_not_covered,
    rls_org,
    rls_policy_gaps,
    set_modules_off,
)
from contacts.models import Contact
from invoices.models import (
    Estimate,
    EstimateLineItem,
    Invoice,
    InvoiceLineItem,
    Product,
    RecurringInvoice,
    RecurringInvoiceLineItem,
)
from opportunity.models import Opportunity
from orders.models import Order, OrderLineItem

ML_DIR = Path(__file__).parent / "mlInfo"
CHARACTERISTICS = ML_DIR / "characteristics.xlsx"
PRICES = ML_DIR / "prices.xlsx"

CATALOG_TABLES = [
    "catalog_sales_channel",
    "catalog_category",
    "catalog_attribute",
    "catalog_attribute_option",
    "catalog_category_attribute",
    "catalog_channel_attribute",
    "catalog_product",
    "catalog_attribute_value",
    "catalog_listing",
    "catalog_listing_variant",
    "catalog_import_run",
    "catalog_import_file",
]

YELLOW = "FFFFF158"
BLUE = "FF6FA8DC"


# =============================================================================
# A small Mercado Libre report, built in the shape ML exports
# =============================================================================

DRESS_COLUMNS = [
    ("FAMILY_ID", "FIXED", "Agrupador de variantes"),
    ("ID", "FIXED", "Número de publicación"),
    ("PRODUCT_NUMBER", "FIXED", "Número de producto"),
    ("SKU", "FIXED", "SKU"),
    ("VARIATION_ID", "FIXED", "Variación ID"),
    ("DOMAIN_ID", "FIXED", "Dominio"),
    ("CATEGORY_ID", "FIXED", "Categoría"),
    ("TITLE", "FIXED", "Título"),
    ("SIZE_VARIATION-COLUMN", "VARIATION", "Talla"),
    ("COLOR_VARIATION-COLUMN", "VARIATION", "Color"),
    ("MAIN_COLOR", "ATTRIBUTE", "Color principal"),
    ("GTIN", "ATTRIBUTE", "Código universal de producto"),
    ("BRAND", "ATTRIBUTE", "Marca\n\n(Si tienes variantes ...)"),
    ("MAIN_MATERIAL", "ATTRIBUTE", "Material principal"),
    ("OCCASIONS", "ATTRIBUTE", "Ocasión"),
    ("IS_SPORTIVE", "ATTRIBUTE", "Es deportiva"),
    ("WIDTH", "ATTRIBUTE", "Ancho"),
    ("WIDTH_UNIT", "ATTRIBUTE_UNIT", "Unidad de Ancho"),
    ("SAT_KEY", "ATTRIBUTE", "Clave del producto"),
    ("NEW_ML_THING", "ATTRIBUTE", "Atributo nuevo"),
]

# Aprons: no size column at all, like the real "Mandiles de cocina" sheet.
APRON_COLUMNS = [
    ("FAMILY_ID", "FIXED", "Agrupador de variantes"),
    ("ID", "FIXED", "Número de publicación"),
    ("PRODUCT_NUMBER", "FIXED", "Número de producto"),
    ("VARIATION_ID", "FIXED", "Variación ID"),
    ("DOMAIN_ID", "FIXED", "Dominio"),
    ("TITLE", "FIXED", "Título"),
    ("COLOR_VARIATION-COLUMN", "VARIATION", "Color"),
    ("GTIN", "ATTRIBUTE", "Código universal de producto"),
    ("MATERIALS", "ATTRIBUTE", "Materiales"),
]

HOODIE_COLUMNS = [
    ("ID", "FIXED", "Número de publicación"),
    ("PRODUCT_NUMBER", "FIXED", "Número de producto"),
    ("DOMAIN_ID", "FIXED", "Dominio"),
    ("TITLE", "FIXED", "Título"),
    ("SIZE_VARIATION-COLUMN", "VARIATION", "Talla"),
    ("WITH_HOOD", "ATTRIBUTE", "Con capucha"),
]

PRICE_COLUMNS = [
    "FAMILY_ID",
    "ITEM_ID",
    "PRODUCT_NUMBER",
    "VARIATION_ID",
    "SKU",
    "TITLE",
    "VARIATIONS",
    "QUANTITY",
    "PRICE",
    "SALE_PRICE",
    "CURRENCY_ID",
    "TIERED_PRICING_QUANTITY_1",
    "TIERED_PRICING_PRICE_1",
    "TIERED_PRICING_QUANTITY_2",
    "TIERED_PRICING_PRICE_2",
    "CONDITION",
    "DESCRIPTION",
    "SHIPPING_METHOD",
    "SHIPPING_COST_MARKETPLACE",
    "LISTING_TYPE",
    "FEE_PER_SALE",
    "STATUS",
    "WARRANTY_TYPE",
    "WARRANTY_TIME",
    "WARRANTY_TIME_UNIT",
    "MANUFACTURING_TIME",
    "CATEGORY",
    "SHIPPING_HEIGHT",
    "SHIPPING_WIDTH",
    "SHIPPING_DEPTH",
    "SHIPPING_WEIGHT",
]


def _ref(columns, code, row):
    index = [c[0] for c in columns].index(code) + 1
    return f"={get_column_letter(index)}{row}"


def _price_ref(code, row, suffix=""):
    letter = get_column_letter(PRICE_COLUMNS.index(code) + 1)
    return f'={letter}{row}&"{suffix}"' if suffix else f"={letter}{row}"


def _family_row(number, item, size, main_color, **extra):
    return {
        "FAMILY_ID": "FAM1",
        "ID": item,
        "PRODUCT_NUMBER": number,
        "DOMAIN_ID": "MLM-DRESSES",
        "CATEGORY_ID": "MLM31457",
        "TITLE": "Conjunto Plim",
        "SIZE_VARIATION-COLUMN": size,
        "COLOR_VARIATION-COLUMN": "Azul",
        "MAIN_COLOR": main_color,
        "BRAND": "Camille",
        "MAIN_MATERIAL": "Algodón",
        "OCCASIONS": "Diario",
        "IS_SPORTIVE": "No",
        **extra,
    }


def dress_rows():
    """Rows 5-6: classic item MLM100, two variations, the second repeating the
    first by formula as ML does. Rows 7-15: family FAM1:

    * U201 (active) and U202 (inactive) differ only by design, named only in
      ML's label ("Plim Plim", "Bam");
    * U203 has only a sale price, U204 is priced 0, U205 is not in the prices
      file: all three are rejected;
    * U206 (active) republishes U202's variant at another price: it wins;
    * U207 (inactive) republishes U201's variant: U201 (active) wins;
    * U209 and U208, both inactive and with no update date, are one variant:
      U208 comes later in the file and wins, although MLM209 is the higher
      publication number.

    Row 16: MLM400, a product whose only row has no price."""
    r = _ref
    return [
        {
            "ID": "MLM100",
            "PRODUCT_NUMBER": "900001",
            "VARIATION_ID": "900001",
            "DOMAIN_ID": "MLM-DRESSES",
            "CATEGORY_ID": "MLM10280",
            "TITLE": "Vestido Fiesta",
            "SIZE_VARIATION-COLUMN": "0-3 M",
            "COLOR_VARIATION-COLUMN": "Rojo",
            "MAIN_COLOR": "Rojo",
            "GTIN": "Otra razón",
            "BRAND": "Camille",
            "MAIN_MATERIAL": "Algodón",
            "OCCASIONS": "Fiesta,Bautismo",
            "IS_SPORTIVE": "No",
            "WIDTH": 35,
            "WIDTH_UNIT": "cm",
            "NEW_ML_THING": "Sí",
        },
        {
            "ID": "MLM100",
            "PRODUCT_NUMBER": "900002",
            "VARIATION_ID": "900002",
            "DOMAIN_ID": "MLM-DRESSES",
            "CATEGORY_ID": "MLM10280",
            "TITLE": "Vestido Fiesta",
            "SIZE_VARIATION-COLUMN": "2 Años",
            "COLOR_VARIATION-COLUMN": "Rojo",
            "MAIN_COLOR": r(DRESS_COLUMNS, "MAIN_COLOR", 5),
            "GTIN": "Escribe el código",
            "BRAND": r(DRESS_COLUMNS, "BRAND", 5),
            "MAIN_MATERIAL": r(DRESS_COLUMNS, "MAIN_MATERIAL", 5),
            "OCCASIONS": r(DRESS_COLUMNS, "OCCASIONS", 5),
            "IS_SPORTIVE": r(DRESS_COLUMNS, "IS_SPORTIVE", 5),
            "WIDTH": 35.0,
            "WIDTH_UNIT": r(DRESS_COLUMNS, "WIDTH_UNIT", 5),
            "SAT_KEY": r(DRESS_COLUMNS, "SAT_KEY", 5),
            "NEW_ML_THING": r(DRESS_COLUMNS, "NEW_ML_THING", 5),
        },
        _family_row("U201", "MLM201", "3-6 M", "Azul", GTIN="7501234567890"),
        _family_row("U202", "MLM202", "3-6 M", "Multicolor", GTIN="Otra razón"),
        _family_row("U203", "MLM203", "6-9", "Azul"),
        _family_row("U204", "MLM204", "2", "Azul"),
        _family_row("U205", "MLM205", "RN", "Azul"),
        _family_row("U206", "MLM206", "3-6 M", "Verde"),
        _family_row("U207", "MLM207", "3-6 M", "Rosa"),
        _family_row("U209", "MLM209", "12-18 M", "Azul"),
        _family_row("U208", "MLM208", "12-18 M", "Rosa"),
        {
            "ID": "MLM400",
            "PRODUCT_NUMBER": "U400",
            "DOMAIN_ID": "MLM-DRESSES",
            "TITLE": "Vestido Sin Precio",
            "SIZE_VARIATION-COLUMN": "RN",
            "COLOR_VARIATION-COLUMN": "Rosa",
        },
    ]


def apron_rows():
    """MLM300 is a classic apron whose sizes appear only in ML's labels;
    U301 names no size anywhere."""
    base = {"DOMAIN_ID": "MLM-KITCHEN_APRONS", "GTIN": "Escribe el código"}
    return [
        {
            **base,
            "ID": "MLM300",
            "PRODUCT_NUMBER": "900301",
            "VARIATION_ID": "900301",
            "TITLE": "Mandil Jengibre",
            "COLOR_VARIATION-COLUMN": "Beige",
            "MATERIALS": "Tela",
        },
        {
            **base,
            "ID": "MLM300",
            "PRODUCT_NUMBER": "900302",
            "VARIATION_ID": "900302",
            "TITLE": "Mandil Jengibre",
            "COLOR_VARIATION-COLUMN": "Beige",
            "MATERIALS": "Tela",
        },
        {
            **base,
            "ID": "MLM301",
            "PRODUCT_NUMBER": "U301",
            "TITLE": "Mandil Navideño",
            "COLOR_VARIATION-COLUMN": "Rojo",
            "MATERIALS": "Tela",
        },
    ]


def hoodie_rows():
    """A sheet none of whose rows is in the prices file."""
    return [
        {
            "ID": "MLM500",
            "PRODUCT_NUMBER": "U500",
            "DOMAIN_ID": "MLM-SWEATSHIRTS_AND_HOODIES",
            "TITLE": "Sudadera",
            "SIZE_VARIATION-COLUMN": "3",
            "WITH_HOOD": "Sí",
        }
    ]


def _conditions(kind="Clásica", status="Inactiva", category="Vestidos"):
    return {
        "CURRENCY_ID": "$",
        "CONDITION": "Nuevo",
        "DESCRIPTION": "Conjunto de verano.",
        "SHIPPING_METHOD": "Envíos en Mercado Libre",
        "SHIPPING_COST_MARKETPLACE": "Ofreces envío gratis",
        "LISTING_TYPE": kind,
        "STATUS": status,
        "WARRANTY_TYPE": "Sin garantía",
        "WARRANTY_TIME": 0,
        "MANUFACTURING_TIME": "No especificado",
        "CATEGORY": category,
        "SHIPPING_HEIGHT": 27,
        "SHIPPING_WIDTH": 19,
        "SHIPPING_DEPTH": 8,
        "SHIPPING_WEIGHT": 0.5,
    }


def _inherit(header_row, category):
    """A classic variation row: everything but its own ids, label and stock
    points back at its header row."""
    return {
        "TITLE": f'="     "&F{header_row}',
        "PRICE": _price_ref("PRICE", header_row),
        "SALE_PRICE": _price_ref("SALE_PRICE", header_row),
        "CURRENCY_ID": _price_ref("CURRENCY_ID", header_row, "     "),
        "CONDITION": _price_ref("CONDITION", header_row, "     "),
        "LISTING_TYPE": _price_ref("LISTING_TYPE", header_row, "     "),
        "STATUS": _price_ref("STATUS", header_row, "     "),
        "CATEGORY": category,
        "SHIPPING_HEIGHT": _price_ref("SHIPPING_HEIGHT", header_row),
        "SHIPPING_WIDTH": _price_ref("SHIPPING_WIDTH", header_row),
        "SHIPPING_DEPTH": _price_ref("SHIPPING_DEPTH", header_row),
        "SHIPPING_WEIGHT": _price_ref("SHIPPING_WEIGHT", header_row),
    }


def _family_price(number, item, label, price, quantity="", status="Inactiva", **extra):
    return {
        "FAMILY_ID": "FAM1",
        "ITEM_ID": item,
        "PRODUCT_NUMBER": number,
        "TITLE": "Conjunto Plim",
        "VARIATIONS": label,
        "QUANTITY": quantity,
        "PRICE": price,
        "SALE_PRICE": "-",
        **_conditions(status=status),
        **extra,
    }


def price_rows():
    """Row 7 is MLM100's header and rows 8-9 its variations; row 10 is
    MLM300's header and rows 11-12 its variations. U205 and U500 are absent;
    U999 has a price but no characteristics row."""
    return [
        {
            "ITEM_ID": "MLM100",
            "TITLE": "Vestido Fiesta",
            "VARIATIONS": "-",
            "QUANTITY": "33",
            "PRICE": 619,
            "SALE_PRICE": "-",
            "FEE_PER_SALE": '=IF(INDIRECT("T7")="Clásica","15%","19.5%")',
            **_conditions("Premium", "Activa"),
            "DESCRIPTION": "Vestido de fiesta.",
            "MANUFACTURING_TIME": "3",
            "SHIPPING_HEIGHT": 29,
            "SHIPPING_WIDTH": 28,
            "SHIPPING_DEPTH": 7,
            "SHIPPING_WEIGHT": 1.212,
        },
        {
            "ITEM_ID": "MLM100",
            "PRODUCT_NUMBER": "900001",
            "VARIATION_ID": "900001",
            "VARIATIONS": "0-3 M / Rojo",
            "QUANTITY": 15,
            **_inherit(7, "Vestidos"),
        },
        {
            "ITEM_ID": "MLM100",
            "PRODUCT_NUMBER": "900002",
            "VARIATION_ID": "900002",
            "VARIATIONS": "2 Años / Rojo",
            "QUANTITY": 18,
            **_inherit(7, "Vestidos"),
        },
        {
            "ITEM_ID": "MLM300",
            "TITLE": "Mandil Jengibre",
            "VARIATIONS": "-",
            "PRICE": 449,
            **_conditions("Premium", "Activa", "Mandiles de cocina"),
        },
        {
            "ITEM_ID": "MLM300",
            "PRODUCT_NUMBER": "900301",
            "VARIATION_ID": "900301",
            "VARIATIONS": "4-6 años / Beige",
            "QUANTITY": 4,
            **_inherit(10, "Mandiles de cocina"),
        },
        {
            "ITEM_ID": "MLM300",
            "PRODUCT_NUMBER": "900302",
            "VARIATION_ID": "900302",
            "VARIATIONS": "6-8 años / Beige",
            "QUANTITY": 6,
            **_inherit(10, "Mandiles de cocina"),
        },
        _family_price(
            "U201",
            "MLM201",
            "Azul 3-6 M Plim Plim",
            529,
            20,
            "Activa",
            SALE_PRICE=499,
            TIERED_PRICING_QUANTITY_1=5,
            TIERED_PRICING_PRICE_1=449,
            TIERED_PRICING_QUANTITY_2=10,
            TIERED_PRICING_PRICE_2=399,
        ),
        _family_price("U202", "MLM202", "Azul 3-6 M Bam", 549),
        _family_price("U203", "MLM203", "Azul 6-9 Plim Plim", "", SALE_PRICE=449),
        _family_price("U204", "MLM204", "Azul 2 Plim Plim", 0),
        _family_price("U206", "MLM206", "Azul 3-6 M Bam", 579, 4, "Activa"),
        _family_price("U207", "MLM207", "Azul 3-6 M Plim Plim", 599, 20),
        _family_price("U209", "MLM209", "Azul 12-18 M Bam", 650, 2),
        _family_price("U208", "MLM208", "Azul 12-18 M Bam", 640, 9),
        {"ITEM_ID": "MLM400", "PRODUCT_NUMBER": "U400", "PRICE": "", **_conditions()},
        _family_price("U999", "MLM999", "Azul 4A Bam", 700),
        {
            "ITEM_ID": "MLM301",
            "PRODUCT_NUMBER": "U301",
            "TITLE": "Mandil Navideño",
            "VARIATIONS": "Rojo",
            "QUANTITY": 9,
            "PRICE": 399,
            **_conditions("Premium", "Activa", "Mandiles de cocina"),
        },
    ]


def write_characteristics(path, sheets):
    """`sheets` is `[(title, columns, rows), ...]`, after a help sheet."""
    wb = openpyxl.Workbook()
    wb.active.title = "Ayuda"
    for title, columns, rows in sheets:
        ws = wb.create_sheet(title)
        for col, (code, kind, label) in enumerate(columns, 1):
            ws.cell(1, col, code)
            ws.cell(2, col, kind)
            cell = ws.cell(4, col, label)
            color = YELLOW if kind in ("FIXED", "VARIATION") else BLUE
            cell.fill = PatternFill("solid", fgColor=color)
        codes = [c[0] for c in columns]
        for r, row in enumerate(rows, 5):
            for code, value in row.items():
                ws.cell(r, codes.index(code) + 1, value)
    wb.save(path)
    return path


def write_prices(path, rows, *, sheet="Publicaciones"):
    """A column a row names beyond ML's usual ones (`LAST_UPDATED`) is added
    after them, so the formulas' column letters still hold."""
    columns = PRICE_COLUMNS + sorted(
        {code for row in rows for code in row} - set(PRICE_COLUMNS)
    )
    wb = openpyxl.Workbook()
    wb.active.title = "Ayuda"
    ws = wb.create_sheet(sheet)
    for col, code in enumerate(columns, 1):
        ws.cell(1, col, code)
    for r, row in enumerate(rows, 7):
        for code, value in row.items():
            ws.cell(r, columns.index(code) + 1, value)
    wb.save(path)
    return path


def mini_sheets(dresses=None):
    return [
        ("Vestidos", DRESS_COLUMNS, dresses or dress_rows()),
        ("Mandiles de cocina", APRON_COLUMNS, apron_rows()),
        ("Sudaderas y hoodies", HOODIE_COLUMNS, hoodie_rows()),
    ]


@pytest.fixture
def mini_report(tmp_path):
    return SimpleNamespace(
        characteristics=write_characteristics(tmp_path / "c.xlsx", mini_sheets()),
        prices=write_prices(tmp_path / "p.xlsx", price_rows()),
        folder=tmp_path,
    )


@pytest.fixture
def mini(org_a, mini_report):
    org_a.default_currency = "MXN"
    org_a.save(update_fields=["default_currency"])
    report = import_mercadolibre(org_a, mini_report.characteristics, mini_report.prices)
    return SimpleNamespace(org=org_a, report=report, files=mini_report)


def _published(product_number):
    return ListingVariant.objects.select_related("listing", "variant").get(
        external_product_id=product_number
    )


def _values(product, variant=None):
    """`{code: [display, ...]}` of what is stored on a product (or variant)."""
    found = {}
    rows = AttributeValue.objects.filter(product=product, variant=variant)
    for row in rows.select_related("attribute", "option"):
        found.setdefault(row.attribute.code, []).append(row.display())
    return {code: sorted(values) for code, values in found.items()}


def _rejected(report, product_number):
    return [r for r in report.rejected if f"product {product_number} " in r]


def _ignored(report, product_number):
    return [w for w in report.warnings if f"product {product_number} is" in w]


# =============================================================================
# A. Reading the Mercado Libre workbooks
# =============================================================================


def test_a1_repeating_formulas_resolve(mini_report):
    """A1 (+). `=M5` takes the cell it points at, and `=K7&"     "` is the
    concatenation, as ML uses them to repeat a value down the sheet."""
    second = read_characteristics(mini_report.characteristics)[0].rows[1]
    assert second["BRAND"] == "Camille"
    assert second["OCCASIONS"] == "Fiesta,Bautismo"
    assert second["SAT_KEY"] == ""
    variation = read_prices(mini_report.prices)[0]["900001"]
    assert variation["PRICE"] == 619
    assert variation["CURRENCY_ID"].strip() == "$"
    assert variation["TITLE"].strip() == "Vestido Fiesta"


def test_a2_an_unevaluated_formula_reads_empty(mini_report):
    """A2 (-). ML's fee `IF(INDIRECT(...))` is not evaluated: it reads as an
    empty cell, never as the 0 the file caches for it."""
    headers = read_prices(mini_report.prices)[1]
    assert headers["MLM100"]["FEE_PER_SALE"] is None


def test_a3_columns_keep_id_kind_label_and_mandatory_fill(mini_report):
    """A3 (+). Row 1 is ML's id, row 2 the kind, row 4 the label (first line)
    and a yellow label is mandatory. The help sheet is skipped."""
    sheets = read_characteristics(mini_report.characteristics)
    assert [s.name for s in sheets] == [
        "Vestidos",
        "Mandiles de cocina",
        "Sudaderas y hoodies",
    ]
    columns = {c.code: c for c in sheets[0].columns}
    assert columns["SIZE_VARIATION-COLUMN"].kind == "VARIATION"
    assert columns["SIZE_VARIATION-COLUMN"].mandatory is True
    assert columns["BRAND"].mandatory is False
    assert columns["BRAND"].label == "Marca"
    assert columns["WIDTH_UNIT"].kind == "ATTRIBUTE_UNIT"


def test_a4_prices_split_header_rows_from_variant_rows(mini_report):
    """A4 (+). A row with no product number is a classic item's header; every
    other row is one variant, keyed by its product number."""
    by_product, headers = read_prices(mini_report.prices)
    assert set(headers) == {"MLM100", "MLM300"}
    assert "900001" in by_product and "U201" in by_product


def test_a5_a_file_that_is_not_a_workbook_is_refused(org_a, mini_report):
    """A5 (-). A text file named .xlsx is refused with a message, not a crash,
    and nothing is loaded."""
    fake = mini_report.folder / "fake.xlsx"
    fake.write_text("FAMILY_ID,ID\n1,2\n")
    with pytest.raises(SpreadsheetError):
        import_mercadolibre(org_a, fake, mini_report.prices)
    assert not CatalogProduct.objects.filter(org=org_a).exists()


def test_a6_a_prices_file_without_its_sheet_is_refused(org_a, mini_report):
    """A6 (-). A prices workbook without the Publicaciones sheet is refused."""
    wrong = write_prices(mini_report.folder / "wrong.xlsx", price_rows(), sheet="Hoja1")
    with pytest.raises(SpreadsheetError, match="Publicaciones"):
        import_mercadolibre(org_a, mini_report.characteristics, wrong)
    assert not Category.objects.filter(org=org_a).exists()


@pytest.mark.parametrize("missing", [None, "", "no-such-file.xlsx"])
def test_a7_the_characteristics_file_is_required(org_a, mini_report, missing):
    """A7 (-). Without the characteristics file there are no characteristics,
    so nothing is loaded: not given, or not found, is refused."""
    path = mini_report.folder / missing if missing else missing
    with pytest.raises(SpreadsheetError, match="characteristics file"):
        import_mercadolibre(org_a, path, mini_report.prices)
    assert not SalesChannel.objects.filter(org=org_a).exists()


@pytest.mark.parametrize("missing", [None, "", "no-such-file.xlsx"])
def test_a8_the_prices_file_is_required(org_a, mini_report, missing):
    """A8 (-). Without the prices file there are no prices or sale conditions,
    so nothing is loaded: not given, or not found, is refused."""
    path = mini_report.folder / missing if missing else missing
    with pytest.raises(SpreadsheetError, match="prices file"):
        import_mercadolibre(org_a, mini_report.characteristics, path)
    assert not SalesChannel.objects.filter(org=org_a).exists()


def test_a9_the_prices_file_as_characteristics_is_refused(org_a, mini_report):
    """A9 (-). The two files swapped: the prices workbook has no category
    sheet, so it is refused as a characteristics file."""
    with pytest.raises(SpreadsheetError, match="no category sheets"):
        import_mercadolibre(org_a, mini_report.prices, mini_report.prices)
    assert not Category.objects.filter(org=org_a).exists()


def test_a10_the_workbooks_are_read_in_streaming_mode():
    """A10 (+). Both real workbooks are read keeping only their values, so a
    report near the 20 MB limit fits a worker: reading the sample report
    peaks under 64 MB of Python memory (it measures about 8 MB)."""
    import tracemalloc

    tracemalloc.start()
    try:
        sheets = read_characteristics(CHARACTERISTICS)
        prices, _ = read_prices(PRICES)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert (len(sheets), len(prices)) == (8, 959)
    assert peak < 64 * 1024 * 1024, peak


# =============================================================================
# B. Sizes
# =============================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0-3 M", "0-3 meses"),
        ("00-03 Meses", "0-3 meses"),
        ("0 Meses - 3 Meses", "0-3 meses"),
        ("6 meses - 12 meses", "6-12 meses"),
        ("6-12 meses", "6-12 meses"),
        ("12-18 M", "12-18 meses"),
        ("12M", "12 meses"),
        ("03 Meses", "3 meses"),
        ("2 A", "2A"),
        ("2 Años", "2A"),
        ("4-6 años", "4-6 años"),
        ("25 cm", "25 cm"),
        ("rn", "RN"),
        ("Recién nacido", "RN"),
    ],
)
def test_b1_size_spellings_become_the_store_spelling(raw, expected):
    """B1 (+). Only the spelling is rewritten."""
    assert canonical_size(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0 meses", "RN"),
        ("0 Meses", "RN"),
        ("1A", "12 meses"),
        ("1 A", "12 meses"),
        ("1 año", "12 meses"),
        ("12 meses", "12 meses"),
        ("1", "12 meses"),
        ("2", "2A"),
        ("3", "3A"),
        ("10", "10A"),
        ("12", "12A"),
        ("3-6", "3-6 meses"),
        ("6-9", "6-9 meses"),
        ("9-12", "9-12 meses"),
        ("24 meses", "2A"),
        ("36 Meses", "3A"),
        ("48 meses", "4A"),
        ("12-24 meses", "12-24 meses"),
    ],
)
def test_b2_the_stores_size_decisions(raw, expected):
    """B2 (+). 0 meses is RN; 1A is written 12 meses; a bare number from 1 to
    16 is years (so a bare 1 is 1A, which is 12 meses); a bare range is
    months; 24 and 36 meses are 2A and 3A. A range in months stays one."""
    assert canonical_size(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "Única", "talla única", "unique"])
def test_b3_no_size_is_unique(raw):
    """B3 (+). A variant with no size is "Unique"."""
    assert canonical_size(raw) == UNIQUE_SIZE


@pytest.mark.parametrize(
    "raw", ["100", "110", "120", "17", "100 1.5 año - 2 años", "XL"]
)
def test_b4_heights_and_other_labels_are_left_as_written(raw):
    """B4 (-). Heights (100, 110, 120), numbers above 16 and labels with no
    readable unit are kept exactly as the report wrote them."""
    assert canonical_size(raw) == raw


def test_b5_sizes_sort_by_age_then_length_then_bare_numbers():
    """B5 (+). Unique, RN, then by age (a point size after the range ending on
    it), then lengths in cm, then bare numbers by value, then anything else."""
    labels = [
        "Unique",
        "RN",
        "0-3 meses",
        "3 meses",
        "3-6 meses",
        "6-9 meses",
        "6-12 meses",
        "12 meses",
        "12-18 meses",
        "2A",
        "3A",
        "4A",
        "5A",
        "12A",
        "25 cm",
        "100",
        "120",
        "Otra",
    ]
    assert sorted(reversed(labels), key=size_sort_order) == labels


def test_b6_a_variant_stores_its_size_spelled_and_sortable(org_a):
    """B6 (+). Saving a product stores the store's spelling and the sort key,
    also when only the size is saved (as a mass update would)."""
    product = Product.objects.create(org=org_a, name="Body", price=1, size="0-3 M")
    assert (product.size, product.size_sort) == (
        "0-3 meses",
        size_sort_order("0-3 meses"),
    )
    product.size = "24 meses"
    product.save(update_fields=["size"])
    product.refresh_from_db()
    assert (product.size, product.size_sort) == ("2A", size_sort_order("2A"))
    plain = Product.objects.create(org=org_a, name="Plain", price=1)
    assert (plain.size, plain.stock) == ("Unique", 0)


# =============================================================================
# C. Importing the small report
# =============================================================================


def test_c1_rows_group_by_family_or_by_item(mini):
    """C1 (+). MLM100 (two variations) is one product. The loaded publications
    of family FAM1 (MLM201, MLM206, MLM208) are one product, with a listing
    each."""
    classic = Listing.objects.get(external_id="MLM100").product
    family = Listing.objects.get(external_id="MLM201").product
    assert Listing.objects.get(external_id="MLM206").product == family
    assert classic.variants.count() == 2
    assert sorted(family.listings.values_list("external_id", flat=True)) == [
        "MLM201",
        "MLM206",
        "MLM208",
    ]
    assert classic.category.name == "Vestidos"
    assert sorted(
        CatalogProduct.objects.filter(org=mini.org).values_list("name", flat=True)
    ) == [
        "Conjunto Plim",
        "Mandil Jengibre",
        "Mandil Navideño",
        "Vestido Fiesta",
    ]


def test_c2_agreed_values_go_on_the_product_the_rest_on_variants(mini):
    """C2 (+). Everything MLM100's two rows agree on is stored once, on the
    product. FAM1's loaded rows disagree on the main color, so each variant
    keeps its own and the product has none."""
    classic = Listing.objects.get(external_id="MLM100").product
    assert _values(classic) == {
        "brand": ["Camille"],
        "is_sportive": ["No"],
        "main_color": ["Rojo"],
        "material": ["Algodón"],
        "new_ml_thing": ["Sí"],
        "occasions": ["Bautismo", "Fiesta"],
        "width": ["35 cm"],
    }
    family = Listing.objects.get(external_id="MLM201").product
    assert "main_color" not in _values(family)
    assert _values(family, _published("U201").variant)["main_color"] == ["Azul"]


def test_c3_empty_cells_and_empty_columns_store_nothing(mini):
    """C3 (-). SAT_KEY is empty in every row: no attribute, link or mapping
    exists for it. FAM1 has no width: no width row for it."""
    assert not Attribute.objects.filter(org=mini.org, code="sat_key").exists()
    assert not ChannelAttribute.objects.filter(external_code="SAT_KEY").exists()
    family = Listing.objects.get(external_id="MLM201").product
    assert not AttributeValue.objects.filter(
        product=family, attribute__code="width"
    ).exists()


def test_c4_lists_split_numbers_keep_units_and_yes_no_is_boolean(mini):
    """C4 (+). "Fiesta,Bautismo" is two option rows; 35 and 35.0 are the
    same 35 cm; "No" is stored as False. MATERIALS feeds the same "material"
    attribute as MAIN_MATERIAL."""
    classic = Listing.objects.get(external_id="MLM100").product
    assert (
        AttributeValue.objects.filter(
            product=classic, attribute__code="occasions"
        ).count()
        == 2
    )
    width = AttributeValue.objects.get(product=classic, attribute__code="width")
    assert (width.value_number, width.unit) == (Decimal("35"), "cm")
    sportive = AttributeValue.objects.get(
        product=classic, attribute__code="is_sportive"
    )
    assert sportive.value_boolean is False
    apron = Listing.objects.get(external_id="MLM300").product
    assert _values(apron)["material"] == ["Tela"]
    codes = ChannelAttribute.objects.filter(attribute__code="material")
    assert sorted(codes.values_list("external_code", flat=True)) == [
        "MAIN_MATERIAL",
        "MATERIALS",
    ]


def test_c5_an_unknown_ml_id_imports_with_an_inferred_type(mini):
    """C5 (+). NEW_ML_THING is not in the importer's table: it arrives as
    `new_ml_thing`, named by its label, typed from its values (Sí/No)."""
    attribute = Attribute.objects.get(org=mini.org, code="new_ml_thing")
    assert (attribute.name, attribute.data_type) == (
        "Atributo nuevo",
        Attribute.BOOLEAN,
    )


def test_c6_classic_variations_inherit_from_their_header(mini):
    """C6 (+). MLM100's variation rows only point at the header row; they
    still get its price, and the listing gets its sale conditions and the
    product its description."""
    listing = Listing.objects.get(external_id="MLM100")
    assert (listing.status, listing.condition, listing.listing_type) == (
        Listing.ACTIVE,
        Listing.NEW,
        "Premium",
    )
    assert listing.free_shipping is True
    assert listing.handling_days == 3
    assert listing.product.description == "Vestido de fiesta."
    for number in ("900001", "900002"):
        variant = _published(number).variant
        assert variant.price == Decimal("619.00")
        assert variant.package_weight_kg == Decimal("1.212")


def test_c7_size_and_stock_are_columns_with_defaults(mini):
    """C7 (+). Size and stock are the variant's own columns: "0-3 M" is
    0-3 meses and "2 Años" 2A, QUANTITY is the stock, an empty QUANTITY is 0
    and an apron that names no size is "Unique"."""
    cases = {
        "900001": ("0-3 meses", 15),
        "900002": ("2A", 18),
        "U201": ("3-6 meses", 20),
        "900301": ("4-6 años", 4),
        "900302": ("6-8 años", 6),
        "U301": (UNIQUE_SIZE, 9),
        "U206": ("3-6 meses", 4),
    }
    for number, expected in cases.items():
        variant = _published(number).variant
        assert (variant.size, variant.stock) == expected, number
    # QUANTITY left empty: the stock is 0.
    rows = price_rows()
    for row in rows:
        if row.get("PRODUCT_NUMBER") == "U201":
            row["QUANTITY"] = ""
    import_mercadolibre(
        mini.org,
        mini.files.characteristics,
        write_prices(mini.files.folder / "q.xlsx", rows),
    )
    assert _published("U201").variant.stock == 0
    assert not Attribute.objects.filter(org=mini.org, code="size").exists()


def test_c8_each_variant_has_one_price_in_the_orgs_currency(mini):
    """C8 (+). The price is column I (PRICE): U201 529, U206 579. The
    report's "$" is not a currency: every price is in the org's (MXN)."""
    assert _published("U201").variant.price == Decimal("529.00")
    assert _published("U206").variant.price == Decimal("579.00")
    variants = Product.objects.filter(org=mini.org, catalog_product__isnull=False)
    assert set(variants.values_list("currency", flat=True)) == {"MXN"}
    assert not variants.filter(price__lte=0).exists()
    fields = {f.name for f in ListingVariant._meta.get_fields()}
    assert not fields & {"price", "sale_price", "currency", "available_quantity"}


def test_c9_every_other_price_is_ignored(mini):
    """C9 (-). U201's sale price (499) and volume prices (5 and 10 units) are
    not loaded anywhere; U203 has a sale price but no PRICE, and is rejected
    rather than priced at its sale price."""
    assert _published("U201").variant.price == Decimal("529.00")
    assert not ListingVariant.objects.filter(external_product_id="U203").exists()
    assert "it has no price" in _rejected(mini.report, "U203")[0]


def test_c10_an_axis_the_sheet_leaves_out_is_recovered(mini):
    """C10 (+). U201 and U206 share size and color; ML's labels "Azul 3-6 M
    Plim Plim" and "Azul 3-6 M Bam" name the design, which becomes an axis,
    so they stay two variants."""
    first, second = _published("U201").variant, _published("U206").variant
    assert first.variant_key != second.variant_key
    assert first.name == "Conjunto Plim (3-6 meses / Azul / Plim Plim)"
    assert second.name == "Conjunto Plim (3-6 meses / Azul / Bam)"
    link = CategoryAttribute.objects.get(
        org=mini.org, category__name="Vestidos", attribute__code="pattern_name"
    )
    assert (link.role, link.is_required) == (CategoryAttribute.VARIANT_AXIS, False)


def test_c11_a_size_the_sheet_leaves_out_is_recovered(mini):
    """C11 (+). The apron sheet has no size column; ML's labels "4-6 años /
    Beige" and "6-8 años / Beige" give MLM300's variants their sizes."""
    apron = Listing.objects.get(external_id="MLM300").product
    assert sorted(apron.variants.values_list("size", flat=True)) == [
        "4-6 años",
        "6-8 años",
    ]
    assert mini.report.notes["sizes_recovered_from_label"] == 2


def test_c12_a_barcode_is_stored_only_when_it_is_a_real_code(mini):
    """C12 (+). U201's GTIN is a real code: it is the variant's `gtin`."""
    assert _published("U201").variant.gtin == "7501234567890"


def test_c13_no_barcode_stores_nothing(mini):
    """C13 (-). A barcode is optional. ML's prompt "Escribe el código" and a
    reason for having none ("Otra razón") store nothing: no `gtin`, and no
    attribute standing in for it."""
    assert _published("900001").variant.gtin == ""
    assert _published("900002").variant.gtin == ""
    assert not Attribute.objects.filter(org=mini.org, code="empty_gtin_reason").exists()
    assert not AttributeValue.objects.filter(
        org=mini.org, value_text__icontains="razón"
    ).exists()


def test_c14_a_row_with_no_price_is_rejected(mini):
    """C14 (-). U203 has no price: nothing of it is loaded, and the report
    says which sheet, row, publication and product, and why."""
    assert not Listing.objects.filter(external_id="MLM203").exists()
    assert _rejected(mini.report, "U203") == [
        "Vestidos, row 9: publication MLM203, product U203 was not loaded: "
        "it has no price."
    ]


def test_c15_a_row_priced_zero_is_rejected(mini):
    """C15 (-). U204's price is 0: rejected like a missing price."""
    assert not ListingVariant.objects.filter(external_product_id="U204").exists()
    assert "its price is 0" in _rejected(mini.report, "U204")[0]


def test_c16_a_row_missing_from_the_prices_file_is_rejected(mini):
    """C16 (-). U205 is only in the characteristics file: it has no price or
    sale conditions."""
    assert not ListingVariant.objects.filter(external_product_id="U205").exists()
    assert "not in the prices file" in _rejected(mini.report, "U205")[0]


def test_c17_a_prices_row_missing_from_the_characteristics_is_rejected(mini):
    """C17 (-). U999 is only in the prices file: it has no characteristics,
    so it is rejected and named with its row in the prices sheet."""
    assert not Listing.objects.filter(external_id="MLM999").exists()
    assert _rejected(mini.report, "U999") == [
        "Publicaciones, row 22: publication MLM999, product U999 was not loaded: "
        "it is not in the characteristics file, so it has no characteristics."
    ]


def test_c18_a_product_with_no_loadable_row_is_not_created(mini):
    """C18 (-). MLM400's only row has no price: the product is not created.
    No hoodie row has a price: no hoodie product, and no attribute only
    hoodies use."""
    assert not CatalogProduct.objects.filter(name="Vestido Sin Precio").exists()
    assert not CatalogProduct.objects.filter(name="Sudadera").exists()
    assert not Attribute.objects.filter(org=mini.org, code="with_hood").exists()
    assert len(mini.report.rejected) == 6


def test_c19_the_active_publication_of_a_variant_is_loaded(mini):
    """C19 (+). U202 (inactive) and U206 (active) are one variant. It is
    loaded from U206: its price (579), stock (4) and main color (Verde)."""
    variant = _published("U206").variant
    assert (variant.price, variant.stock) == (Decimal("579.00"), 4)
    family = variant.catalog_product
    assert _values(family, variant)["main_color"] == ["Verde"]
    assert family.variants.filter(variant_key=variant.variant_key).count() == 1


def test_c20_the_inactive_twin_is_ignored_with_a_warning(mini):
    """C20 (-). U202 comes before U206 and U207 after U201, but both are
    inactive twins of an active publication: nothing of them is kept (no
    listing, no link, not their price) and the report says so."""
    for number, item in (("U202", "MLM202"), ("U207", "MLM207")):
        assert not ListingVariant.objects.filter(external_product_id=number).exists()
        assert not Listing.objects.filter(external_id=item).exists()
        assert "ignored: that one is active" in _ignored(mini.report, number)[0]
    assert _published("U201").variant.price == Decimal("529.00")


def test_c21_of_two_inactive_twins_the_later_in_the_file_is_loaded(mini):
    """C21 (+). U209 and U208 are one variant, both inactive, and the report
    has no update dates. U208 comes later in the file, so it is loaded
    (price 640, stock 9, main color Rosa) although MLM209 is the higher
    publication number; U209 is ignored with a warning."""
    variant = _published("U208").variant
    assert (variant.price, variant.stock, variant.size) == (
        Decimal("640.00"),
        9,
        "12-18 meses",
    )
    assert _values(variant.catalog_product, variant)["main_color"] == ["Rosa"]
    assert not ListingVariant.objects.filter(external_product_id="U209").exists()
    assert not Listing.objects.filter(external_id="MLM209").exists()
    assert "that one comes later in the file" in _ignored(mini.report, "U209")[0]
    assert mini.report.notes["duplicate_publications_ignored"] == 3


def test_c22_a_characteristic_is_never_stored_twice(mini):
    """C22 (-). The 3-6 meses / Azul / Bam variant was published twice with
    different main colors: it has one main color row, the loaded (active)
    publication's, and no variant or product has any attribute twice."""
    variant = _published("U206").variant
    rows = AttributeValue.objects.filter(variant=variant, attribute__code="main_color")
    assert [r.option.value for r in rows] == ["Verde"]
    single = AttributeValue.objects.filter(
        org=mini.org, attribute__is_multivalued=False
    )
    owners = single.values_list("product_id", "variant_id", "attribute_id")
    assert len(owners) == len(set(owners))


def _snapshot(org):
    return sorted(
        Product.objects.filter(org=org).values_list(
            "id",
            "name",
            "variant_key",
            "price",
            "currency",
            "size",
            "stock",
            "is_active",
        )
    )


def test_c23_reimporting_changes_nothing(mini):
    """C23 (+). A second import matches every row by its ML ids: nothing is
    created and every variant, value and listing is as it was."""
    before = (_snapshot(mini.org), AttributeValue.objects.filter(org=mini.org).count())
    again = import_mercadolibre(mini.org, mini.files.characteristics, mini.files.prices)
    for kind in ("products", "variants", "listings", "listing_variants", "categories"):
        assert again.created.get(kind, 0) == 0, kind
    assert (
        _snapshot(mini.org),
        AttributeValue.objects.filter(org=mini.org).count(),
    ) == before
    assert len(again.rejected) == 6


def test_c24_a_currency_fix_and_reimport_changes_only_the_currency(mini):
    """C24 (+). The org's currency was wrong (MXN). Set it to USD, import the
    same files again: every variant is in USD, and nothing else changed."""
    before = [row[:4] + row[5:] for row in _snapshot(mini.org)]
    mini.org.default_currency = "USD"
    mini.org.save(update_fields=["default_currency"])
    import_mercadolibre(mini.org, mini.files.characteristics, mini.files.prices)
    after = _snapshot(mini.org)
    assert {row[4] for row in after} == {"USD"}
    assert [row[:4] + row[5:] for row in after] == before


def test_c25_reimporting_a_row_that_lost_its_price_leaves_the_variant(mini):
    """C25 (-). U201 is loaded at 529. A later report has no price for it: the
    row is rejected and the variant keeps its price and characteristics; it
    is not deleted."""
    rows = price_rows()
    for row in rows:
        if row.get("PRODUCT_NUMBER") == "U201":
            row["PRICE"] = ""
    later = write_prices(mini.files.folder / "later.xlsx", rows)
    report = import_mercadolibre(mini.org, mini.files.characteristics, later)
    assert "it has no price" in _rejected(report, "U201")[0]
    variant = _published("U201").variant
    assert variant.price == Decimal("529.00")
    family = Listing.objects.get(external_id="MLM201").product
    assert _values(family, variant)["main_color"] == ["Azul"]


def test_c26_a_new_value_replaces_the_old_one(mini):
    """C26 (+). A later report gives U201 the main color Blanco: the variant
    then has one main color, Blanco, not Azul and Blanco."""
    dresses = dress_rows()
    for row in dresses:
        if row.get("PRODUCT_NUMBER") == "U201":
            row["MAIN_COLOR"] = "Blanco"
    later = write_characteristics(
        mini.files.folder / "later-c.xlsx", mini_sheets(dresses)
    )
    import_mercadolibre(mini.org, later, mini.files.prices)
    variant = _published("U201").variant
    rows = AttributeValue.objects.filter(variant=variant, attribute__code="main_color")
    assert [r.option.value for r in rows] == ["Blanco"]


def test_c27_a_dry_run_saves_nothing(org_b, mini_report):
    """C27 (+). A dry run reports the same work and leaves the org untouched."""
    with rls_org(org_b):
        dry = import_mercadolibre(
            org_b, mini_report.characteristics, mini_report.prices, dry_run=True
        )
        assert dry.created["variants"] == 8
        assert len(dry.rejected) == 6
        assert not CatalogProduct.objects.filter(org=org_b).exists()
        assert not Category.objects.filter(org=org_b).exists()


def test_c28_the_command_loads_and_prints_the_rejections(org_a, mini_report, capsys):
    """C28 (+). `import_mercadolibre --org <name>` loads the catalog and
    prints its report, rejected rows and ignored twins included."""
    call_command(
        "import_mercadolibre",
        org=org_a.name,
        characteristics=str(mini_report.characteristics),
        prices=str(mini_report.prices),
    )
    out = capsys.readouterr().out
    assert '"variants": 8' in out
    assert "product U203 was not loaded" in out
    assert "product U202 is the same variant" in out
    assert "6 rows were rejected" in out
    assert CatalogProduct.objects.filter(org=org_a).count() == 4


def test_c29_the_command_refuses_an_unknown_org_or_a_missing_file(org_a, mini_report):
    """C29 (-). An org that does not exist, a prices file that is not there
    and a missing --prices argument are each a CommandError; nothing loads."""
    files = {
        "characteristics": str(mini_report.characteristics),
        "prices": str(mini_report.prices),
    }
    with pytest.raises(CommandError, match="No organization"):
        call_command("import_mercadolibre", org="No such org", **files)
    with pytest.raises(CommandError, match="prices file"):
        call_command(
            "import_mercadolibre",
            org=org_a.name,
            characteristics=files["characteristics"],
            prices=str(mini_report.folder / "gone.xlsx"),
        )
    with pytest.raises(CommandError, match="--prices"):
        call_command(
            "import_mercadolibre",
            org=org_a.name,
            characteristics=files["characteristics"],
        )
    assert not CatalogProduct.objects.filter(org=org_a).exists()


def _reprice(changes):
    """The mini prices file with `changes` ({product number: {column: value}})
    applied."""
    rows = price_rows()
    for row in rows:
        row.update(changes.get(row.get("PRODUCT_NUMBER"), {}))
    return rows


def _import_prices(mini, name, changes):
    path = write_prices(mini.files.folder / name, _reprice(changes))
    return import_mercadolibre(mini.org, mini.files.characteristics, path)


def test_c30_of_two_active_twins_the_later_in_the_file_is_loaded(org_a, mini_report):
    """C30 (+). U201 (row 7) and U207 (row 13) are one variant. With both
    active and no update dates, U207 comes later in the file and is loaded:
    its price 599 and main color Rosa. U201 is ignored with a warning."""
    prices = write_prices(
        mini_report.folder / "both-active.xlsx",
        _reprice({"U207": {"STATUS": "Activa"}}),
    )
    report = import_mercadolibre(org_a, mini_report.characteristics, prices)
    variant = _published("U207").variant
    assert variant.price == Decimal("599.00")
    assert _values(variant.catalog_product, variant)["main_color"] == ["Rosa"]
    assert not ListingVariant.objects.filter(external_product_id="U201").exists()
    assert "that one comes later in the file" in _ignored(report, "U201")[0]


def test_c31_the_twin_the_channel_updated_last_is_loaded(org_a, mini_report):
    """C31 (+). U209 and U208, both inactive, now carry the channel's update
    date (LAST_UPDATED). U209 was updated last, so it is loaded although U208
    comes later in the file; U208 is ignored, and the warning says why."""
    prices = write_prices(
        mini_report.folder / "dated.xlsx",
        _reprice(
            {
                "U209": {"LAST_UPDATED": "2026-10-01T10:00:00.000-06:00"},
                "U208": {"LAST_UPDATED": "2026-09-01T10:00:00.000-06:00"},
            }
        ),
    )
    report = import_mercadolibre(org_a, mini_report.characteristics, prices)
    variant = _published("U209").variant
    assert (variant.price, variant.stock) == (Decimal("650.00"), 2)
    assert not ListingVariant.objects.filter(external_product_id="U208").exists()
    assert "Mercado Libre updated that one last" in _ignored(report, "U208")[0]


def test_c32_an_update_date_does_not_beat_the_active_publication(org_a, mini_report):
    """C32 (-). U202 (inactive) was updated on the channel after U206
    (active). The active one is still the one loaded."""
    prices = write_prices(
        mini_report.folder / "late-inactive.xlsx",
        _reprice(
            {
                "U202": {"LAST_UPDATED": "2026-12-01"},
                "U206": {"LAST_UPDATED": "2026-01-01"},
            }
        ),
    )
    report = import_mercadolibre(org_a, mini_report.characteristics, prices)
    assert _published("U206").variant.price == Decimal("579.00")
    assert not ListingVariant.objects.filter(external_product_id="U202").exists()
    assert "that one is active" in _ignored(report, "U202")[0]


def test_c33_a_date_on_only_one_twin_is_not_compared(org_a, mini_report):
    """C33 (-). Only U209 has an update date. A date cannot be compared with
    none, so the file order decides: U208, later in the file, is loaded."""
    prices = write_prices(
        mini_report.folder / "one-dated.xlsx",
        _reprice({"U209": {"LAST_UPDATED": "2026-10-01"}}),
    )
    report = import_mercadolibre(org_a, mini_report.characteristics, prices)
    assert _published("U208").variant.price == Decimal("640.00")
    assert "that one comes later in the file" in _ignored(report, "U209")[0]


@pytest.mark.parametrize(
    "written, expected",
    [
        ("2026-10-01T10:00:00.000-06:00", "2026-10-01T16:00:00+00:00"),
        ("2026-10-01T16:00:00Z", "2026-10-01T16:00:00+00:00"),
        ("01/10/2026 10:00", "2026-10-01T10:00:00+00:00"),
        ("2026-10-01", "2026-10-01T00:00:00+00:00"),
    ],
)
def test_c34_the_channels_update_date_is_kept_on_the_listing(
    org_a, mini_report, written, expected
):
    """C34 (+). The date is stored on the listing, from ISO text with or
    without a zone or from day/month/year text. One without a zone is read
    in the org's timezone (UTC here)."""
    org_a.timezone = "UTC"
    org_a.save(update_fields=["timezone"])
    prices = write_prices(
        mini_report.folder / "dated-one.xlsx",
        _reprice({"U201": {"LAST_UPDATED": written}}),
    )
    import_mercadolibre(org_a, mini_report.characteristics, prices)
    listing = Listing.objects.get(org=org_a, external_id="MLM201")
    assert listing.channel_updated_at.isoformat() == expected
    assert (
        Listing.objects.get(org=org_a, external_id="MLM206").channel_updated_at is None
    )


def test_c35_a_category_with_no_products_is_created_and_reported(mini):
    """C35 (+). No hoodie row could be loaded. The category exists all the
    same, with no products, and the report names it, with how many of its
    rows there were and how many were rejected."""
    hoodies = Category.objects.get(org=mini.org, name="Sudaderas y hoodies")
    assert hoodies.products.count() == 0
    assert Category.objects.filter(org=mini.org).count() == 3
    assert mini.report.empty_categories == [
        {
            "category": "Sudaderas y hoodies",
            "sheet": "Sudaderas y hoodies",
            "rows": 1,
            "rejected": 1,
        }
    ]


def test_c36_the_command_names_each_category_with_no_products(
    org_a, mini_report, capsys
):
    """C36 (+). The command prints a line for the hoodies: no products, all
    its rows rejected."""
    call_command(
        "import_mercadolibre",
        org=org_a.name,
        characteristics=str(mini_report.characteristics),
        prices=str(mini_report.prices),
    )
    out = capsys.readouterr().out
    assert (
        "Category Sudaderas y hoodies: no products, all 1 of its rows were rejected."
        in out
    )


def test_c37_a_store_with_mercado_libres_code_stops_the_import(org_a, mini_report):
    """C37 (-). A physical store already has the code the importer gives
    Mercado Libre. A store has no listings, so the import stops with a
    readable error and saves nothing."""
    SalesChannel.objects.create(
        org=org_a, name="Tienda Centro", code="mercadolibre", kind=SalesChannel.STORE
    )
    with pytest.raises(SpreadsheetError, match="is a physical store"):
        import_mercadolibre(org_a, mini_report.characteristics, mini_report.prices)
    assert not CatalogProduct.objects.filter(org=org_a).exists()
    assert not Category.objects.filter(org=org_a).exists()


# =============================================================================
# D. Importing the real report
# =============================================================================


@pytest.fixture(scope="module")
def ml(django_db_setup, django_db_blocker):
    """The real report, imported once for this module.

    Created outside the per-test transactions so every D test reads the same
    rows, and removed with the org at the end. Tests must not change it (D14
    re-imports inside its own transaction, which is rolled back).
    """
    with django_db_blocker.unblock():
        org = Org.objects.create(
            name="Camille (Mercado Libre report)", default_currency="MXN"
        )
        report = import_mercadolibre(org, CHARACTERISTICS, PRICES)
    yield SimpleNamespace(org=org, report=report)
    with django_db_blocker.unblock():
        org.delete()


def _ml_variants(org):
    return Product.objects.filter(org=org, catalog_product__isnull=False)


def test_d1_the_real_report_builds_the_base_catalog(ml):
    """D1 (+). 8 categories, one per sheet (the hoodies' with no product, as
    none of its rows has a price), 89 products, 939 variants, 784 publications
    and 939 variant-publication links, on one Mercado Libre channel."""
    org = ml.org
    assert sorted(Category.objects.filter(org=org).values_list("name", flat=True)) == [
        "Cobijas para bebés",
        "Conjuntos para bebés",
        "Disfraces",
        "Mamelucos para bebés",
        "Mandiles de cocina",
        "Ropa para bebés",
        "Sudaderas y hoodies",
        "Vestidos",
    ]
    assert (
        Category.objects.get(org=org, name="Sudaderas y hoodies").products.count() == 0
    )
    assert CatalogProduct.objects.filter(org=org).count() == 89
    assert _ml_variants(org).count() == 939
    assert Listing.objects.filter(org=org).count() == 784
    assert ListingVariant.objects.filter(org=org).count() == 939
    channel = SalesChannel.objects.get(org=org)
    assert (channel.code, channel.site) == ("mercadolibre", "MLM")


def test_d2_unpriced_rows_are_rejected_and_named(ml):
    """D2 (-). The 15 hoodie rows are not in the prices file and 18 rows have
    a blank price: 33 rows rejected, each named, none loaded. Every prices
    row has its characteristics."""
    rejected = ml.report.rejected
    assert len(rejected) == 33
    assert len([r for r in rejected if "not in the prices file" in r]) == 15
    assert (
        len([r for r in rejected if r.endswith("not loaded: it has no price.")]) == 18
    )
    assert not [r for r in rejected if "characteristics file" in r]
    assert not Listing.objects.filter(
        channel__org=ml.org, external_id="MLM2646408225"
    ).exists()
    assert not _ml_variants(ml.org).filter(price__lte=0).exists()


def test_d3_no_two_variants_share_size_and_axes(ml):
    """D3 (+). Size plus axis values identify a variant: every key is unique
    in its product."""
    keys = list(_ml_variants(ml.org).values_list("catalog_product_id", "variant_key"))
    assert len(keys) == len(set(keys))


def test_d4_duplicate_publications_keep_the_later_in_the_file(ml):
    """D4 (-). Two pairs of inactive publications are one variant each (once
    "6-9" is 6-9 meses and "9-12" is 9-12 meses). The report has no update
    dates, so the one later in the file is loaded (rows 264 and 208 of the
    Mamelucos sheet), the other ignored with a warning."""
    assert ml.report.notes["duplicate_publications_ignored"] == 2
    for kept, ignored in (
        ("U3989721170", "U4715126011"),
        ("U3989721174", "U4744150202"),
    ):
        assert ListingVariant.objects.filter(
            org=ml.org, external_product_id=kept
        ).exists()
        assert not ListingVariant.objects.filter(
            org=ml.org, external_product_id=ignored
        ).exists()
        assert "that one comes later in the file" in _ignored(ml.report, ignored)[0]


def test_d5_shared_characteristics_are_one_attribute(ml):
    """D5 (+). Material is one attribute whether ML calls it MAIN_MATERIAL,
    MATERIAL or MATERIALS, and the mapping remembers each domain's code.
    Gender is one attribute linked to the 7 categories that have it."""
    material = Attribute.objects.get(org=ml.org, code="material")
    assert sorted(
        material.channel_mappings.values_list("external_code", "external_domain")
    ) == [
        ("MAIN_MATERIAL", "MLM-COSTUMES"),
        ("MAIN_MATERIAL", "MLM-DRESSES"),
        ("MAIN_MATERIAL", "MLM-SWEATSHIRTS_AND_HOODIES"),
        ("MATERIAL", "MLM-BABY_BLANKETS"),
        ("MATERIALS", "MLM-KITCHEN_APRONS"),
    ]
    assert Attribute.objects.get(org=ml.org, code="gender").category_links.count() == 7


@pytest.mark.parametrize(
    "code",
    [
        "size",
        "empty_gtin_reason",
        "invoice_product_name",
        "sat_key",
        "measure_unit_key",
        "measure_unit_description",
        "iva_for_resale",
        "ieps",
        "composition",
        "release_year",
        "hypoallergenic",
        "with_hood",
        "garment_type",
        "fit",
    ],
)
def test_d6_empty_columns_have_no_representation(ml, code):
    """D6 (-). Columns no loaded row filled in (invoice data, composition,
    release year, ...) and hoodie-only columns (the hoodies were rejected)
    create no attribute. Size is a column, never an attribute, and a barcode
    reason is not stored."""
    assert not Attribute.objects.filter(org=ml.org, code=code).exists()


def test_d7_a_classic_item_with_a_stock_per_size(ml):
    """D7 (+). MLM4549556180 is one listing with four variations at 619, in
    12 meses, 18 meses, 2A and 3A (24 and 36 meses), with stock 15, 18, 13
    and 2."""
    classic = Listing.objects.get(channel__org=ml.org, external_id="MLM4549556180")
    variants = classic.product.variants.order_by("size_sort")
    assert [(v.size, v.stock, v.price) for v in variants] == [
        ("12 meses", 15, Decimal("619.00")),
        ("18 meses", 18, Decimal("619.00")),
        ("2A", 13, Decimal("619.00")),
        ("3A", 2, Decimal("619.00")),
    ]
    assert classic.product.name == "Vestidito Plim Plim Para Fiesta"


def test_d8_a_user_product_family_is_one_product(ml):
    """D8 (+). Family 7441400827737232 has 31 publications; 30 are priced, and
    they are one Vestidos product with 30 variants."""
    family = Listing.objects.filter(external_group_id="7441400827737232")
    assert family.count() == 30
    product = family.first().product
    assert product.category.name == "Vestidos"
    assert product.variants.count() == 30


def test_d9_variants_of_one_product_carry_different_prices(ml):
    """D9 (+). U3802788905 sells at 529 and its sibling U3802800831 at 435;
    30 products have variants at different prices."""
    a = ListingVariant.objects.get(
        org=ml.org, external_product_id="U3802788905"
    ).variant
    b = ListingVariant.objects.get(
        org=ml.org, external_product_id="U3802800831"
    ).variant
    assert a.catalog_product_id == b.catalog_product_id
    assert (a.price, b.price) == (Decimal("529.00"), Decimal("435.00"))
    varied = [
        p
        for p in CatalogProduct.objects.filter(org=ml.org)
        if p.variants.values("price").distinct().count() > 1
    ]
    assert len(varied) == 30


def test_d10_missing_axes_are_recovered_from_mls_labels(ml):
    """D10 (+). A Conjunto's 60 variants are told apart by design (Plim Plim,
    Bam, Acuarella, Hoggie), named only in ML's labels; apron MLM4473265762
    gets its sizes the same way."""
    conjunto = (
        Listing.objects.filter(external_group_id="4454985263648094").first().product
    )
    assert conjunto.variants.count() == 60
    designs = AttributeValue.objects.filter(
        product=conjunto, attribute__code="pattern_name"
    ).values_list("option__value", flat=True)
    assert sorted(set(designs)) == ["Acuarella", "Bam", "Hoggie", "Plim Plim"]
    apron = Listing.objects.get(channel__org=ml.org, external_id="MLM4473265762")
    assert sorted(apron.product.variants.values_list("size", flat=True)) == [
        "10-12 años",
        "4-6 años",
        "6-8 años",
        "8-10 años",
    ]
    assert ml.report.notes["designs_recovered_from_label"] == 104
    assert ml.report.notes["sizes_recovered_from_label"] == 4


def test_d11_the_store_size_decisions_hold_on_the_real_data(ml):
    """D11 (+). No variant keeps a bare number, a bare range, 1A, 0 meses,
    24 or 36 meses. The adult costume "Disfraz Juan Diego 3 Pz" runs 12 meses
    (its "1"), 2A ... 10A. 56 variants are "Unique"."""
    variants = _ml_variants(ml.org)
    kept = [
        "1",
        "2",
        "3",
        "10",
        "3-6",
        "6-9",
        "9-12",
        "1A",
        "0 meses",
        "24 meses",
        "36 meses",
    ]
    assert not variants.filter(size__in=kept).exists()
    assert variants.filter(size=UNIQUE_SIZE).count() == 56
    juan_diego = variants.filter(catalog_product__name="Disfraz Juan Diego 3 Pz")
    assert sorted(set(juan_diego.values_list("size_sort", "size"))) == sorted(
        (size_sort_order(s), s)
        for s in ("12 meses", "2A", "3A", "4A", "5A", "6A", "7A", "8A", "9A", "10A")
    )


def test_d12_filters_on_the_real_catalog(ml):
    """D12 (+). 89 variants come in 2A, and ML's spelling "2 Años" finds the
    same ones; 70 are for babies in 0-3 months; 854 have stock, 85 none, and
    the stock adds up to 15,302 units."""
    variants = _ml_variants(ml.org)
    gender = Attribute.objects.get(org=ml.org, code="gender")
    assert narrow_variants(variants, sizes=["2A"]).count() == 89
    assert narrow_variants(variants, sizes=[canonical_size("2 Años")]).count() == 89
    assert narrow_variants(variants, [(gender, ["Bebés"])], ["0-3 meses"]).count() == 70
    assert narrow_variants(variants, in_stock=True).count() == 854
    assert narrow_variants(variants, in_stock=False).count() == 85
    assert sum(variants.values_list("stock", flat=True)) == 15302


def test_d13_every_price_is_in_the_orgs_currency(ml):
    """D13 (+). Every variant is priced in MXN, the org's currency, whatever
    the report's currency column says, and every price is above 0."""
    variants = _ml_variants(ml.org)
    assert set(variants.values_list("currency", flat=True)) == {"MXN"}
    assert min(variants.values_list("price", flat=True)) > 0


def test_d14_reimporting_the_real_report_creates_nothing(ml):
    """D14 (+). Importing the same files again matches every row: nothing is
    created and every variant is as it was. Its report counts each of the
    784 listings once, and each of the 939 variants once. (Runs inside this
    test's transaction, so the module's rows are untouched.)"""
    before = _snapshot(ml.org)
    again = import_mercadolibre(ml.org, CHARACTERISTICS, PRICES)
    for kind in ("products", "variants", "listings", "listing_variants", "categories"):
        assert again.created.get(kind, 0) == 0, kind
    assert (again.updated["listings"], again.updated["variants"]) == (784, 939)
    assert _snapshot(ml.org) == before


def test_d15_no_barcode_is_made_up(ml):
    """D15 (-). The report's barcode column holds only ML's prompt ("Escribe
    el código") and reasons for having none, no real code: no variant gets a
    barcode."""
    variants = _ml_variants(ml.org)
    assert not variants.exclude(gtin="").exists()
    assert not variants.filter(gtin__in=["Escribe el código", "Otra razón"]).exists()


def test_d16_a_currency_fix_changes_only_the_currency(ml):
    """D16 (+). With the org's currency corrected to USD, importing again
    moves every variant to USD and changes nothing else. (Rolled back with
    the test's transaction.)"""
    before = [row[:4] + row[5:] for row in _snapshot(ml.org)]
    ml.org.default_currency = "USD"
    ml.org.save(update_fields=["default_currency"])
    import_mercadolibre(ml.org, CHARACTERISTICS, PRICES)
    after = _snapshot(ml.org)
    assert {row[4] for row in after} == {"USD"}
    assert [row[:4] + row[5:] for row in after] == before


def test_d17_the_hoodies_are_reported_as_a_category_with_no_products(ml):
    """D17 (+). Every one of the 15 rows of the "Sudaderas y hoodies" sheet was
    rejected, so its category has no products, and the report says so in
    its own list, apart from the rejected rows."""
    assert ml.report.empty_categories == [
        {
            "category": "Sudaderas y hoodies",
            "sheet": "Sudaderas y hoodies",
            "rows": 15,
            "rejected": 15,
        }
    ]


def test_d18_the_mi_primer_ano_nuevo_set_is_sized_in_months(ml):
    """D18 (+). The store's exception to "a bare number is years": the baby
    set "Sudadera Conjunto Bebé Mi Primer Año Nuevo", written 3, 6, 9 and
    12, comes in 3, 6, 9 and 12 meses, sorted among the months."""
    baby_set = _ml_variants(ml.org).filter(
        catalog_product__name__startswith="Sudadera Conjunto Bebé Mi"
    )
    assert list(baby_set.order_by("size_sort").values_list("size", flat=True)) == [
        "3 meses",
        "6 meses",
        "9 meses",
        "12 meses",
    ]


# =============================================================================
# A small catalog built in code, for the model, query and API tests
# =============================================================================


def _attribute(org, code, data_type, *, multi=False, unit="", options=()):
    attribute = Attribute.objects.create(
        org=org,
        code=code,
        name=code.replace("_", " ").title(),
        data_type=data_type,
        is_multivalued=multi,
        unit=unit,
    )
    for order, value in enumerate(options):
        AttributeOption.objects.create(
            org=org, attribute=attribute, value=value, sort_order=order
        )
    return attribute


def build_dresses(org):
    """Vestidos with one required axis (color) and five characteristics; one
    dress in RN and 0-3 meses, both red, made of Algodón except the 0-3 meses
    one (Poliéster); an ML listing publishing the RN one. Prices in MXN."""
    org.default_currency = "MXN"
    org.save(update_fields=["default_currency"])
    color = _attribute(org, "color", Attribute.OPTION, options=("Rojo", "Azul"))
    material = _attribute(
        org, "material", Attribute.OPTION, options=("Algodón", "Poliéster")
    )
    occasions = _attribute(
        org, "occasions", Attribute.OPTION, multi=True, options=("Fiesta", "Diario")
    )
    sportive = _attribute(org, "is_sportive", Attribute.BOOLEAN)
    width = _attribute(org, "width", Attribute.NUMBER, unit="cm")
    model = _attribute(org, "model", Attribute.TEXT)
    category = Category.objects.create(org=org, name="Vestidos")
    CategoryAttribute.objects.create(
        org=org,
        category=category,
        attribute=color,
        role=CategoryAttribute.VARIANT_AXIS,
        is_required=True,
    )
    for attribute in (material, occasions, sportive, width, model):
        CategoryAttribute.objects.create(
            org=org,
            category=category,
            attribute=attribute,
            role=CategoryAttribute.CHARACTERISTIC,
        )
    product = CatalogProduct.objects.create(org=org, name="Vestido", category=category)
    set_values(product, None, material, [{"option": find_option(material, "Algodón")}])
    newborn = save_variant(
        product,
        None,
        {"color": "Rojo"},
        {"price": Decimal("499.00"), "currency": "MXN", "size": "RN", "stock": 5},
    )
    baby = save_variant(
        product,
        None,
        {"color": "Rojo", "material": "Poliéster"},
        {
            "price": Decimal("549.00"),
            "currency": "MXN",
            "size": "0-3 M",
            "sku": "VES-03",
        },
    )
    channel = SalesChannel.objects.create(
        org=org, name="Mercado Libre", code="mercadolibre"
    )
    listing = Listing.objects.create(
        org=org, channel=channel, product=product, external_id="MLM1", title="Vestido"
    )
    published = ListingVariant.objects.create(
        org=org, listing=listing, variant=newborn, external_product_id="U1"
    )
    return SimpleNamespace(
        org=org,
        category=category,
        product=product,
        newborn=newborn,
        baby=baby,
        channel=channel,
        listing=listing,
        published=published,
        color=color,
        material=material,
        occasions=occasions,
        sportive=sportive,
        width=width,
        model=model,
    )


@pytest.fixture
def dresses(org_a):
    return build_dresses(org_a)


def _variants(org):
    return Product.objects.filter(org=org, catalog_product__isnull=False)


# =============================================================================
# E. Model invariants and deletion (of variants, products and organizations)
# =============================================================================


def test_e1_size_and_axes_are_unique_within_a_product(dresses):
    """E1 (-). The database refuses a second variant with the same key in one
    product; the same key in another product is fine."""
    with pytest.raises(IntegrityError), transaction.atomic():
        Product.objects.create(
            org=dresses.org,
            name="Copy",
            catalog_product=dresses.product,
            variant_key=dresses.newborn.variant_key,
        )
    other = CatalogProduct.objects.create(
        org=dresses.org, name="Otro", category=dresses.category
    )
    Product.objects.create(
        org=dresses.org,
        name="Same key elsewhere",
        catalog_product=other,
        variant_key=dresses.newborn.variant_key,
    )


def test_e2_one_value_row_per_owner_and_attribute(dresses):
    """E2 (-). A second product-wide model, or the same color twice on one
    variant, is refused by the database; a list takes one row per option.
    (Two different colors on one variant are refused earlier: see F2.)"""
    AttributeValue.objects.create(
        org=dresses.org,
        product=dresses.product,
        attribute=dresses.model,
        value_text="A",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        AttributeValue.objects.create(
            org=dresses.org,
            product=dresses.product,
            attribute=dresses.model,
            value_text="B",
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        AttributeValue.objects.create(
            org=dresses.org,
            product=dresses.product,
            variant=dresses.newborn,
            attribute=dresses.color,
            option=find_option(dresses.color, "Rojo"),
        )
    for value in ("Fiesta", "Diario"):
        AttributeValue.objects.create(
            org=dresses.org,
            product=dresses.product,
            attribute=dresses.occasions,
            option=find_option(dresses.occasions, value),
        )


def test_e3_only_option_lists_take_several_values(org_a):
    """E3 (-). A multi-valued text attribute is refused by the database."""
    with pytest.raises(IntegrityError), transaction.atomic():
        Attribute.objects.create(
            org=org_a, code="x", name="X", data_type=Attribute.TEXT, is_multivalued=True
        )


def test_e4_stock_cannot_be_negative(org_a):
    """E4 (-). The database refuses a negative stock."""
    with pytest.raises(IntegrityError), transaction.atomic():
        Product.objects.create(org=org_a, name="Body", price=1, stock=-1)


def _org_with_sales(org):
    """`org` with the dress catalog, one invoice, one order and one recurring
    invoice."""
    with rls_org(org):
        build_dresses(org)
        account = Account.objects.create(name="Cliente", org=org)
        Invoice.objects.create(org=org, invoice_title="Venta", account=account)
        Order.objects.create(org=org, name="Pedido", account=account)
        RecurringInvoice.objects.create(org=org, title="Mensual", account=account)
    return org


def _org_with_deals(org, deals=2):
    """`org` with the dress catalog and `deals` deals, and no sales."""
    with rls_org(org):
        build_dresses(org)
        for n in range(deals):
            Opportunity.objects.create(org=org, name=f"Trato {n}", stage="PROSPECTING")
    return org


def _everything_of(org_id):
    return {
        "orgs": Org.objects.filter(pk=org_id).count(),
        "invoices": Invoice.objects.filter(org_id=org_id).count(),
        "orders": Order.objects.filter(org_id=org_id).count(),
        "recurring": RecurringInvoice.objects.filter(org_id=org_id).count(),
        "deals": Opportunity.objects.filter(org_id=org_id).count(),
        "variants": Product.objects.filter(org_id=org_id).count(),
        "categories": Category.objects.filter(org_id=org_id).count(),
    }


NOTHING_LEFT = {
    "orgs": 0,
    "invoices": 0,
    "orders": 0,
    "recurring": 0,
    "deals": 0,
    "variants": 0,
    "categories": 0,
}


def test_e5_an_org_with_sales_is_not_deleted(org_b, admin_user):
    """E5 (-). An org with an invoice, an order and a recurring invoice is
    not deleted, whichever way it is asked: `delete_org` refuses it naming
    the sales, and deleting the instance or a queryset is refused too
    (Django's own PROTECT on the invoice's account stops those). An org whose
    only sales are orders is stopped by the org's pre_delete guard. Nothing
    at all is removed."""
    _org_with_sales(org_b)
    before = _everything_of(org_b.id)
    with pytest.raises(
        OrgHasSales,
        match="has 1 invoice, 1 order and 1 recurring invoice, so it cannot",
    ):
        delete_org(org_b, user=admin_user)
    with rls_org(org_b):
        with pytest.raises((OrgHasSales, ProtectedError)), transaction.atomic():
            org_b.delete()
        with pytest.raises((OrgHasSales, ProtectedError)), transaction.atomic():
            Org.objects.filter(pk=org_b.pk).delete()
    assert (
        _everything_of(org_b.id)
        == before
        == {
            "orgs": 1,
            "invoices": 1,
            "orders": 1,
            "recurring": 1,
            "deals": 0,
            "variants": 2,
            "categories": 1,
        }
    )
    orders_only = Org.objects.create(name="Solo pedidos")
    with rls_org(orders_only):
        account = Account.objects.create(name="Cliente", org=orders_only)
        Order.objects.create(org=orders_only, name="Pedido", account=account)
        with pytest.raises(OrgHasSales, match="has 1 order, so it cannot"):
            with transaction.atomic():
                orders_only.delete()
    assert Order.objects.filter(org=orders_only).count() == 1


def _sell_everywhere(d, customer):
    """Put the RN dress on one of each record that sells products."""
    org, variant = d.org, d.newborn
    deal = Opportunity.objects.create(org=org, name="Boutique", stage="PROSPECTING")
    deal.line_items.create(org=org, product=variant, quantity=1)
    order = Order.objects.create(org=org, name="Pedido 1", account=customer.account)
    OrderLineItem.objects.create(
        org=org, order=order, product=variant, name=variant.name
    )
    invoice = Invoice.objects.create(
        org=org, invoice_title="Venta", account=customer.account, currency="MXN"
    )
    InvoiceLineItem.objects.create(
        org=org, invoice=invoice, product=variant, quantity=1
    )
    estimate = Estimate.objects.create(
        org=org, title="Cotización", account=customer.account
    )
    EstimateLineItem.objects.create(
        org=org, estimate=estimate, product=variant, quantity=1
    )
    recurring = RecurringInvoice.objects.create(
        org=org, title="Mensual", account=customer.account
    )
    RecurringInvoiceLineItem.objects.create(
        org=org, recurring_invoice=recurring, product=variant, quantity=1
    )


def test_e6_a_product_that_is_sold_is_not_deleted(admin_client, dresses, customer):
    """E6 (-). The RN dress is on a deal, an order, an invoice, an estimate and
    a recurring invoice: deleting the product is refused (409), the message
    names each, and the product and its variants are still there."""
    _sell_everywhere(dresses, customer)
    response = admin_client.delete(f"/api/catalog/products/{dresses.product.id}/")
    assert response.status_code == 409
    message = response.json()["detail"]
    for what in ("1 deal", "1 order", "1 invoice", "1 estimate", "1 recurring invoice"):
        assert what in message, what
    assert "Retire the product instead" in message
    assert dresses.product.variants.count() == 2


def test_e7_a_sold_variant_is_not_deleted_through_either_api(admin_client, dresses):
    """E7 (-). A variant on a deal is refused by /api/catalog/variants/ and by
    /api/invoices/products/ alike, and stays."""
    deal = Opportunity.objects.create(org=dresses.org, name="Deal", stage="PROSPECTING")
    deal.line_items.create(org=dresses.org, product=dresses.newborn, quantity=1)
    catalog_api = admin_client.delete(f"/api/catalog/variants/{dresses.newborn.id}/")
    assert (catalog_api.status_code, "1 deal" in catalog_api.json()["detail"]) == (
        409,
        True,
    )
    products_api = admin_client.delete(f"/api/invoices/products/{dresses.newborn.id}/")
    assert products_api.status_code == 409
    assert "1 deal" in products_api.json()["message"]
    assert Product.objects.filter(pk=dresses.newborn.pk).exists()


def test_e8_an_unsold_product_is_deleted_with_its_variants(admin_client, dresses):
    """E8 (+). Nothing sells the dress: it is deleted, its variants, values
    and listings with it."""
    response = admin_client.delete(f"/api/catalog/products/{dresses.product.id}/")
    assert response.status_code == 204
    assert not Product.objects.filter(catalog_product_id=dresses.product.id).exists()
    assert not Listing.objects.filter(pk=dresses.listing.pk).exists()


def test_e9_a_standalone_sold_product_deletes_as_before(admin_client, org_a, customer):
    """E9 (+). A plain product (no catalog product) on an invoice is deleted
    through /api/invoices/products/ as it always was; the invoice line keeps
    its own name and price."""
    plain = Product.objects.create(org=org_a, name="Consultoría", price=100)
    invoice = Invoice.objects.create(
        org=org_a, invoice_title="Servicio", account=customer.account, currency="MXN"
    )
    line = InvoiceLineItem.objects.create(
        org=org_a,
        invoice=invoice,
        product=plain,
        name="Consultoría",
        quantity=1,
        unit_price=100,
    )
    assert admin_client.delete(f"/api/invoices/products/{plain.id}/").status_code == 200
    line.refresh_from_db()
    assert (line.product, line.name, line.unit_price) == (
        None,
        "Consultoría",
        Decimal("100.00"),
    )


def test_e10_an_org_without_sales_is_deleted_with_its_whole_catalog(org_b):
    """E10 (+). No invoice and no order: the org is deleted, and its whole
    catalog with it (categories, attributes and channels in use refuse to be
    deleted on their own, but not as part of their org)."""
    with rls_org(org_b):
        build_dresses(org_b)
        org_b.delete()
        assert not Product.objects.filter(org_id=org_b.id).exists()
        assert not Attribute.objects.filter(org_id=org_b.id).exists()
        assert not SalesChannel.objects.filter(org_id=org_b.id).exists()


def test_e11_only_a_superuser_may_delete_an_org_with_its_sales(org_b):
    """E11 (-). `delete_org(..., with_sales=True)` refuses an org admin who is
    not a superuser, and an inactive superuser; nothing is removed."""
    _org_with_sales(org_b)
    before = _everything_of(org_b.id)
    admin = User.objects.create_user(email="boss@b.test", password="x")
    gone = User.objects.create_superuser(
        email="old-root@b.test", password="x", is_active=False
    )
    for user in (admin, gone, None):
        with pytest.raises(PermissionDenied):
            delete_org(org_b, user=user, with_sales=True)
    assert _everything_of(org_b.id) == before


def test_e12_a_superuser_deletes_an_org_with_its_sales_and_it_is_audited(org_b):
    """E12 (+). A superuser deletes the org with its sales: the org, its
    invoice, order, recurring invoice and catalog are gone, and the security
    audit log records who did it and which sales went."""
    _org_with_sales(org_b)
    root = User.objects.create_superuser(email="root@b.test", password="x")
    org_id, name = org_b.id, org_b.name
    delete_org(org_b, user=root, with_sales=True)
    assert _everything_of(org_id) == NOTHING_LEFT
    row = SecurityAuditLog.objects.get(event_type="ORG_DELETED")
    assert (row.user, row.metadata) == (
        root,
        {
            "org_id": str(org_id),
            "org_name": name,
            "sales": {"invoice": 1, "order": 1, "recurring invoice": 1},
            "deals": 0,
        },
    )


@pytest.fixture
def django_admin():
    """A Django admin session for a superuser."""
    root = User.objects.create_superuser(email="root@admin.test", password="x")
    client = Client()
    client.force_login(root)
    return client


def test_e13_the_admin_names_the_sales_before_deleting(django_admin, org_b):
    """E13 (+). The superuser's delete page for an org with sales says that
    deleting it cannot be undone, names its sales, and asks for a tick to
    confirm they go too, before anything happens."""
    _org_with_sales(org_b)
    page = django_admin.get(f"/admin/common/org/{org_b.pk}/delete/")
    assert page.status_code == 200
    html = page.content.decode()
    assert "Deleting an organization cannot be undone." in html
    assert (
        "This organization has sales: 1 invoice, 1 order and 1 recurring invoice."
        in html
    )
    assert 'name="delete_sales"' in html
    assert (
        "I understand that 1 invoice, 1 order and 1 recurring invoice will be "
        "deleted with this organization, and that this cannot be undone." in html
    )
    assert Org.objects.filter(pk=org_b.pk).exists()


def test_e14_the_admin_deletes_nothing_without_the_confirmation(django_admin, org_b):
    """E14 (-). Posting the delete page without ticking the box deletes
    nothing and says why."""
    _org_with_sales(org_b)
    before = _everything_of(org_b.id)
    response = django_admin.post(
        f"/admin/common/org/{org_b.pk}/delete/", {"post": "yes"}, follow=True
    )
    assert "Nothing was deleted" in response.content.decode()
    assert _everything_of(org_b.id) == before


def test_e15_the_admin_deletes_the_org_and_its_sales_once_confirmed(
    django_admin, org_b
):
    """E15 (+). With the box ticked the org goes, with its invoice, order,
    recurring invoice and catalog."""
    _org_with_sales(org_b)
    org_id = org_b.id
    response = django_admin.post(
        f"/admin/common/org/{org_id}/delete/", {"post": "yes", "delete_sales": "yes"}
    )
    assert response.status_code == 302
    assert _everything_of(org_id) == NOTHING_LEFT


def test_e16_staff_who_are_not_superusers_cannot_delete_an_org(org_b):
    """E16 (-). A staff user with every model permission is still refused the
    delete page (403), and the list offers no bulk delete to anyone."""
    from django.contrib.auth.models import Permission

    staff = User.objects.create_user(email="staff@admin.test", password="x")
    staff.is_staff = True
    staff.save()
    staff.user_permissions.set(
        Permission.objects.filter(content_type__app_label="common")
    )
    client = Client()
    client.force_login(staff)
    assert client.get(f"/admin/common/org/{org_b.pk}/delete/").status_code == 403
    assert (
        client.post(
            f"/admin/common/org/{org_b.pk}/delete/", {"post": "yes"}
        ).status_code
        == 403
    )
    assert Org.objects.filter(pk=org_b.pk).exists()
    root = User.objects.create_superuser(email="root2@admin.test", password="x")
    client.force_login(root)
    listing = client.get("/admin/common/org/").content.decode()
    assert 'value="delete_selected"' not in listing


def test_e19_an_org_whose_only_sales_are_recurring_invoices_is_not_deleted(
    org_b, admin_user
):
    """E19 (-). A recurring invoice is a sale: an org with one, and nothing
    else sold, is refused like one with invoices."""
    with rls_org(org_b):
        account = Account.objects.create(name="Cliente", org=org_b)
        RecurringInvoice.objects.create(org=org_b, title="Mensual", account=account)
    with pytest.raises(OrgHasSales, match="has 1 recurring invoice, so it cannot"):
        delete_org(org_b, user=admin_user)
    assert Org.objects.filter(pk=org_b.pk).exists()
    assert RecurringInvoice.objects.filter(org=org_b).count() == 1


def test_e20_an_org_with_deals_is_not_deleted_until_they_are_agreed(org_b, admin_user):
    """E20 (-). Two deals and no sales: without agreeing that the deals go
    too, `delete_org` (the path a person takes, through the Django admin)
    refuses naming them, and nothing is removed. Code deleting the org
    directly has nobody to ask, and is not stopped for deals."""
    _org_with_deals(org_b)
    before = _everything_of(org_b.id)
    with pytest.raises(OrgHasDeals, match="has 2 deals, deleted with it"):
        delete_org(org_b, user=admin_user)
    assert _everything_of(org_b.id) == before
    assert before["deals"] == 2


def test_e21_once_agreed_the_org_goes_with_its_deals(org_b, admin_user):
    """E21 (+). Agreed (`with_deals=True`), the org is deleted with its deals
    and catalog, and the audit log records the two deals. No superuser is
    needed: there are no sales."""
    _org_with_deals(org_b)
    org_id = org_b.id
    delete_org(org_b, user=admin_user, with_deals=True)
    assert _everything_of(org_id) == NOTHING_LEFT
    row = SecurityAuditLog.objects.get(event_type="ORG_DELETED")
    assert (row.metadata["sales"], row.metadata["deals"]) == ({}, 2)


def test_e22_the_admin_warns_about_deals_and_asks_to_agree(django_admin, org_b):
    """E22 (+). The delete page of an org with deals says they are deleted
    with it and cannot be recovered, and asks for its own tick."""
    _org_with_deals(org_b)
    html = django_admin.get(f"/admin/common/org/{org_b.pk}/delete/").content.decode()
    assert "Deleting an organization cannot be undone." in html
    assert "This organization has 2 deals." in html
    assert 'name="delete_deals"' in html
    assert 'name="delete_sales"' not in html


def test_e23_the_admin_deletes_nothing_until_the_deals_are_agreed(django_admin, org_b):
    """E23 (-). Posted without the deals' tick, nothing is deleted and the
    page says why."""
    _org_with_deals(org_b)
    before = _everything_of(org_b.id)
    response = django_admin.post(
        f"/admin/common/org/{org_b.pk}/delete/", {"post": "yes"}, follow=True
    )
    assert "Tick each box to confirm that 2 deals are deleted too." in (
        response.content.decode()
    )
    assert _everything_of(org_b.id) == before


def test_e24_with_the_deals_agreed_the_admin_deletes_the_org(django_admin, org_b):
    """E24 (+). With the deals' tick the org goes, with its deals."""
    _org_with_deals(org_b)
    org_id = org_b.id
    response = django_admin.post(
        f"/admin/common/org/{org_id}/delete/", {"post": "yes", "delete_deals": "yes"}
    )
    assert response.status_code == 302
    assert _everything_of(org_id) == NOTHING_LEFT


def test_e17_an_unsold_variant_of_a_sold_product_is_deleted(admin_client, dresses):
    """E17 (+). The RN dress is on a deal; its sibling 0-3 meses is on
    nothing. The sibling is deleted; the product and the sold RN dress stay."""
    deal = Opportunity.objects.create(org=dresses.org, name="Deal", stage="PROSPECTING")
    deal.line_items.create(org=dresses.org, product=dresses.newborn, quantity=1)
    response = admin_client.delete(f"/api/catalog/variants/{dresses.baby.id}/")
    assert response.status_code == 204
    assert list(dresses.product.variants.all()) == [dresses.newborn]
    assert CatalogProduct.objects.filter(pk=dresses.product.pk).exists()


def test_e18_the_products_refusal_names_the_variants_with_sales(admin_client, dresses):
    """E18 (-). With only the RN dress sold, deleting the product is refused,
    and the message names that variant (RN / Rojo), not the unsold one, and
    says the others can be deleted one by one."""
    deal = Opportunity.objects.create(org=dresses.org, name="Deal", stage="PROSPECTING")
    deal.line_items.create(org=dresses.org, product=dresses.newborn, quantity=1)
    response = admin_client.delete(f"/api/catalog/products/{dresses.product.id}/")
    assert response.status_code == 409
    message = response.json()["detail"]
    assert "Variants with sales: RN / Rojo." in message
    assert "0-3 meses" not in message
    assert "delete its variants that have no sales one by one" in message
    assert dresses.product.variants.count() == 2


# =============================================================================
# F. Values and variants
# =============================================================================


def test_f1_option_lookup_ignores_case_accents_and_follows_aliases(dresses):
    """F1 (+). "algodon" is Algodón; an alias finds its option. (-) An
    unknown value finds nothing."""
    assert find_option(dresses.material, "algodon").value == "Algodón"
    assert find_option(dresses.material, "  ALGODÓN ").value == "Algodón"
    red = find_option(dresses.color, "Rojo")
    red.aliases = ["red"]
    red.save()
    assert find_option(dresses.color, "Red").value == "Rojo"
    assert find_option(dresses.material, "Lino") is None


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("material", "Lino"),
        ("material", ["Algodón", "Poliéster"]),
        ("sportive", "quizás"),
        ("width", "ancho"),
        ("width", "NaN"),
        ("model", "x" * 501),
        ("model", {"value": "x"}),
    ],
)
def test_f2_bad_values_are_refused_with_the_attribute_code(dresses, name, raw):
    """F2 (-). An unknown option, two values for a single-valued attribute, a
    non yes/no, a non-number, an over-long text and a non-text are refused,
    naming the attribute."""
    attribute = getattr(dresses, name)
    with pytest.raises(CatalogError) as refused:
        coerce_values(attribute, raw)
    assert refused.value.field == attribute.code


def test_f3_good_values_are_stored_in_their_type(dresses):
    """F3 (+). Sí/No are booleans, a number takes its unit, a list fills a
    multi-valued attribute once per option, and empty means nothing."""
    assert coerce_values(dresses.sportive, "Sí") == [{"value_boolean": True}]
    assert coerce_values(dresses.width, {"value": "1", "unit": "m"}) == [
        {"value_number": Decimal("1"), "unit": "m"}
    ]
    assert coerce_values(dresses.width, 35) == [
        {"value_number": Decimal("35"), "unit": "cm"}
    ]
    assert len(coerce_values(dresses.occasions, ["Fiesta", "Diario", "fiesta"])) == 2
    assert coerce_values(dresses.model, "") == []
    assert coerce_values(dresses.model, None) == []


def test_f4_a_variant_needs_its_axes_and_a_combination_of_its_own(dresses):
    """F4 (-). Leaving out the required color, or repeating a sibling's size
    and color in any spelling, is refused before anything is written."""
    before = Product.objects.count()
    with pytest.raises(CatalogError, match="color"):
        save_variant(dresses.product, None, {}, {"price": 1, "size": "3-6 meses"})
    with pytest.raises(CatalogError, match="Another variant") as clash:
        save_variant(
            dresses.product, None, {"color": "ROJO"}, {"price": 1, "size": "rn"}
        )
    assert clash.value.existing == dresses.newborn
    assert Product.objects.count() == before


def test_f5_a_product_comes_in_only_the_sizes_it_has(dresses):
    """F5 (+). The same color in another size is a new variant; the product
    carries RN, 0-3 meses and 2A and nothing else, and a product with a single
    variant of no size is fine."""
    save_variant(dresses.product, None, {"color": "Rojo"}, {"price": 599, "size": "2A"})
    sizes = _variants(dresses.org).filter(catalog_product=dresses.product)
    assert sorted(sizes.values_list("size_sort", "size")) == sorted(
        (size_sort_order(s), s) for s in ("RN", "0-3 meses", "2A")
    )
    apron = CatalogProduct.objects.create(
        org=dresses.org, name="Mandil", category=dresses.category
    )
    only = save_variant(apron, None, {"color": "Azul"}, {"price": 399})
    assert (only.size, only.stock) == (UNIQUE_SIZE, 0)


def test_f6_a_variants_name_is_built_from_product_size_and_axes(dresses):
    """F6 (+). Names read "<product> (<size> / <color>)"; "Unique" is left
    out of the name but kept in the key."""
    assert dresses.newborn.name == "Vestido (RN / Rojo)"
    assert dresses.baby.name == "Vestido (0-3 meses / Rojo)"
    apron = CatalogProduct.objects.create(
        org=dresses.org, name="Mandil", category=dresses.category
    )
    only = save_variant(apron, None, {"color": "Azul"}, {"price": 399})
    assert only.name == "Mandil (Azul)"
    assert only.variant_key == "size=unique|color=azul"


# =============================================================================
# G. Querying
# =============================================================================


def test_g1_product_values_count_for_variants_unless_overridden(dresses):
    """G1 (+). The dress says Algodón once; the 0-3 meses variant says
    Poliéster for itself. Algodón finds only RN, Poliéster only 0-3 meses."""
    found = filter_variants(_variants(dresses.org), [(dresses.material, ["algodon"])])
    assert list(found) == [dresses.newborn]
    found = filter_variants(_variants(dresses.org), [(dresses.material, ["Poliéster"])])
    assert list(found) == [dresses.baby]


def test_g2_alternatives_within_a_code_and_all_codes_must_match(admin_client, dresses):
    """G2 (+). Colors Rojo or Azul find both variants; adding material
    Poliéster leaves the 0-3 meses one."""
    both = admin_client.get("/api/catalog/variants/?attr=color:Rojo&attr=color:Azul")
    assert both.json()["count"] == 2
    one = admin_client.get(
        "/api/catalog/variants/?attr=color:Rojo&attr=color:Azul&attr=material:Poliéster"
    )
    assert [v["id"] for v in one.json()["results"]] == [str(dresses.baby.id)]


@pytest.mark.parametrize(
    "query",
    [
        "attr=nope:1",
        "attr=size:RN",
        "attr=color",
        "attr=:Rojo",
        "attr=is_sportive:quizás",
        "&".join(["attr=color:Rojo"] * 11),
        "category=not-a-uuid",
    ],
)
def test_g3_bad_filters_are_a_400(admin_client, dresses, query):
    """G3 (-). An unknown code (size is a column, not an attribute), a filter
    without a value, a value its type cannot read, more than 10 filters or a
    malformed id is a 400, never a silently wider list."""
    assert admin_client.get(f"/api/catalog/variants/?{query}").status_code == 400
    assert admin_client.get(f"/api/catalog/products/?{query}").status_code == 400


def test_g4_size_and_stock_filters_on_variants(admin_client, dresses):
    """G4 (+). `size=` takes any spelling ("0-3 M" is 0-3 meses) and repeats;
    `in_stock=true` finds the RN variant (5 units), `false` the other."""
    one = admin_client.get("/api/catalog/variants/?size=0-3%20M").json()
    assert [v["id"] for v in one["results"]] == [str(dresses.baby.id)]
    both = admin_client.get("/api/catalog/variants/?size=RN&size=0-3%20meses").json()
    assert both["count"] == 2
    stocked = admin_client.get("/api/catalog/variants/?in_stock=true").json()
    assert [v["size"] for v in stocked["results"]] == ["RN"]
    empty = admin_client.get("/api/catalog/variants/?in_stock=false").json()
    assert [v["size"] for v in empty["results"]] == ["0-3 meses"]


def test_g5_product_filters_must_hold_for_one_variant(admin_client, dresses):
    """G5 (-). The dress has an Azul variant only in 2A. Asking for 0-3 meses
    and Azul finds no product, although it has both, in different variants;
    asking for 2A and Azul finds it."""
    save_variant(dresses.product, None, {"color": "Azul"}, {"price": 599, "size": "2A"})
    url = "/api/catalog/products/?attr=color:Azul&size="
    assert admin_client.get(url + "0-3%20meses").json()["count"] == 0
    assert admin_client.get(url + "2A").json()["count"] == 1


def test_g6_the_sizes_endpoint_lists_the_sizes_in_use(admin_client, dresses):
    """G6 (+). Only the sizes some variant has, youngest first, with counts;
    narrowed by category, an empty category has none."""
    rows = admin_client.get("/api/catalog/sizes/").json()["results"]
    assert rows == [
        {"size": "RN", "variant_count": 1},
        {"size": "0-3 meses", "variant_count": 1},
    ]
    empty = Category.objects.create(org=dresses.org, name="Pijamas")
    assert (
        admin_client.get(f"/api/catalog/sizes/?category={empty.id}").json()["results"]
        == []
    )


def test_g7_the_product_list_pages(admin_client, dresses):
    """G7 (+). With 3 products and a page of 2, the first page has a next
    link and no previous, the second the reverse; the count is 3 on both."""
    for name in ("Vestido B", "Vestido C"):
        CatalogProduct.objects.create(
            org=dresses.org, name=name, category=dresses.category
        )
    first = admin_client.get("/api/catalog/products/?limit=2").json()
    assert (first["count"], len(first["results"]), first["previous"]) == (3, 2, None)
    assert first["next"]
    second = admin_client.get("/api/catalog/products/?limit=2&offset=2").json()
    assert (second["count"], len(second["results"]), second["next"]) == (3, 1, None)
    assert second["previous"]
    names = [p["name"] for p in first["results"] + second["results"]]
    assert names == ["Vestido", "Vestido B", "Vestido C"]


# =============================================================================
# H. API
# =============================================================================


def _writes(d):
    """Every write endpoint, with a payload an admin could send."""
    link = d.category.attribute_links.get(attribute=d.model)
    option = find_option(d.color, "Azul")
    return [
        ("post", "/api/catalog/channels/", {"name": "Amazon", "code": "amazon"}),
        ("patch", f"/api/catalog/channels/{d.channel.id}/", {"site": "MLM"}),
        ("delete", f"/api/catalog/channels/{d.channel.id}/", None),
        (
            "post",
            "/api/catalog/attributes/",
            {"code": "season", "name": "Temporada", "data_type": "TEXT"},
        ),
        ("patch", f"/api/catalog/attributes/{d.model.id}/", {"name": "Modelo"}),
        ("delete", f"/api/catalog/attributes/{d.model.id}/", None),
        ("post", f"/api/catalog/attributes/{d.color.id}/options/", {"value": "Verde"}),
        ("patch", f"/api/catalog/attribute-options/{option.id}/", {"sort_order": 9}),
        ("delete", f"/api/catalog/attribute-options/{option.id}/", None),
        ("post", "/api/catalog/categories/", {"name": "Pijamas"}),
        ("patch", f"/api/catalog/categories/{d.category.id}/", {"name": "V"}),
        ("delete", f"/api/catalog/categories/{d.category.id}/", None),
        (
            "post",
            f"/api/catalog/categories/{d.category.id}/attributes/",
            {"attribute": str(d.width.id), "role": "CHARACTERISTIC"},
        ),
        ("patch", f"/api/catalog/category-attributes/{link.id}/", {"sort_order": 2}),
        ("delete", f"/api/catalog/category-attributes/{link.id}/", None),
        (
            "post",
            "/api/catalog/products/",
            {"name": "Nuevo", "category": str(d.category.id)},
        ),
        ("patch", f"/api/catalog/products/{d.product.id}/", {"name": "X"}),
        ("delete", f"/api/catalog/products/{d.product.id}/", None),
        (
            "post",
            f"/api/catalog/products/{d.product.id}/variants/",
            {"price": "1.00", "size": "2A", "attributes": {"color": "Azul"}},
        ),
        ("patch", f"/api/catalog/variants/{d.newborn.id}/", {"price": "1.00"}),
        ("delete", f"/api/catalog/variants/{d.newborn.id}/", None),
        (
            "post",
            "/api/catalog/listings/",
            {
                "channel": str(d.channel.id),
                "product": str(d.product.id),
                "external_id": "MLM2",
                "title": "T",
            },
        ),
        ("patch", f"/api/catalog/listings/{d.listing.id}/", {"title": "T"}),
        ("delete", f"/api/catalog/listings/{d.listing.id}/", None),
        (
            "post",
            f"/api/catalog/listings/{d.listing.id}/variants/",
            {"variant": str(d.baby.id), "external_product_id": "U2"},
        ),
        (
            "patch",
            f"/api/catalog/listing-variants/{d.published.id}/",
            {"external_sku": "S"},
        ),
        ("delete", f"/api/catalog/listing-variants/{d.published.id}/", None),
    ]


READS = [
    "/api/catalog/channels/",
    "/api/catalog/attributes/",
    "/api/catalog/categories/",
    "/api/catalog/products/",
    "/api/catalog/variants/",
    "/api/catalog/sizes/",
    "/api/catalog/listings/",
]


def test_h1_any_member_reads_and_only_an_admin_writes(user_client, dresses):
    """H1 (+) a member (role USER) reads every list and detail. (-) Every
    write endpoint refuses them with 403 and changes nothing."""
    for url in READS:
        assert user_client.get(url).status_code == 200, url
    assert (
        user_client.get(f"/api/catalog/products/{dresses.product.id}/").status_code
        == 200
    )
    assert (
        user_client.get(f"/api/catalog/variants/{dresses.newborn.id}/").status_code
        == 200
    )
    for method, url, payload in _writes(dresses):
        response = getattr(user_client, method)(url, payload, format="json")
        assert response.status_code == 403, (method, url)
    dresses.newborn.refresh_from_db()
    assert dresses.newborn.price == Decimal("499.00")
    assert CatalogProduct.objects.filter(pk=dresses.product.pk).exists()


def test_h2_another_orgs_catalog_is_404_and_its_ids_are_refused(
    admin_client, org_b_client, org_b, dresses
):
    """H2 (-). Org B sees none of org A's catalog; every read or write on an
    org A id is 404. Org A cannot point at org B's category, channel or
    variant in a payload: a 400, like an id that does not exist."""
    for url in READS:
        assert org_b_client.get(url).json()["results"] == [], url
    assert (
        org_b_client.get(f"/api/catalog/products/{dresses.product.id}/").status_code
        == 404
    )
    assert (
        org_b_client.get(f"/api/catalog/variants/{dresses.newborn.id}/").status_code
        == 404
    )
    for method, url, payload in _writes(dresses):
        if url.count("/") == 4:
            continue  # a collection URL carries no org A id
        response = getattr(org_b_client, method)(url, payload, format="json")
        assert response.status_code == 404, (method, url)
    assert CatalogProduct.objects.filter(pk=dresses.product.pk).exists()

    with rls_org(org_b):
        theirs = build_dresses(org_b)
    cases = [
        ("/api/catalog/products/", {"name": "X", "category": str(theirs.category.id)}),
        (
            "/api/catalog/listings/",
            {
                "channel": str(theirs.channel.id),
                "product": str(dresses.product.id),
                "external_id": "MLM9",
                "title": "T",
            },
        ),
        (
            f"/api/catalog/listings/{dresses.listing.id}/variants/",
            {"variant": str(theirs.newborn.id)},
        ),
    ]
    for url, payload in cases:
        assert admin_client.post(url, payload, format="json").status_code == 400, url


def test_h3_no_credentials_no_catalog(unauthenticated_client, dresses):
    """H3 (-). Every catalog read needs a signed-in member."""
    for url in READS:
        assert unauthenticated_client.get(url).status_code in (401, 403), url


def test_h4_sales_channels(admin_client, dresses):
    """H4. (+) Add Amazon (online by default), change it, list both, delete
    Amazon. (-) A second channel with Amazon's code is refused; Mercado Libre
    is not deleted while it has listings."""
    response = admin_client.post(
        "/api/catalog/channels/", {"name": "Amazon", "code": "amazon"}, format="json"
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "ONLINE"
    amazon = response.json()["id"]
    duplicate = {"name": "Amazon MX", "code": "amazon"}
    assert (
        admin_client.post(
            "/api/catalog/channels/", duplicate, format="json"
        ).status_code
        == 400
    )
    response = admin_client.patch(
        f"/api/catalog/channels/{amazon}/", {"site": "A1AM78C64UM0Y8"}, format="json"
    )
    assert response.json()["site"] == "A1AM78C64UM0Y8"
    codes = [
        p["code"] for p in admin_client.get("/api/catalog/channels/").json()["results"]
    ]
    assert codes == ["amazon", "mercadolibre"]
    assert (
        admin_client.delete(f"/api/catalog/channels/{dresses.channel.id}/").status_code
        == 409
    )
    assert admin_client.delete(f"/api/catalog/channels/{amazon}/").status_code == 204


def test_h5_attributes_and_options(admin_client, dresses):
    """H5. (+) Create a multi-valued option list, add and edit an option,
    rename the attribute, delete both. (-) A duplicate code, a multi-valued
    text, a duplicate option, an option on a text attribute, a type change on
    an attribute in use, and deleting what products use are all refused."""
    response = admin_client.post(
        "/api/catalog/attributes/",
        {
            "code": "season",
            "name": "Temporada",
            "data_type": "OPTION",
            "is_multivalued": True,
        },
        format="json",
    )
    assert response.status_code == 201
    season = response.json()["id"]
    for payload in (
        {"code": "season", "name": "Otra", "data_type": "TEXT"},
        {"code": "notes", "name": "Notas", "data_type": "TEXT", "is_multivalued": True},
    ):
        assert (
            admin_client.post(
                "/api/catalog/attributes/", payload, format="json"
            ).status_code
            == 400
        )
    response = admin_client.post(
        f"/api/catalog/attributes/{season}/options/",
        {"value": "Verano", "aliases": ["Summer"]},
        format="json",
    )
    assert (response.status_code, response.json()["aliases"]) == (201, ["summer"])
    option = response.json()["id"]
    assert (
        admin_client.post(
            f"/api/catalog/attributes/{season}/options/",
            {"value": "verano"},
            format="json",
        ).status_code
        == 400
    )
    assert (
        admin_client.post(
            f"/api/catalog/attributes/{dresses.model.id}/options/",
            {"value": "A"},
            format="json",
        ).status_code
        == 400
    )
    response = admin_client.patch(
        f"/api/catalog/attribute-options/{option}/", {"sort_order": 3}, format="json"
    )
    assert response.json()["sort_order"] == 3
    response = admin_client.patch(
        f"/api/catalog/attributes/{season}/",
        {"name": "Temporada de uso"},
        format="json",
    )
    assert response.json()["options"][0]["value"] == "Verano"
    assert (
        admin_client.patch(
            f"/api/catalog/attributes/{dresses.material.id}/",
            {"data_type": "TEXT"},
            format="json",
        ).status_code
        == 400
    )
    codes = [
        a["code"]
        for a in admin_client.get("/api/catalog/attributes/").json()["results"]
    ]
    assert "season" in codes
    used = find_option(dresses.material, "Algodón")
    assert (
        admin_client.delete(f"/api/catalog/attribute-options/{used.id}/").status_code
        == 409
    )
    assert (
        admin_client.delete(f"/api/catalog/attributes/{dresses.color.id}/").status_code
        == 409
    )
    assert (
        admin_client.delete(f"/api/catalog/attribute-options/{option}/").status_code
        == 204
    )
    assert admin_client.delete(f"/api/catalog/attributes/{season}/").status_code == 204


def test_h6_categories_and_their_attributes(admin_client, dresses):
    """H6. (+) Create a category, link an attribute and make it an axis while
    the category has no variants, rename a category (its variants follow),
    delete what is unused. (-) A duplicate name, a new or removed axis once a
    category has variants, a category inside itself, and deleting a category
    in use are refused."""
    response = admin_client.post(
        "/api/catalog/categories/", {"name": "Pijamas"}, format="json"
    )
    assert response.status_code == 201
    pijamas = response.json()["id"]
    assert (
        admin_client.post(
            "/api/catalog/categories/", {"name": "Pijamas"}, format="json"
        ).status_code
        == 400
    )
    response = admin_client.post(
        f"/api/catalog/categories/{pijamas}/attributes/",
        {"attribute": str(dresses.color.id), "role": "CHARACTERISTIC"},
        format="json",
    )
    assert response.status_code == 201
    link = response.json()["id"]
    response = admin_client.patch(
        f"/api/catalog/category-attributes/{link}/",
        {"role": "VARIANT_AXIS", "is_required": True},
        format="json",
    )
    assert response.json()["role"] == "VARIANT_AXIS"
    assert (
        admin_client.post(
            f"/api/catalog/categories/{dresses.category.id}/attributes/",
            {"attribute": str(dresses.width.id), "role": "VARIANT_AXIS"},
            format="json",
        ).status_code
        == 400
    )
    color_link = dresses.category.attribute_links.get(attribute=dresses.color)
    assert (
        admin_client.delete(
            f"/api/catalog/category-attributes/{color_link.id}/"
        ).status_code
        == 400
    )
    assert (
        admin_client.patch(
            f"/api/catalog/categories/{pijamas}/", {"parent": pijamas}, format="json"
        ).status_code
        == 400
    )
    assert (
        admin_client.delete(
            f"/api/catalog/categories/{dresses.category.id}/"
        ).status_code
        == 409
    )
    response = admin_client.patch(
        f"/api/catalog/categories/{dresses.category.id}/",
        {"name": "Vestidos bebé"},
        format="json",
    )
    assert response.json()["product_count"] == 1
    dresses.newborn.refresh_from_db()
    assert dresses.newborn.category == "Vestidos bebé"
    names = {
        c["name"]
        for c in admin_client.get("/api/catalog/categories/").json()["results"]
    }
    assert names == {"Pijamas", "Vestidos bebé"}
    assert (
        admin_client.delete(f"/api/catalog/category-attributes/{link}/").status_code
        == 204
    )
    assert admin_client.delete(f"/api/catalog/categories/{pijamas}/").status_code == 204


def test_h7_products(admin_client, dresses):
    """H7. (+) Create a product with characteristics, list and filter it with
    its sizes, stock and price range, read it, rename it (its variants
    follow), drop a characteristic by sending null, delete it. (-) Unknown or
    mistyped characteristics and a category change while it has variants are
    refused."""
    category = str(dresses.category.id)
    body = {
        "name": "Vestido Nuevo",
        "category": category,
        "attributes": {
            "occasions": ["Fiesta", "Diario"],
            "is_sportive": "No",
            "width": {"value": 35, "unit": "cm"},
            "model": "Princesa",
        },
    }
    response = admin_client.post("/api/catalog/products/", body, format="json")
    assert response.status_code == 201
    created = response.json()
    values = {a["code"]: a["value"] for a in created["attributes"]}
    assert sorted(values.pop("occasions")) == ["Diario", "Fiesta"]
    assert values == {"is_sportive": False, "width": 35, "model": "Princesa"}
    for bad in ({"nope": 1}, {"occasions": ["Boda"]}, {"is_sportive": "quizás"}):
        response = admin_client.post(
            "/api/catalog/products/", {**body, "attributes": bad}, format="json"
        )
        assert response.status_code == 400, bad

    page = admin_client.get(
        f"/api/catalog/products/?category={category}&search=vestido"
    ).json()
    assert page["count"] == 2
    row = next(p for p in page["results"] if p["id"] == str(dresses.product.id))
    assert (row["sizes"], row["stock"], row["variant_count"]) == (
        ["RN", "0-3 meses"],
        5,
        2,
    )
    assert (row["price_min"], row["price_max"], row["listing_count"]) == (
        "499.00",
        "549.00",
        1,
    )

    detail = admin_client.get(f"/api/catalog/products/{dresses.product.id}/").json()
    assert detail["sizes"] == ["RN", "0-3 meses"]
    assert [a["code"] for a in detail["axes"]] == ["color"]
    assert [a["code"] for a in detail["attributes"]] == ["material"]
    assert [(v["size"], v["stock"], v["price"]) for v in detail["variants"]] == [
        ("RN", 5, "499.00"),
        ("0-3 meses", 0, "549.00"),
    ]
    assert detail["variants"][0]["listings"][0]["listing_external_id"] == "MLM1"

    response = admin_client.patch(
        f"/api/catalog/products/{dresses.product.id}/",
        {"name": "Vestido Princesa", "attributes": {"material": None}},
        format="json",
    )
    assert (response.status_code, response.json()["attributes"]) == (200, [])
    dresses.newborn.refresh_from_db()
    assert dresses.newborn.name == "Vestido Princesa (RN / Rojo)"
    other = Category.objects.create(org=dresses.org, name="Disfraces")
    assert (
        admin_client.patch(
            f"/api/catalog/products/{dresses.product.id}/",
            {"category": str(other.id)},
            format="json",
        ).status_code
        == 400
    )
    assert (
        admin_client.delete(f"/api/catalog/products/{created['id']}/").status_code
        == 204
    )


def test_h8_variants(admin_client, dresses):
    """H8. (+) Add a variant with price, size and stock (size in any spelling,
    currency always the org's), one with only a price (Unique, stock 0),
    change size, stock and price, read it alone and in the list, delete it.
    (-) No price, a price of 0, a negative stock, a repeated size and color, a
    missing color and a taken SKU are refused; a currency sent is ignored."""
    url = f"/api/catalog/products/{dresses.product.id}/variants/"
    body = {
        "price": "599.00",
        "size": "2 Años",
        "stock": 3,
        "sku": "VES-2A",
        "currency": "EUR",
        "attributes": {"color": "azul"},
    }
    response = admin_client.post(url, body, format="json")
    assert response.status_code == 201
    variant = response.json()
    assert (variant["name"], variant["size"], variant["stock"]) == (
        "Vestido (2A / Azul)",
        "2A",
        3,
    )
    assert variant["currency"] == "MXN"
    plain = admin_client.post(
        url, {"price": "399.00", "attributes": {"color": "Azul"}}, format="json"
    )
    assert (plain.json()["size"], plain.json()["stock"]) == ("Unique", 0)
    for bad in (
        {"size": "3A", "attributes": {"color": "Azul"}},
        {"price": "0", "size": "3A", "attributes": {"color": "Azul"}},
        {"price": "1.00", "size": "3A", "stock": -1, "attributes": {"color": "Azul"}},
        {"price": "1.00", "size": "2A", "attributes": {"color": "Azul"}},
        {"price": "1.00", "size": "3A"},
        {
            "price": "1.00",
            "size": "3A",
            "sku": "VES-03",
            "attributes": {"color": "Azul"},
        },
    ):
        assert admin_client.post(url, bad, format="json").status_code == 400, bad

    response = admin_client.patch(
        f"/api/catalog/variants/{variant['id']}/",
        {"price": "579.00", "size": "3A", "stock": 8},
        format="json",
    )
    assert (
        response.json()["price"],
        response.json()["size"],
        response.json()["stock"],
    ) == (
        "579.00",
        "3A",
        8,
    )
    assert (
        admin_client.patch(
            f"/api/catalog/variants/{variant['id']}/",
            {"size": "RN", "attributes": {"color": "Rojo"}},
            format="json",
        ).status_code
        == 400
    )
    assert (
        admin_client.patch(
            f"/api/catalog/variants/{variant['id']}/", {"price": "0"}, format="json"
        ).status_code
        == 400
    )

    baby = admin_client.get(f"/api/catalog/variants/{dresses.baby.id}/").json()
    assert baby["attributes"][0]["value"] == "Poliéster"
    listed = admin_client.get(
        f"/api/catalog/variants/?product={dresses.product.id}"
    ).json()
    assert [v["size"] for v in listed["results"]] == ["Unique", "RN", "0-3 meses", "3A"]
    assert admin_client.get("/api/catalog/variants/?search=VES-03").json()["count"] == 1
    assert (
        admin_client.delete(f"/api/catalog/variants/{variant['id']}/").status_code
        == 204
    )
    assert not Product.objects.filter(pk=variant["id"]).exists()


def test_h9_listings_and_the_variants_they_publish(admin_client, dresses):
    """H9. (+) Publish the dress on a second listing, publish a variant there,
    change its ids and the listing's status, list by channel and status,
    delete. (-) The same external id twice, a variant of another product,
    publishing a variant twice on one listing and moving a listing that
    publishes variants are refused."""
    body = {
        "channel": str(dresses.channel.id),
        "product": str(dresses.product.id),
        "external_id": "MLM2",
        "title": "Vestido Premium",
        "status": "ACTIVE",
        "condition": "NEW",
        "listing_type": "Premium",
        "handling_days": 3,
    }
    response = admin_client.post("/api/catalog/listings/", body, format="json")
    assert response.status_code == 201
    listing = response.json()["id"]
    assert (
        admin_client.post("/api/catalog/listings/", body, format="json").status_code
        == 400
    )

    url = f"/api/catalog/listings/{listing}/variants/"
    response = admin_client.post(
        url,
        {"variant": str(dresses.baby.id), "external_product_id": "U2"},
        format="json",
    )
    assert response.status_code == 201
    published = response.json()
    assert (published["channel"], published["listing_external_id"]) == (
        "mercadolibre",
        "MLM2",
    )
    other = CatalogProduct.objects.create(
        org=dresses.org, name="Otro", category=dresses.category
    )
    stranger = save_variant(other, None, {"color": "Azul"}, {"price": 1})
    for bad in ({"variant": str(stranger.id)}, {"variant": str(dresses.baby.id)}):
        assert admin_client.post(url, bad, format="json").status_code == 400, bad

    response = admin_client.patch(
        f"/api/catalog/listing-variants/{published['id']}/",
        {"external_sku": "SKU-2"},
        format="json",
    )
    assert response.json()["external_sku"] == "SKU-2"
    response = admin_client.patch(
        f"/api/catalog/listings/{listing}/", {"status": "INACTIVE"}, format="json"
    )
    assert (response.json()["status"], response.json()["variant_count"]) == (
        "INACTIVE",
        1,
    )
    assert (
        admin_client.patch(
            f"/api/catalog/listings/{listing}/",
            {"product": str(other.id)},
            format="json",
        ).status_code
        == 400
    )
    page = admin_client.get(
        f"/api/catalog/listings/?channel={dresses.channel.id}&status=INACTIVE"
    )
    assert [row["external_id"] for row in page.json()["results"]] == ["MLM2"]
    assert (
        admin_client.get(f"/api/catalog/products/?channel={dresses.channel.id}").json()[
            "count"
        ]
        == 1
    )
    assert (
        admin_client.delete(
            f"/api/catalog/listing-variants/{published['id']}/"
        ).status_code
        == 204
    )
    assert admin_client.delete(f"/api/catalog/listings/{listing}/").status_code == 204


def test_h10_renaming_an_option_renames_and_rekeys_its_variants(admin_client, dresses):
    """H10 (+). Rojo becomes "Rojo carmín": the red variants are renamed and
    re-keyed, so the key still spells the combination."""
    red = find_option(dresses.color, "Rojo")
    response = admin_client.patch(
        f"/api/catalog/attribute-options/{red.id}/",
        {"value": "Rojo carmín"},
        format="json",
    )
    assert response.status_code == 200
    dresses.newborn.refresh_from_db()
    assert dresses.newborn.name == "Vestido (RN / Rojo carmín)"
    assert dresses.newborn.variant_key == "size=rn|color=rojo carmin"


def test_h11_the_catalog_follows_the_products_modules(admin_client, org_a, dresses):
    """H11. (+) Like /api/invoices/products/, the catalog stays while Invoices
    or Deals is on. (-) With both off it answers the module-off 404."""
    set_modules_off(org_a, "invoices")
    assert admin_client.get("/api/catalog/products/").status_code == 200
    set_modules_off(org_a, "invoices", "deals")
    response = admin_client.get("/api/catalog/products/")
    assert (response.status_code, response.json()["code"]) == (404, "module_off")


# =============================================================================
# I. The behaviour the CRM already had
# =============================================================================


def test_i1_a_standalone_product_still_works_as_before(admin_client):
    """I1 (+). Creating, editing and deleting a plain product through
    /api/invoices/products/ is unchanged; it belongs to no catalog product and
    takes the defaults (Unique, stock 0)."""
    response = admin_client.post(
        "/api/invoices/products/",
        {"name": "Consultoría", "price": "100.00", "sku": "SRV-1"},
        format="json",
    )
    assert response.status_code == 201
    product = response.json()["product"]
    assert product["catalog_product"] is None
    stored = Product.objects.get(pk=product["id"])
    assert (stored.size, stored.stock) == ("Unique", 0)
    response = admin_client.put(
        f"/api/invoices/products/{product['id']}/",
        {"name": "Consultoría senior", "price": "150.00"},
        format="json",
    )
    assert response.json()["product"]["name"] == "Consultoría senior"
    assert (
        admin_client.delete(f"/api/invoices/products/{product['id']}/").status_code
        == 200
    )


def test_i2_the_products_list_shows_which_rows_are_variants(admin_client, dresses):
    """I2 (+). Variants are sellable products: listed for the line-item
    pickers with their one price, and naming their catalog product."""
    rows = admin_client.get("/api/invoices/products/?limit=1000").json()["results"]
    row = next(r for r in rows if r["name"] == "Vestido (RN / Rojo)")
    assert (row["catalog_product"], row["price"]) == (str(dresses.product.id), "499.00")


def test_i3_a_variant_is_not_renamed_through_invoices(admin_client, dresses):
    """I3 (-). Its name comes from the catalog, so renaming it here is a 400."""
    url = f"/api/invoices/products/{dresses.newborn.id}/"
    assert (
        admin_client.put(url, {"name": "Otro nombre"}, format="json").status_code == 400
    )
    dresses.newborn.refresh_from_db()
    assert dresses.newborn.name == "Vestido (RN / Rojo)"


def test_i4_a_variant_is_repriced_through_invoices(admin_client, dresses):
    """I4 (+). Sending its own name back with a new price, as the product edit
    form does, changes its one price."""
    url = f"/api/invoices/products/{dresses.newborn.id}/"
    response = admin_client.put(
        url, {"name": dresses.newborn.name, "price": "459.00"}, format="json"
    )
    assert response.status_code == 200
    dresses.newborn.refresh_from_db()
    assert dresses.newborn.price == Decimal("459.00")


def test_i5_a_deal_line_item_takes_the_variants_one_price(admin_client, dresses):
    """I5 (+). A deal line given only the variant takes its name and price."""
    deal = Opportunity.objects.create(
        org=dresses.org, name="Boutique", stage="PROSPECTING"
    )
    response = admin_client.post(
        f"/api/opportunities/{deal.id}/line-items/",
        {"product_id": str(dresses.baby.id), "quantity": "3"},
        format="json",
    )
    assert response.status_code == 201
    line = response.json()["line_item"]
    assert (line["name"], Decimal(line["unit_price"])) == (
        "Vestido (0-3 meses / Rojo)",
        Decimal("549.00"),
    )


def _invoice(account, contact, product, unit_price):
    return {
        "invoice_title": "Venta boutique",
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "currency": "MXN",
        "line_items": [
            {
                "product": str(product.id),
                "name": product.name,
                "quantity": 2,
                "unit_price": unit_price,
            }
        ],
    }


@pytest.fixture
def customer(org_a):
    account = Account.objects.create(name="Boutique Luna", org=org_a)
    contact = Contact.objects.create(
        first_name="Ana", last_name="Luna", email="ana@example.com", org=org_a
    )
    return SimpleNamespace(account=account, contact=contact)


def test_i6_an_invoice_line_sells_a_variant_at_its_one_price(
    admin_client, dresses, customer
):
    """I6 (+). An invoice line names the variant and carries its price (the
    web's line editor copies it from the product): two RN dresses at 499."""
    payload = _invoice(customer.account, customer.contact, dresses.newborn, "499.00")
    response = admin_client.post("/api/invoices/", payload, format="json")
    assert response.status_code == 201, response.content
    line = InvoiceLineItem.objects.get(product=dresses.newborn)
    assert (line.unit_price, line.quantity) == (Decimal("499.00"), Decimal("2"))
    assert line.invoice.total_amount == Decimal("998.00")


def test_i7_an_invoice_line_cannot_sell_another_orgs_variant(
    admin_client, org_b, dresses, customer
):
    """I7 (-). The existing guard still holds for variants: a line naming
    another org's variant is a 400 and no invoice is created."""
    with rls_org(org_b):
        theirs = build_dresses(org_b)
    payload = _invoice(customer.account, customer.contact, theirs.newborn, "499.00")
    assert (
        admin_client.post("/api/invoices/", payload, format="json").status_code == 400
    )
    assert not InvoiceLineItem.objects.filter(product=theirs.newborn).exists()


# =============================================================================
# J. Row-level security
# =============================================================================


def test_j1_every_catalog_table_is_registered_for_rls():
    """J1 (+). Each catalog table is in ORG_SCOPED_TABLES, and no org-owned
    table is left out of it."""
    assert set(CATALOG_TABLES) <= set(ORG_SCOPED_TABLES)
    assert [p for p in org_tables_not_covered() if p.startswith("catalog_")] == []


def _need_rls():
    if connection.vendor != "postgresql":
        pytest.skip("Row-level security is a PostgreSQL feature")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if cursor.fetchone()[0]:
            pytest.skip("Needs a role row-level security binds, as in CI")


@pytest.mark.postgres_only
def test_j2_catalog_tables_have_their_policies():
    """J2 (+). Every catalog table has RLS on, forced, and both policies."""
    if connection.vendor != "postgresql":
        pytest.skip("RLS policies exist only on PostgreSQL")
    assert rls_policy_gaps(CATALOG_TABLES) == []


@pytest.mark.postgres_only
def test_j3_another_orgs_catalog_is_invisible_and_unwritable(org_a, org_b):
    """J3 (-). Under org B's context none of org A's catalog rows are
    readable (its import runs and their files included), and writing a row
    for org A from org B's context is refused."""
    _need_rls()
    mine = build_dresses(org_a)
    run = ImportRun.objects.create(org=org_a, source="mercadolibre")
    ImportFile.objects.create(
        org=org_a, run=run, field="prices", name="p.xlsx", content=b"x"
    )
    with rls_org(org_b):
        assert not ImportRun.objects.exists()
        assert not ImportFile.objects.exists()
        assert not CatalogProduct.objects.exists()
        assert not Attribute.objects.exists()
        assert not AttributeValue.objects.exists()
        assert not ListingVariant.objects.exists()
        assert not Product.objects.filter(catalog_product__isnull=False).exists()
        with pytest.raises(DatabaseError), transaction.atomic():
            Category.objects.create(org=org_a, name="Planted")
    assert CatalogProduct.objects.get() == mine.product


# =============================================================================
# K. Importing from the web: sources, background runs, emails
# =============================================================================

SOURCES_URL = "/api/catalog/import/sources/"
RUNS = "/api/catalog/import/runs/"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _upload(path, name=None):
    return SimpleUploadedFile(name or path.name, path.read_bytes(), content_type=XLSX)


def _start(
    client, report, *, dry_run=True, notify=False, source="mercadolibre", **over
):
    files = {
        "characteristics": _upload(report.characteristics),
        "prices": _upload(report.prices),
    }
    files.update(over)
    payload = {
        "source": source,
        "dry_run": "true" if dry_run else "false",
        "notify": "true" if notify else "false",
        **{k: v for k, v in files.items() if v is not None},
    }
    return client.post(RUNS, payload, format="multipart")


@pytest.fixture
def worker(monkeypatch, django_capture_on_commit_callbacks):
    """Stands in for the Celery worker: `run()` carries out every run queued
    since the last call, as the task does, and returns their ids."""
    queued = []
    monkeypatch.setattr(
        "catalog.tasks.run_catalog_import.delay",
        lambda run_id, org_id: queued.append((run_id, org_id)),
    )

    def run():
        done = []
        while queued:
            run_id, org_id = queued.pop(0)
            run_catalog_import(run_id, org_id)
            done.append(run_id)
        return done

    return SimpleNamespace(
        run=run, queued=queued, capture=django_capture_on_commit_callbacks
    )


def _queued(worker, client, report, **kwargs):
    with worker.capture(execute=True):
        response = _start(client, report, **kwargs)
    assert response.status_code == 202, response.content
    return response.json()


def _nothing_imported(org):
    return not (
        CatalogProduct.objects.filter(org=org).exists()
        or Category.objects.filter(org=org).exists()
        or SalesChannel.objects.filter(org=org).exists()
    )


def test_k1_a_preview_runs_in_the_background_and_saves_nothing(
    admin_client, org_a, mini_report, worker
):
    """K1 (+). Starting a preview answers 202 at once with a queued run and
    its files stored. Run, it holds the whole report (what would be created,
    the rejected rows, the category with no products), nothing is saved, and
    the files are kept for the import."""
    started = _queued(worker, admin_client, mini_report, dry_run=True)
    assert (started["status"], started["dry_run"]) == ("QUEUED", True)
    assert ImportFile.objects.filter(run_id=started["id"]).count() == 2
    assert worker.run() == [started["id"]]
    run = admin_client.get(f"{RUNS}{started['id']}/").json()
    assert (run["status"], run["files_kept"]) == ("DONE", True)
    assert (
        run["report"]["created"]["products"],
        run["report"]["created"]["variants"],
    ) == (
        4,
        8,
    )
    assert len(run["report"]["rejected"]) == 6
    assert [c["category"] for c in run["report"]["empty_categories"]] == [
        "Sudaderas y hoodies"
    ]
    assert _nothing_imported(org_a)


def test_k2_an_import_loads_the_catalog_and_drops_its_files(
    admin_client, org_a, mini_report, worker
):
    """K2 (+). An import run loads the catalog into the admin's org, keeps its
    report, and no longer keeps the files."""
    started = _queued(worker, admin_client, mini_report, dry_run=False)
    worker.run()
    run = ImportRun.objects.get(pk=started["id"])
    assert (run.status, run.report["created"]["variants"]) == (ImportRun.DONE, 8)
    assert CatalogProduct.objects.filter(org=org_a).count() == 4
    assert not run.files.exists()


def test_k3_a_previews_files_are_imported_without_sending_them_again(
    admin_client, org_a, mini_report, worker
):
    """K3 (+). "Import these files" after a preview starts an import of the
    same files; it loads the catalog, and the preview keeps no copy."""
    preview = _queued(worker, admin_client, mini_report, dry_run=True)
    worker.run()
    with worker.capture(execute=True):
        response = admin_client.post(
            f"{RUNS}{preview['id']}/import/", {}, format="json"
        )
    assert response.status_code == 202, response.content
    assert response.json()["dry_run"] is False
    worker.run()
    assert CatalogProduct.objects.filter(org=org_a).count() == 4
    assert not ImportFile.objects.filter(run_id=preview["id"]).exists()
    assert ImportRun.objects.get(pk=response.json()["id"]).preview_of_id == UUID(
        preview["id"]
    )


def test_k4_a_member_can_neither_see_nor_start_imports(user_client, org_a, mini_report):
    """K4 (-). A member who is not an admin gets 403 from the sources, the
    runs and starting one; nothing is stored."""
    assert user_client.get(SOURCES_URL).status_code == 403
    assert user_client.get(RUNS).status_code == 403
    assert _start(user_client, mini_report).status_code == 403
    assert not ImportRun.objects.filter(org=org_a).exists()


@pytest.mark.parametrize("missing", ["characteristics", "prices"])
def test_k5_both_files_are_required(admin_client, org_a, mini_report, missing):
    """K5 (-). Without either file the start is a 400 naming it, and nothing
    is stored."""
    response = _start(admin_client, mini_report, **{missing: None})
    assert response.status_code == 400
    assert response.json()[missing] == [f"The {missing} file is required."]
    assert not ImportRun.objects.filter(org=org_a).exists()


@pytest.mark.parametrize(
    "name, content, message",
    [
        ("report.csv", b"ID,PRICE", "must be an .xlsx workbook"),
        ("report.xlsx", b"not a workbook", "is not an .xlsx workbook"),
        ("report.xlsx", b"", "is empty"),
    ],
)
def test_k6_a_file_that_is_not_a_workbook_is_refused(
    admin_client, org_a, mini_report, name, content, message
):
    """K6 (-). A file that is not an .xlsx by name, by content, or that is
    empty is a 400 at once, with a message that does not quote the file's
    name; nothing is stored."""
    response = _start(
        admin_client, mini_report, prices=SimpleUploadedFile(name, content)
    )
    assert response.status_code == 400
    detail = response.json()["prices"][0]
    assert message in detail and name not in detail
    assert not ImportRun.objects.filter(org=org_a).exists()


def test_k7_a_file_over_20_mb_is_refused(admin_client, org_a, mini_report):
    """K7 (-). A file one byte over the 20 MB limit is refused before it is
    read as a workbook; nothing is stored."""
    big = SimpleUploadedFile("big.xlsx", b"0" * (20 * 1024 * 1024 + 1))
    response = _start(admin_client, mini_report, characteristics=big)
    assert response.status_code == 400
    assert "exceeds the 20 MB limit" in response.json()["characteristics"][0]
    assert not ImportRun.objects.filter(org=org_a).exists()


def test_k8_swapped_files_fail_the_run_and_save_nothing(
    admin_client, org_a, mini_report, worker
):
    """K8 (-). The prices file sent as the characteristics file passes the
    upload checks, but its run fails with the reason, and nothing is saved."""
    started = _queued(
        worker,
        admin_client,
        mini_report,
        dry_run=False,
        characteristics=_upload(mini_report.prices),
        prices=_upload(mini_report.characteristics),
    )
    worker.run()
    run = admin_client.get(f"{RUNS}{started['id']}/").json()
    assert (run["status"], run["error"]) == (
        "FAILED",
        "The characteristics file has no category sheets.",
    )
    assert _nothing_imported(org_a)


def test_k9_no_credentials_no_import(unauthenticated_client, mini_report):
    """K9 (-). Every import endpoint needs a signed-in admin; nothing is
    stored."""
    before = ImportRun.objects.count()
    assert unauthenticated_client.get(SOURCES_URL).status_code in (401, 403)
    assert unauthenticated_client.get(RUNS).status_code in (401, 403)
    assert _start(unauthenticated_client, mini_report).status_code in (401, 403)
    assert ImportRun.objects.count() == before


def test_k10_an_import_lands_in_the_callers_org_only(
    org_b_client, admin_client, org_a, org_b, mini_report, worker
):
    """K10 (-). Org B's admin imports: the catalog and the run are org B's.
    Org A has none of it, and cannot open org B's run (404)."""
    started = _queued(worker, org_b_client, mini_report, dry_run=False)
    worker.run()
    with rls_org(org_b):
        assert CatalogProduct.objects.filter(org=org_b).count() == 4
    assert _nothing_imported(org_a)
    assert admin_client.get(f"{RUNS}{started['id']}/").status_code == 404
    assert admin_client.get(RUNS).json()["results"] == []


def _padded(path, megabytes):
    """The workbook at `path` with an extra sheet of random text, so it
    weighs about `megabytes` MB. The importer skips a sheet with no ML ids."""
    import secrets

    wb = openpyxl.load_workbook(path)
    ws = wb.create_sheet("Notas")
    chunk = 32_000
    # Hex text packs to about half its length, so twice as much is written.
    for row in range(1, int(2 * megabytes * 1024 * 1024 / chunk) + 2):
        ws.cell(row, 1, secrets.token_hex(chunk // 2))
    target = path.with_name(f"padded-{path.name}")
    wb.save(target)
    return target


def test_k11_a_file_above_the_old_5_mb_limit_is_imported(
    admin_client, org_a, mini_report, worker
):
    """K11 (+). A characteristics file of about 6 MB (over the previous 5 MB
    limit, under 20 MB) is stored and imported like any other."""
    heavy = _padded(mini_report.characteristics, 6)
    assert 5 * 1024 * 1024 < heavy.stat().st_size < 20 * 1024 * 1024
    started = _queued(
        worker, admin_client, mini_report, dry_run=False, characteristics=_upload(heavy)
    )
    worker.run()
    assert ImportRun.objects.get(pk=started["id"]).status == ImportRun.DONE
    assert CatalogProduct.objects.filter(org=org_a).count() == 4


def test_k12_sources_list_mercado_libre_and_every_other_channel(admin_client, dresses):
    """K12 (+). Mercado Libre is available, with its two files. Every other
    channel of the org (here Amazon and a physical store) is listed too, not
    available, saying why."""
    SalesChannel.objects.create(org=dresses.org, name="Amazon", code="amazon")
    SalesChannel.objects.create(
        org=dresses.org, name="Tienda Centro", code="centro", kind=SalesChannel.STORE
    )
    listed = admin_client.get(SOURCES_URL).json()["results"]
    assert [(s["name"], s["available"]) for s in listed] == [
        ("Mercado Libre", True),
        ("Amazon", False),
        ("Tienda Centro", False),
    ]
    assert [f["field"] for f in listed[0]["files"]] == ["characteristics", "prices"]
    assert listed[1]["reason"] == (
        "Importing from Amazon is not available yet: its report has to be analyzed "
        "and an importer written for its layout first."
    )


def test_k13_a_channel_with_no_importer_takes_no_files(
    admin_client, dresses, mini_report
):
    """K13 (-). Files sent for Amazon, which has no importer, are refused
    with the same reason, and nothing is stored."""
    SalesChannel.objects.create(org=dresses.org, name="Amazon", code="amazon")
    response = _start(admin_client, mini_report, source="amazon")
    assert response.status_code == 400
    assert "Importing from Amazon is not available yet" in response.json()["detail"]
    assert not ImportRun.objects.filter(org=dresses.org).exists()


def test_k14_one_run_at_a_time_unless_the_other_was_lost(
    admin_client, org_a, mini_report, worker
):
    """K14 (-). While a run is queued another is refused (409). A run left
    queued for more than two hours (its worker died) no longer blocks."""
    first = _queued(worker, admin_client, mini_report)
    response = _start(admin_client, mini_report)
    assert response.status_code == 409
    assert "Another import is still running" in response.json()["detail"]
    ImportRun.objects.filter(pk=first["id"]).update(
        created_at=timezone.now() - timedelta(hours=3)
    )
    assert _start(admin_client, mini_report).status_code == 202


def test_k15_the_report_is_emailed_when_asked(admin_client, org_a, mini_report, worker):
    """K15 (+). "Email me the report" sends it to whoever started the run
    when it finishes: what was created, the category with no products, how
    many rows were rejected, and a link to the whole report."""
    started = _queued(worker, admin_client, mini_report, dry_run=False, notify=True)
    worker.run()
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == "Catalog import finished: Mercado Libre"
    assert message.to == ["admin@test.com"]
    assert "New: 4 products, 8 variants" in message.body
    assert "Sudaderas y hoodies: all 1 of its rows were rejected." in message.body
    assert "Rejected rows: 6" in message.body
    assert f"/invoices/catalog/import/{started['id']}" in message.body
    assert ImportRun.objects.get(pk=started["id"]).emailed_at is not None


def test_k16_a_run_longer_than_a_minute_is_emailed_anyway(
    admin_client, org_a, mini_report, worker, monkeypatch
):
    """K16 (+). Nobody asked, but the run took longer than a minute: the
    report is emailed all the same."""
    monkeypatch.setattr("catalog.imports.EMAIL_AFTER", timedelta(0))
    _queued(worker, admin_client, mini_report, dry_run=True, notify=False)
    worker.run()
    assert [m.subject for m in mail.outbox] == [
        "Catalog preview finished: Mercado Libre"
    ]
    assert "This was a preview: nothing was saved." in mail.outbox[0].body


def test_k17_a_quick_run_nobody_asked_about_sends_nothing(
    admin_client, org_a, mini_report, worker
):
    """K17 (-). A run under a minute, with no email asked for, sends none."""
    _queued(worker, admin_client, mini_report, notify=False)
    worker.run()
    assert mail.outbox == []


def test_k18_a_failed_runs_email_says_why(admin_client, org_a, mini_report, worker):
    """K18 (-). A run that fails is emailed, when asked, with its reason and
    that nothing was saved."""
    _queued(
        worker,
        admin_client,
        mini_report,
        dry_run=False,
        notify=True,
        characteristics=_upload(mini_report.prices),
    )
    worker.run()
    assert mail.outbox[0].subject == "Catalog import failed: Mercado Libre"
    assert "It failed: The characteristics file has no category sheets." in (
        mail.outbox[0].body
    )
    assert "Nothing was saved." in mail.outbox[0].body


def test_k19_a_previews_files_are_forgotten_after_a_day(
    admin_client, org_a, mini_report, worker
):
    """K19 (-). A day after a preview its files are dropped (when the next run
    starts), and importing that preview is refused (409)."""
    preview = _queued(worker, admin_client, mini_report)
    worker.run()
    ImportRun.objects.filter(pk=preview["id"]).update(
        created_at=timezone.now() - timedelta(days=2)
    )
    _queued(worker, admin_client, mini_report)
    worker.run()
    assert not ImportFile.objects.filter(run_id=preview["id"]).exists()
    response = admin_client.post(f"{RUNS}{preview['id']}/import/", {}, format="json")
    assert response.status_code == 409
    assert "no longer kept" in response.json()["detail"]


def test_k20_the_task_runs_a_queued_run_once(admin_client, org_a, mini_report, worker):
    """K20 (+). The task carries out a queued run and nothing else: run again,
    or for a finished run, it changes nothing."""
    started = _queued(worker, admin_client, mini_report, dry_run=False)
    worker.run()
    finished = ImportRun.objects.get(pk=started["id"]).finished_at
    run_catalog_import(started["id"], str(org_a.id))
    assert ImportRun.objects.get(pk=started["id"]).finished_at == finished
    assert ImportRun.objects.filter(org=org_a).count() == 1


def test_k21_an_email_that_cannot_be_sent_leaves_the_run_done(
    admin_client, org_a, mini_report, worker, monkeypatch
):
    """K21 (-). The mail server refuses: the run is still done, with its
    report, and is not marked as emailed."""

    def refuse(*args, **kwargs):
        raise ConnectionRefusedError

    monkeypatch.setattr("catalog.imports.EmailMessage.send", refuse)
    started = _queued(worker, admin_client, mini_report, notify=True)
    worker.run()
    run = ImportRun.objects.get(pk=started["id"])
    assert (run.status, run.emailed_at) == (ImportRun.DONE, None)
    assert run.report["created"]["variants"] == 8


# =============================================================================
# L. Sales channels: online channels and physical stores
# =============================================================================

CHANNELS = "/api/catalog/channels/"
STORE = {
    "name": "Tienda Centro",
    "code": "tienda-centro",
    "kind": "STORE",
    "address_line": "Av. Juárez 12",
    "city": "Puebla",
    "state": "Puebla",
    "postcode": "72000",
    "country": "MX",
    "phone": "+52 222 123 4567",
}


def test_l1_online_channels_and_physical_stores_are_created(admin_client, dresses):
    """L1 (+). A physical store is created with its address and phone, next
    to the online Mercado Libre; the list gives each its kind."""
    response = admin_client.post(CHANNELS, STORE, format="json")
    assert response.status_code == 201, response.content
    store = response.json()
    assert (store["kind"], store["city"], store["phone"], store["site"]) == (
        "STORE",
        "Puebla",
        "+52 222 123 4567",
        "",
    )
    kinds = {c["code"]: c["kind"] for c in admin_client.get(CHANNELS).json()["results"]}
    assert kinds == {"mercadolibre": "ONLINE", "tienda-centro": "STORE"}


def test_l2_a_physical_store_has_no_market_id(admin_client, dresses):
    """L2 (-). A store with a market id is refused, on create and on edit."""
    response = admin_client.post(CHANNELS, {**STORE, "site": "MLM"}, format="json")
    assert (response.status_code, response.json()["site"]) == (
        400,
        ["A physical store has no market id."],
    )
    store = admin_client.post(CHANNELS, STORE, format="json").json()
    response = admin_client.patch(
        f"{CHANNELS}{store['id']}/", {"site": "MLM"}, format="json"
    )
    assert response.status_code == 400
    assert SalesChannel.objects.get(pk=store["id"]).site == ""


def test_l3_an_online_channel_has_no_address(admin_client, dresses):
    """L3 (-). An online channel with an address or phone is refused."""
    for field in ("address_line", "phone"):
        response = admin_client.post(
            CHANNELS,
            {"name": "Amazon", "code": "amazon", field: STORE[field]},
            format="json",
        )
        assert (response.status_code, response.json()[field]) == (
            400,
            ["Only a physical store has an address or phone."],
        ), field
    assert not SalesChannel.objects.filter(code="amazon").exists()


def test_l4_a_channel_with_listings_stays_online(admin_client, dresses):
    """L4 (-). Mercado Libre has a listing, so it cannot become a store."""
    response = admin_client.patch(
        f"{CHANNELS}{dresses.channel.id}/", {"kind": "STORE"}, format="json"
    )
    assert response.status_code == 400
    assert "stays online" in response.json()["kind"][0]
    dresses.channel.refresh_from_db()
    assert dresses.channel.kind == SalesChannel.ONLINE


def test_l5_a_physical_store_has_no_listings(admin_client, dresses):
    """L5 (-). A listing on a store is refused: a store sells the whole
    catalog without publishing it."""
    store = admin_client.post(CHANNELS, STORE, format="json").json()
    response = admin_client.post(
        "/api/catalog/listings/",
        {
            "channel": store["id"],
            "product": str(dresses.product.id),
            "external_id": "T-1",
            "title": "Vestido",
        },
        format="json",
    )
    assert response.status_code == 400
    assert "A physical store has no listings" in response.json()["channel"][0]
    assert not Listing.objects.filter(channel_id=store["id"]).exists()


def test_l6_a_channel_with_listings_or_sales_is_not_deleted(
    admin_client, dresses, customer
):
    """L6 (-). Mercado Libre has a listing, an invoice and an order: deleting
    it is a 409 naming all three, and it stays."""
    Invoice.objects.create(
        org=dresses.org,
        invoice_title="Venta ML",
        account=customer.account,
        sales_channel=dresses.channel,
    )
    Order.objects.create(
        org=dresses.org,
        name="Pedido ML",
        account=customer.account,
        sales_channel=dresses.channel,
    )
    response = admin_client.delete(f"{CHANNELS}{dresses.channel.id}/")
    assert response.status_code == 409
    detail = response.json()["detail"]
    for what in ("1 listing", "1 invoice", "1 order", "Switch it off instead"):
        assert what in detail, what
    assert SalesChannel.objects.filter(pk=dresses.channel.pk).exists()


def test_l7_a_store_with_no_sales_is_deleted(admin_client, dresses):
    """L7 (+). A store nothing was sold through is deleted."""
    store = admin_client.post(CHANNELS, STORE, format="json").json()
    assert admin_client.delete(f"{CHANNELS}{store['id']}/").status_code == 204
    assert not SalesChannel.objects.filter(pk=store["id"]).exists()


def _sold_through(channel, customer, variant):
    payload = _invoice(customer.account, customer.contact, variant, str(variant.price))
    return {**payload, "sales_channel_id": str(channel.id)}


def test_l8_an_invoice_records_its_channel_and_keeps_its_customer(
    admin_client, dresses, customer
):
    """L8 (+). An invoice sold through Mercado Libre records the channel; its
    account is still the customer. The detail and the list show the channel,
    and the list filters by it."""
    payload = _sold_through(dresses.channel, customer, dresses.newborn)
    response = admin_client.post("/api/invoices/", payload, format="json")
    assert response.status_code == 201, response.content
    invoice = Invoice.objects.get(invoice_title="Venta boutique")
    assert (invoice.sales_channel, invoice.account) == (
        dresses.channel,
        customer.account,
    )
    detail = admin_client.get(f"/api/invoices/{invoice.id}/").json()
    shown = detail["invoice"]["sales_channel"]
    assert shown == {
        "id": str(dresses.channel.id),
        "name": "Mercado Libre",
        "kind": "ONLINE",
    }
    listed = admin_client.get(f"/api/invoices/?sales_channel={dresses.channel.id}")
    assert [i["id"] for i in listed.json()["results"]] == [str(invoice.id)]
    other = SalesChannel.objects.create(org=dresses.org, name="Amazon", code="amazon")
    assert (
        admin_client.get(f"/api/invoices/?sales_channel={other.id}").json()["results"]
        == []
    )


def test_l9_an_invoice_cannot_record_another_orgs_channel(
    admin_client, org_b, dresses, customer
):
    """L9 (-). Org B's channel in org A's invoice is a 400, like an id that
    does not exist; no invoice is created."""
    with rls_org(org_b):
        theirs = build_dresses(org_b)
    payload = _sold_through(theirs.channel, customer, dresses.newborn)
    response = admin_client.post("/api/invoices/", payload, format="json")
    assert response.status_code == 400
    assert "sales_channel_id" in response.json()["errors"]
    assert not Invoice.objects.filter(org=dresses.org).exists()


def test_l10_a_switched_off_channel_takes_no_new_sales(admin_client, dresses, customer):
    """L10 (-). Mercado Libre switched off: a new invoice naming it is a 400."""
    dresses.channel.is_active = False
    dresses.channel.save(update_fields=["is_active"])
    payload = _sold_through(dresses.channel, customer, dresses.newborn)
    response = admin_client.post("/api/invoices/", payload, format="json")
    assert response.status_code == 400
    assert response.json()["errors"]["sales_channel_id"] == [
        "That sales channel is switched off."
    ]


def test_l11_an_invoice_keeps_a_channel_switched_off_later(
    admin_client, dresses, customer
):
    """L11 (+). An invoice sold through Mercado Libre before it was switched
    off keeps it, and is still edited with it."""
    payload = _sold_through(dresses.channel, customer, dresses.newborn)
    admin_client.post("/api/invoices/", payload, format="json")
    invoice = Invoice.objects.get(invoice_title="Venta boutique")
    dresses.channel.is_active = False
    dresses.channel.save(update_fields=["is_active"])
    response = admin_client.put(
        f"/api/invoices/{invoice.id}/",
        {
            "invoice_title": "Venta boutique 2",
            "sales_channel_id": str(dresses.channel.id),
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    invoice.refresh_from_db()
    assert (invoice.invoice_title, invoice.sales_channel) == (
        "Venta boutique 2",
        dresses.channel,
    )


def test_l12_every_channel_sells_the_whole_catalog_at_one_price(
    admin_client, dresses, customer
):
    """L12 (+). The RN dress, published only on Mercado Libre, is sold on an
    invoice of a physical store too, at its one price (499)."""
    store = SalesChannel.objects.create(
        org=dresses.org, name="Tienda Centro", code="centro", kind=SalesChannel.STORE
    )
    for channel in (dresses.channel, store):
        payload = _sold_through(channel, customer, dresses.newborn)
        assert (
            admin_client.post("/api/invoices/", payload, format="json").status_code
            == 201
        )
    lines = InvoiceLineItem.objects.filter(product=dresses.newborn)
    assert sorted(
        (line.invoice.sales_channel.kind, line.unit_price) for line in lines
    ) == [("ONLINE", Decimal("499.00")), ("STORE", Decimal("499.00"))]


def test_l13_an_order_records_its_channel(dresses, customer):
    """L13 (+). An order taken in a physical store records that store; the
    store cannot then be deleted out from under it."""
    store = SalesChannel.objects.create(
        org=dresses.org, name="Tienda Centro", code="centro", kind=SalesChannel.STORE
    )
    order = Order.objects.create(
        org=dresses.org, name="Pedido", account=customer.account, sales_channel=store
    )
    assert list(store.orders.all()) == [order]
    with pytest.raises(ProtectedError), transaction.atomic():
        store.delete()


def _duplicate_sold_through(admin_client, dresses, customer, *, switched_off):
    payload = _sold_through(dresses.channel, customer, dresses.newborn)
    admin_client.post("/api/invoices/", payload, format="json")
    original = Invoice.objects.get(invoice_title="Venta boutique")
    if switched_off:
        dresses.channel.is_active = False
        dresses.channel.save(update_fields=["is_active"])
    response = admin_client.post(f"/api/invoices/{original.id}/duplicate/")
    assert response.status_code in (200, 201), response.content
    return Invoice.objects.get(invoice_title="Copy of Venta boutique")


def test_l14_a_duplicated_invoice_keeps_its_channel(admin_client, dresses, customer):
    """L14 (+). Duplicating an invoice sold through Mercado Libre gives a
    draft sold through it too."""
    copy = _duplicate_sold_through(admin_client, dresses, customer, switched_off=False)
    assert copy.sales_channel == dresses.channel


def test_l15_a_duplicate_takes_no_switched_off_channel(admin_client, dresses, customer):
    """L15 (-). Once Mercado Libre is switched off, a duplicate is a new sale
    it cannot take: the draft has no channel. The original keeps it."""
    copy = _duplicate_sold_through(admin_client, dresses, customer, switched_off=True)
    assert copy.sales_channel is None
    original = Invoice.objects.get(invoice_title="Venta boutique")
    assert original.sales_channel == dresses.channel


def test_l16_with_invoices_off_a_channel_they_record_is_kept(
    admin_client, dresses, customer
):
    """L16 (-). Invoices is switched off, so its invoices are out of sight.
    Deleting a store an invoice records answers 409 naming the module, as
    deleting an account does, and the store stays."""
    store = SalesChannel.objects.create(
        org=dresses.org, name="Tienda Centro", code="centro", kind=SalesChannel.STORE
    )
    Invoice.objects.create(
        org=dresses.org,
        invoice_title="Venta",
        account=customer.account,
        sales_channel=store,
    )
    set_modules_off(dresses.org, "invoices")
    response = admin_client.delete(f"{CHANNELS}{store.id}/")
    assert response.status_code == 409
    assert response.json()["code"] == "module_off"
    assert "Tienda Centro has 1 invoice." in response.json()["errors"]
    assert SalesChannel.objects.filter(pk=store.pk).exists()
