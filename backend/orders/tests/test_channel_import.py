"""Online channel sales import: Mercado Libre reports into orders, products,
stock and work orders.

Reports are built in memory in Mercado Libre's own layout (preamble, section
row, headers with the repeated "Estado", pack rows), so each test states the
sales it imports. The last test reads the real sample report when present.

Run with: pytest orders/tests/test_channel_import.py --no-cov
"""

import datetime
import hashlib
import importlib
import io
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.utils import timezone

from accounts.models import Account
from business_hours.models import BusinessCalendar, BusinessHoliday
from common.models import Comment, Org, Profile, User
from invoices.models import Product
from orders import inventory
from orders.channel_import import (
    ACTION_REQUIRED,
    CHANGE,
    NOT_IMPORTED,
    WARNING,
    import_channel_report,
    record_import,
    sync_channel_orders,
)
from orders.channels.base import ChannelImportError, ChannelLine, ChannelOrder
from orders.channels.mercadolibre import classify_status
from orders.models import (
    DEFAULT_DAY_COUNT,
    ChannelListing,
    ChannelPreference,
    ImportRun,
    Order,
    OrderLineItem,
    ReturnInspection,
    WorkOrder,
)

SAMPLE_REPORT = (
    Path(__file__).resolve().parents[1] / "ML Sales Info" / "full sales.xlsx"
)

# (section, header) in report order. Only the section's first column carries
# its name, as in the real export.
COLUMNS = [
    ("Ventas", "# de venta"),
    ("", "Fecha de venta"),
    ("", "Estado"),
    ("", "Descripción del estado"),
    ("", "Unidades"),
    ("", "Ingresos por productos (MXN)"),
    ("", "Ingresos por envío (MXN)"),
    ("", "Total (MXN)"),
    ("Publicaciones", "SKU"),
    ("", "# de publicación"),
    ("", "Título de la publicación"),
    ("", "Variante"),
    ("", "Precio unitario de venta de la publicación (MXN)"),
    ("Compradores", "Comprador"),
    ("", "Domicilio"),
    ("", "Municipio/Alcaldía"),
    ("", "Estado"),
    ("", "Código postal"),
    ("", "País"),
    ("Envíos", "Fecha en camino"),
]
SALE_DATE = "15 de noviembre de 2026 10:40 hs."


def sale(
    sale_id,
    status="Etiqueta impresa",
    listing="MLM100",
    variant="Talla : 3-6 M",
    units=1,
    price=400,
    sku=" ",
    date=SALE_DATE,
    shipped=" ",
):
    """One row of a single-product sale. `price=""` leaves the price empty."""
    priced = isinstance(price, (int, float))
    return [
        sale_id,
        date,
        status,
        "Detalle",
        units,
        price * units if priced else "",
        "",
        price * units * 0.7 if priced else "",
        sku,
        listing,
        f"Producto {listing}",
        variant,
        price,
        "Ana Compradora",
        "Calle 1",
        "Guadalajara",
        "Jalisco",
        "44100",
        "Mexico",
        shipped,
    ]


def pack(sale_id, *items, date=SALE_DATE):
    """A multi-product sale: its header row, then one row per product with
    the buyer columns blank, as the report has them."""
    head = [
        sale_id,
        date,
        f"Paquete de {len(items)} productos",
        " ",
        "",
        sum(row[5] for row in items if row[5] != ""),
        "",
        "",
        " ",
        " ",
        " ",
        " ",
        "",
        "Pedro Paquete",
        "Calle 2",
        "Monterrey",
        "Nuevo León",
        "64000",
        "Mexico",
        " ",
    ]
    children = []
    for row in items:
        child = list(row)
        child[1] = date
        child[5] = ""
        child[13:19] = [" "] * 6
        children.append(child)
    return [head, *children]


def report(*rows):
    """An .xlsx in Mercado Libre's layout holding `rows` (lists, or the lists
    `pack` returns)."""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append([None])
    sheet.append(["En este reporte encontrarás la información de tus ventas."])
    sheet.append([None, None, None, "Ir a Facturas y reportes de tus cargos"])
    sheet.append(["Ventas  Estado de tus ventas al 17 de noviembre de 2026."])
    sheet.append([section or None for section, _ in COLUMNS])
    sheet.append([header for _, header in COLUMNS])
    for row in rows:
        if row and isinstance(row[0], list):
            for inner in row:
                sheet.append(inner)
        else:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def run(org, *rows):
    return import_channel_report(org, "MERCADOLIBRE", "ventas.xlsx", report(*rows))


def item(org, external_id):
    return OrderLineItem.objects.get(org=org, external_id=external_id)


def work(org, sale=None):
    """{kind: (quantity, status)} of a sale's work orders (the org's, when it
    has one sale), for sales of a single product."""
    orders = WorkOrder.objects.filter(org=org)
    if sale:
        orders = orders.filter(order__external_id=sale)
    found = [(wo.kind, (wo.quantity, wo.status)) for wo in orders]
    assert len({kind for kind, _ in found}) == len(found), found
    return dict(found)


def preparations(org, sale, status="PENDING"):
    """{what: quantity} of a sale's preparation orders in `status`."""
    return {
        str(wo.product or wo.line_item): wo.quantity
        for wo in WorkOrder.objects.filter(
            org=org, order__external_id=sale, kind="PREPARATION", status=status
        )
    }


def packaging(org, sale):
    """[(quantity, status)] of a sale's packaging orders."""
    return [
        (wo.quantity, wo.status)
        for wo in WorkOrder.objects.filter(
            org=org, order__external_id=sale, kind="PACKAGING"
        )
    ]


def listing_product(org, listing="MLM100", variant="Talla : 3-6 M", **fields):
    """A catalog product the listing is already mapped to."""
    fields.setdefault("name", "Mapped")
    product = Product.objects.create(org=org, price=400, **fields)
    ChannelListing.objects.create(
        org=org,
        channel="MERCADOLIBRE",
        listing_id=listing,
        variant=variant,
        product=product,
    )
    return product


# ---------------------------------------------------------------------------
# 1. New orders, linked to products
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestImportNewOrders:
    def test_single_and_pack_sales_become_orders_with_items(self, org_a):
        result = run(
            org_a,
            sale("S1", listing="MLM1", units=2, price=300),
            pack(
                "P1",
                sale("P1-A", listing="MLM2", variant="Color : Rojo"),
                sale("P1-B", listing="MLM3", variant=" "),
            ),
        )

        assert result.orders_created == 2
        assert result.items_created == 3
        single = Order.objects.get(org=org_a, external_id="S1")
        assert single.channel == "MERCADOLIBRE"
        assert single.name == "Mercado Libre S1"
        assert single.order_date == datetime.date(2026, 11, 15)
        assert single.currency == "MXN"
        assert single.buyer_name == "Ana Compradora"
        # "Estado" is both the sale status and the buyer's state.
        assert single.channel_status == "Etiqueta impresa"
        assert single.shipping_state == "Jalisco"
        assert single.shipping_country == "MX"
        assert single.account.name == "Mercado Libre"
        line = single.line_items.get()
        assert (line.external_id, line.quantity, line.unit_price, line.total) == (
            "S1",
            Decimal("2.00"),
            Decimal("300.00"),
            Decimal("600.00"),
        )
        assert single.subtotal == Decimal("600.00")

        packed = Order.objects.get(org=org_a, external_id="P1")
        assert packed.buyer_name == "Pedro Paquete"
        assert [x.external_id for x in packed.line_items.all()] == ["P1-A", "P1-B"]
        assert packed.total_amount == Decimal("800.00")

    def test_items_link_to_catalog_products_through_listings(self, org_a):
        result = run(
            org_a,
            sale("S1", listing="MLM1", variant="Talla : 1"),
            sale("S2", listing="MLM1", variant="Talla : 1"),
            sale("S3", listing="MLM1", variant="Talla : 2"),
        )

        one, again, other = (item(org_a, s) for s in ("S1", "S2", "S3"))
        assert one.product is not None
        assert again.product == one.product
        assert other.product not in (None, one.product)
        assert one.listing.listing_id == "MLM1"
        assert one.product.name == "Producto MLM1 (Talla : 1)"
        assert one.product.currency == "MXN"
        assert result.products_created == 2
        assert result.listings_created == 2

    def test_existing_product_is_matched_by_sku(self, org_a):
        product = Product.objects.create(org=org_a, name="Own", sku="SKU-9", price=1)

        result = run(org_a, sale("S1", sku="SKU-9"))

        assert item(org_a, "S1").product == product
        assert ChannelListing.objects.get(org=org_a).product == product
        assert result.products_created == 0

    def test_mapped_listing_wins_over_a_new_product(self, org_a):
        product = listing_product(org_a)

        result = run(org_a, sale("S1"))

        assert item(org_a, "S1").product == product
        assert result.products_created == 0

    def test_sku_is_only_matched_inside_the_org(self, org_a, org_b):
        Product.objects.create(org=org_b, name="Other org", sku="SKU-9", price=1)

        run(org_a, sale("S1", sku="SKU-9"))

        assert item(org_a, "S1").product.org == org_a

    def test_due_date_adds_the_catalog_availability_time(self, org_a):
        listing_product(org_a, listing="MLM1", variant="", availability_days=5)

        run(org_a, pack("P1", sale("A", listing="MLM1", variant=""), sale("B")))

        assert item(org_a, "A").due_date == datetime.date(2026, 11, 20)
        # No availability on record reads as 0: ready on the sale date.
        assert item(org_a, "B").due_date == datetime.date(2026, 11, 15)
        assert Order.objects.get(org=org_a).due_date == datetime.date(2026, 11, 20)

    @pytest.mark.parametrize(
        ("status", "fulfillment", "order_status"),
        [
            ("Etiqueta impresa", "PENDING", "ACTIVATED"),
            ("Entregado", "DELIVERED", "COMPLETED"),
            ("Cancelada por el comprador", "CANCELLED", "CANCELLED"),
            (
                "Devolución finalizada con reembolso al comprador",
                "RETURNED",
                "CANCELLED",
            ),
        ],
    )
    def test_channel_status_sets_order_status(
        self, org_a, status, fulfillment, order_status
    ):
        run(org_a, sale("S1", status=status))

        order = Order.objects.get(org=org_a)
        assert order.fulfillment_status == fulfillment
        assert order.status == order_status

    def test_shipped_date_takes_the_year_after_the_sale(self, org_a):
        run(
            org_a,
            sale(
                "S1",
                status="En camino",
                date="30 de diciembre de 2026 10:00 hs.",
                shipped="2 de enero | 10:15",
            ),
        )

        order = Order.objects.get(org=org_a)
        assert order.fulfillment_status == "SHIPPED"
        assert order.shipped_date == datetime.date(2027, 1, 2)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Esperando disponibilidad de stock", "PENDING"),
        ("Etiqueta impresa", "PENDING"),
        ("Entregado", "DELIVERED"),
        ("Venta entregada", "DELIVERED"),
        ("Mediación finalizada. Te dimos el dinero.", "DELIVERED"),
        ("Cancelaste la venta", "CANCELLED"),
        ("Devolución entregada", "RETURNED"),
        ("Te dimos el dinero del producto que te devolvieron", "RETURNED"),
        ("Reclamo cerrado con reembolso al comprador", "RETURNED"),
        ("Paquete no entregado", "RETURNED"),
        ("Devuelto el 13 de noviembre", "RETURNED"),
        ("Algo nuevo", None),
        ("", None),
    ],
)
def test_mercadolibre_status_classification(text, expected):
    assert classify_status(text) == expected


# ---------------------------------------------------------------------------
# 2. Re-import: update, never duplicate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReimport:
    ROWS = (
        sale("S1"),
        pack("P1", sale("P1-A", listing="MLM2"), sale("P1-B", listing="MLM3")),
    )

    def _counts(self, org):
        return [
            model.objects.filter(org=org).count()
            for model in (Order, OrderLineItem, Product, ChannelListing, WorkOrder)
        ] + [Account.objects.filter(org=org).count()]

    def test_same_report_twice_creates_nothing_new(self, org_a):
        run(org_a, *self.ROWS)
        before = self._counts(org_a)

        result = run(org_a, *self.ROWS)

        assert self._counts(org_a) == before
        assert result.orders_created == 0
        assert result.orders_updated == 2
        assert result.items_created == 0
        assert result.preparation_orders == result.packaging_orders == 0

    def test_same_report_twice_takes_stock_once(self, org_a):
        product = listing_product(org_a, stock_quantity=5)

        run(org_a, sale("S1", units=2))
        run(org_a, sale("S1", units=2))

        product.refresh_from_db()
        assert product.stock_quantity == Decimal("3.00")

    def test_status_change_updates_the_order(self, org_a):
        run(org_a, sale("S1"))

        run(org_a, sale("S1", status="En camino", shipped="16 de noviembre | 09:00"))
        order = Order.objects.get(org=org_a)
        assert (order.fulfillment_status, order.status) == ("SHIPPED", "ACTIVATED")
        assert order.shipped_date == datetime.date(2026, 11, 16)

        run(org_a, sale("S1", status="Entregado"))
        order.refresh_from_db()
        assert (order.fulfillment_status, order.status) == ("DELIVERED", "COMPLETED")
        assert order.channel_status == "Entregado"
        assert Order.objects.filter(org=org_a).count() == 1

    def test_shipping_closes_open_work_orders(self, org_a):
        listing_product(org_a, stock_quantity=1)
        run(org_a, sale("S1", units=3))
        assert work(org_a) == {
            "PREPARATION": (2, "PENDING"),
            "PACKAGING": (3, "PENDING"),
        }

        run(org_a, sale("S1", units=3, status="Entregado"))

        assert work(org_a) == {"PREPARATION": (2, "DONE"), "PACKAGING": (3, "DONE")}
        # One from stock plus two prepared: the whole quantity went out.
        assert item(org_a, "S1").stock_allocated == Decimal("3.00")

    def test_cancellation_returns_stock_and_cancels_work(self, org_a):
        product = listing_product(org_a, stock_quantity=1)
        run(org_a, sale("S1", units=3))
        product.refresh_from_db()
        assert product.stock_quantity == 0

        result = run(org_a, sale("S1", units=3, status="Cancelada por el comprador"))

        product.refresh_from_db()
        assert product.stock_quantity == Decimal("1.00")
        assert result.stock_released == Decimal("1.00")
        assert work(org_a) == {
            "PREPARATION": (2, "CANCELLED"),
            "PACKAGING": (3, "CANCELLED"),
        }
        line = item(org_a, "S1")
        assert (line.stock_allocated, line.fulfillment_planned) == (0, False)
        assert Order.objects.get(org=org_a).status == "CANCELLED"

    def test_prepared_then_cancelled_before_packaging_restocks_and_cancels(self, org_a):
        product = listing_product(org_a, stock_quantity=0)
        run(org_a, sale("S1", units=2))
        preparation = WorkOrder.objects.get(org=org_a, kind="PREPARATION")
        assert inventory.complete_work_order(preparation)
        # The two units made are set aside for the sale, not left on the shelf.
        product.refresh_from_db()
        assert product.stock_quantity == 0
        assert item(org_a, "S1").stock_allocated == Decimal("2.00")

        result = run(org_a, sale("S1", units=2, status="Cancelaste la venta"))

        product.refresh_from_db()
        assert product.stock_quantity == Decimal("2.00")
        assert result.stock_released == Decimal("2.00")
        assert work(org_a) == {
            "PREPARATION": (2, "DONE"),
            "PACKAGING": (2, "CANCELLED"),
        }

    def test_partly_stocked_prepared_then_cancelled_restocks_everything(self, org_a):
        product = listing_product(org_a, stock_quantity=2)
        run(org_a, sale("S1", units=5))
        product.refresh_from_db()
        assert product.stock_quantity == 0
        inventory.complete_work_order(
            WorkOrder.objects.get(org=org_a, kind="PREPARATION")
        )

        run(org_a, sale("S1", units=5, status="Cancelada por el comprador"))

        # Two taken from stock and three prepared all go back on the shelf.
        product.refresh_from_db()
        assert product.stock_quantity == Decimal("5.00")
        assert work(org_a) == {
            "PREPARATION": (3, "DONE"),
            "PACKAGING": (5, "CANCELLED"),
        }

    def test_cancelled_before_anything_was_done_cancels_all_work(self, org_a):
        product = listing_product(org_a, stock_quantity=1)
        run(org_a, sale("S1", units=3))

        run(org_a, sale("S1", units=3, status="Cancelada por el comprador"))

        product.refresh_from_db()
        assert product.stock_quantity == Decimal("1.00")
        assert work(org_a) == {
            "PREPARATION": (2, "CANCELLED"),
            "PACKAGING": (3, "CANCELLED"),
        }

    def test_cancelling_one_item_of_a_pack_keeps_the_order_open(self, org_a):
        product = listing_product(org_a, listing="MLM2", variant="", stock_quantity=4)
        rows = lambda a_status: pack(  # noqa: E731
            "P1",
            sale("P1-A", listing="MLM2", variant="", status=a_status),
            sale("P1-B", listing="MLM3"),
        )
        run(org_a, rows("Etiqueta impresa"))

        run(org_a, rows("Cancelada por el comprador"))

        product.refresh_from_db()
        assert product.stock_quantity == Decimal("4.00")
        order = Order.objects.get(org=org_a)
        assert order.fulfillment_status == "PENDING"
        assert item(org_a, "P1-A").fulfillment_status == "CANCELLED"
        # The packaging shrinks to the item still to be shipped.
        assert packaging(org_a, "P1") == [(1, "PENDING")]

    def test_quantity_change_before_shipping_plans_again(self, org_a):
        product = listing_product(org_a, stock_quantity=5)
        run(org_a, sale("S1", units=1))

        run(org_a, sale("S1", units=4))

        product.refresh_from_db()
        assert product.stock_quantity == Decimal("1.00")
        assert item(org_a, "S1").stock_allocated == Decimal("4.00")

        run(org_a, sale("S1", units=7))

        product.refresh_from_db()
        assert product.stock_quantity == 0
        # Only the last plan's work is open; 5 on hand, 2 to prepare.
        open_work = WorkOrder.objects.filter(org=org_a, status="PENDING")
        assert {(wo.kind, wo.quantity) for wo in open_work} == {
            ("PACKAGING", 7),
            ("PREPARATION", 2),
        }

    def test_remapped_listing_moves_open_items_stock(self, org_a):
        first = listing_product(org_a, stock_quantity=2)
        run(org_a, sale("S1", units=2))
        second = Product.objects.create(
            org=org_a, name="Real", price=1, stock_quantity=5
        )
        ChannelListing.objects.filter(org=org_a).update(product=second)

        run(org_a, sale("S1", units=2))

        first.refresh_from_db()
        second.refresh_from_db()
        assert (first.stock_quantity, second.stock_quantity) == (2, 3)
        assert item(org_a, "S1").product == second

    def test_remapping_after_shipping_keeps_the_items_product(self, org_a):
        first = listing_product(org_a, stock_quantity=2)
        run(org_a, sale("S1"))
        run(org_a, sale("S1", status="En camino"))
        second = Product.objects.create(org=org_a, name="Real", price=1)
        ChannelListing.objects.filter(org=org_a).update(product=second)

        run(org_a, sale("S1", status="En camino"))
        assert item(org_a, "S1").product == first
        run(org_a, sale("S1", status="Cancelada por el comprador"))
        inspection = ReturnInspection.objects.get(org=org_a)
        assert inspection.product == first
        inventory.accept_return(inspection, None)

        first.refresh_from_db()
        second.refresh_from_db()
        assert (first.stock_quantity, second.stock_quantity) == (2, None)

    def test_refund_before_shipping_releases(self, org_a):
        product = listing_product(org_a, stock_quantity=2)
        run(org_a, sale("S1"))

        run(org_a, sale("S1", status="Le reembolsamos el dinero al comprador"))

        product.refresh_from_db()
        assert product.stock_quantity == Decimal("2.00")
        assert not ReturnInspection.objects.exists()

    def test_return_after_delivery_does_not_restock(self, org_a):
        product = listing_product(org_a, stock_quantity=2)
        run(org_a, sale("S1"))
        run(org_a, sale("S1", status="Entregado"))

        result = run(org_a, sale("S1", status="Devolución finalizada"))

        product.refresh_from_db()
        assert product.stock_quantity == Decimal("1.00")
        assert Order.objects.get(org=org_a).fulfillment_status == "RETURNED"
        assert result.returns_to_inspect == 1
        assert ReturnInspection.objects.get(org=org_a).status == "PENDING"

    def test_unknown_status_keeps_the_previous_one(self, org_a):
        run(org_a, sale("S1", status="Entregado"))

        result = run(org_a, sale("S1", status="Estado desconocido"))

        assert item(org_a, "S1").fulfillment_status == "DELIVERED"
        assert any("not recognised" in w for w in result.warnings)

    def test_same_sale_id_in_another_org_is_another_order(self, org_a, org_b):
        run(org_a, sale("S1"))

        result = run(org_b, sale("S1"))

        assert result.orders_created == 1
        assert Order.objects.filter(external_id="S1").count() == 2
        assert item(org_b, "S1").product.org == org_b


# ---------------------------------------------------------------------------
# 3. Stock and preparation / packaging work orders
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStockAndWorkOrders:
    def test_no_stock_information_means_zero(self, org_a):
        product = listing_product(org_a, availability_days=3)

        result = run(org_a, sale("S1", units=2))

        assert work(org_a) == {
            "PREPARATION": (2, "PENDING"),
            "PACKAGING": (2, "PENDING"),
        }
        preparation = WorkOrder.objects.get(org=org_a, kind="PREPARATION")
        assert preparation.product == product
        assert preparation.line_item is None
        assert preparation.due_date == datetime.date(2026, 11, 18)
        assert (result.preparation_orders, result.packaging_orders) == (1, 1)
        product.refresh_from_db()
        assert product.stock_quantity is None

    def test_zero_stock_prepares_and_packages(self, org_a):
        product = listing_product(org_a, stock_quantity=0)

        run(org_a, sale("S1"))

        assert work(org_a) == {
            "PREPARATION": (1, "PENDING"),
            "PACKAGING": (1, "PENDING"),
        }
        product.refresh_from_db()
        assert product.stock_quantity == 0

    def test_stock_short_of_the_quantity_packages_and_prepares_the_rest(self, org_a):
        product = listing_product(org_a, stock_quantity=2)

        result = run(org_a, sale("S1", units=5))

        assert work(org_a) == {
            "PACKAGING": (5, "PENDING"),
            "PREPARATION": (3, "PENDING"),
        }
        assert item(org_a, "S1").stock_allocated == Decimal("2.00")
        product.refresh_from_db()
        assert product.stock_quantity == 0
        assert (result.packaging_orders, result.preparation_orders) == (1, 1)
        assert result.stock_allocated == 2

    def test_enough_stock_is_taken_and_packaged(self, org_a):
        product = listing_product(org_a, stock_quantity=10)

        run(org_a, sale("S1", units=4))

        assert work(org_a) == {"PACKAGING": (4, "PENDING")}
        product.refresh_from_db()
        assert product.stock_quantity == Decimal("6.00")
        assert item(org_a, "S1").stock_allocated == Decimal("4.00")

    def test_stock_equal_to_the_quantity_is_enough(self, org_a):
        product = listing_product(org_a, stock_quantity=3)

        run(org_a, sale("S1", units=3))

        assert work(org_a) == {"PACKAGING": (3, "PENDING")}
        product.refresh_from_db()
        assert product.stock_quantity == 0

    def test_a_single_unit_takes_one_from_stock(self, org_a):
        product = listing_product(org_a, stock_quantity=4)

        run(org_a, sale("S1", units=1))

        product.refresh_from_db()
        assert product.stock_quantity == Decimal("3.00")
        assert work(org_a) == {"PACKAGING": (1, "PENDING")}

    def test_one_packaging_per_sale_and_one_preparation_per_product(self, org_a):
        # Four items of three products: two listings sell product A.
        listing_product(org_a, listing="MLM-A1", variant="", stock_quantity=1)
        product_a = Product.objects.get(org=org_a)
        product_a.name = "A"
        product_a.save()
        ChannelListing.objects.create(
            org=org_a,
            channel="MERCADOLIBRE",
            listing_id="MLM-A2",
            product=product_a,
        )
        b = listing_product(org_a, listing="MLM-B", variant="", stock_quantity=0)
        c = listing_product(org_a, listing="MLM-C", variant="", stock_quantity=9)
        Product.objects.filter(pk=b.pk).update(name="B")
        Product.objects.filter(pk=c.pk).update(name="C")

        result = run(
            org_a,
            pack(
                "P1",
                sale("P1-1", listing="MLM-A1", variant="", units=2),
                sale("P1-2", listing="MLM-A2", variant="", units=3),
                sale("P1-3", listing="MLM-B", variant="", units=4),
                sale("P1-4", listing="MLM-C", variant="", units=1),
            ),
        )

        # A: 5 sold, 1 in stock, 4 to prepare. B: 4 to prepare. C: in stock.
        assert preparations(org_a, "P1") == {"A": 4, "B": 4}
        assert packaging(org_a, "P1") == [(10, "PENDING")]
        assert (result.preparation_orders, result.packaging_orders) == (2, 1)

    def test_each_sale_gets_its_own_work_orders(self, org_a):
        listing_product(org_a, stock_quantity=3)

        run(org_a, sale("S1", units=2), sale("S2", units=2), sale("S3", units=1))

        assert work(org_a, "S1") == {"PACKAGING": (2, "PENDING")}
        assert work(org_a, "S2") == {
            "PACKAGING": (2, "PENDING"),
            "PREPARATION": (1, "PENDING"),
        }
        assert work(org_a, "S3") == {
            "PACKAGING": (1, "PENDING"),
            "PREPARATION": (1, "PENDING"),
        }
        assert WorkOrder.objects.filter(org=org_a, kind="PACKAGING").count() == 3

    def test_reimport_keeps_one_of_each(self, org_a):
        listing_product(org_a, stock_quantity=0)
        run(org_a, sale("S1", units=2))

        run(org_a, sale("S1", units=2))
        run(org_a, sale("S1", units=2))

        assert WorkOrder.objects.filter(org=org_a).count() == 2

    def test_a_quantity_change_resizes_the_open_work(self, org_a):
        listing_product(org_a, stock_quantity=1)
        run(org_a, sale("S1", units=2))

        run(org_a, sale("S1", units=5))

        assert work(org_a) == {
            "PREPARATION": (4, "PENDING"),
            "PACKAGING": (5, "PENDING"),
        }

    def test_shipping_finishes_all_the_sales_work(self, org_a):
        listing_product(org_a, stock_quantity=1)
        run(org_a, sale("S1", units=3))

        run(org_a, sale("S1", units=3, status="En camino"))

        assert work(org_a) == {
            "PREPARATION": (2, "DONE"),
            "PACKAGING": (3, "DONE"),
        }

    def test_sales_first_seen_finished_touch_no_stock(self, org_a):
        product = listing_product(org_a, stock_quantity=5)

        run(
            org_a,
            sale("S1", status="Entregado"),
            sale("S2", status="Cancelada por el comprador"),
            sale("S3", status="En camino"),
        )

        product.refresh_from_db()
        assert product.stock_quantity == Decimal("5.00")
        assert not WorkOrder.objects.filter(org=org_a).exists()

    def test_channels_share_the_products_stock(self, org_a):
        product = listing_product(org_a, stock_quantity=3)
        ChannelListing.objects.create(
            org=org_a, channel="AMAZON", listing_id="B00X", product=product
        )

        run(org_a, sale("S1", units=2))
        sync_channel_orders(
            org_a,
            "AMAZON",
            [
                ChannelOrder(
                    external_id="AMZ-1",
                    order_date=datetime.date(2026, 11, 16),
                    lines=[
                        ChannelLine(
                            external_id="AMZ-1-1",
                            quantity=Decimal("2"),
                            unit_price=Decimal("10"),
                            listing_id="B00X",
                            status="PENDING",
                        )
                    ],
                )
            ],
        )

        product.refresh_from_db()
        assert product.stock_quantity == 0
        amazon = Order.objects.get(org=org_a, channel="AMAZON")
        assert {(wo.kind, wo.quantity) for wo in amazon.work_orders.all()} == {
            ("PACKAGING", 2),
            ("PREPARATION", 1),
        }
        assert Order.objects.get(org=org_a, channel="MERCADOLIBRE").external_id == "S1"

    def test_items_without_a_product_are_each_prepared(self, org_a):
        result = run(
            org_a, pack("P1", sale("P1-A", listing=" "), sale("P1-B", listing=" "))
        )

        assert item(org_a, "P1-A").product is None
        prepared = WorkOrder.objects.filter(org=org_a, kind="PREPARATION")
        assert sorted(wo.line_item.external_id for wo in prepared) == ["P1-A", "P1-B"]
        assert packaging(org_a, "P1") == [(2, "PENDING")]
        assert len(result.of_level(ACTION_REQUIRED)) == 2

    def test_completing_a_work_order_twice_is_refused(self, org_a):
        run(org_a, sale("S1"))
        work_order = WorkOrder.objects.get(org=org_a, kind="PREPARATION")

        assert inventory.complete_work_order(work_order)
        assert not inventory.complete_work_order(work_order)
        assert item(org_a, "S1").stock_allocated == Decimal("1.00")

    def test_a_finished_preparation_only_serves_its_own_product(self, org_a):
        a = listing_product(org_a, listing="MLM-A", variant="", name="A")
        listing_product(org_a, listing="MLM-B", variant="", name="B")
        ChannelListing.objects.create(
            org=org_a, channel="MERCADOLIBRE", listing_id="MLM-A2", product=a
        )
        run(
            org_a,
            pack(
                "P1",
                sale("P1-1", listing="MLM-B", variant="", units=2),
                sale("P1-2", listing="MLM-A", variant="", units=2),
                sale("P1-3", listing="MLM-A2", variant="", units=1),
            ),
        )
        prepare_a = WorkOrder.objects.get(org=org_a, kind="PREPARATION", product=a)
        assert prepare_a.quantity == 3
        # Made one short: the first A item is served in full, the second not.
        prepare_a.quantity = Decimal("2")

        inventory.complete_work_order(prepare_a)

        allocated = {
            line.external_id: line.stock_allocated
            for line in OrderLineItem.objects.filter(org=org_a)
        }
        assert allocated == {"P1-1": 0, "P1-2": 2, "P1-3": 0}

    def test_packaging_waits_for_the_sales_open_preparations(self, org_a):
        listing_product(org_a, listing="MLM-A", variant="", stock_quantity=0)
        listing_product(org_a, listing="MLM-B", variant="", stock_quantity=0)
        run(
            org_a,
            pack(
                "P1",
                sale("P1-A", listing="MLM-A", variant=""),
                sale("P1-B", listing="MLM-B", variant=""),
            ),
        )
        packing = WorkOrder.objects.get(org=org_a, kind="PACKAGING")
        first, second = WorkOrder.objects.filter(org=org_a, kind="PREPARATION")

        assert inventory.why_not_completable(packing) == (
            "2 preparation order(s) of the sale are still open."
        )
        assert not inventory.complete_work_order(packing)
        assert inventory.complete_work_order(first)
        assert not inventory.complete_work_order(packing)
        assert inventory.complete_work_order(second)

        assert inventory.why_not_completable(packing) is None
        assert inventory.complete_work_order(packing)
        packing.refresh_from_db()
        assert packing.status == "DONE"

    def test_another_sales_preparation_does_not_hold_packaging(self, org_a):
        listing_product(org_a, stock_quantity=1)
        run(org_a, sale("S1", units=1), sale("S2", units=1))
        s1_packing = WorkOrder.objects.get(
            org=org_a, kind="PACKAGING", order__external_id="S1"
        )
        assert WorkOrder.objects.filter(
            org=org_a, kind="PREPARATION", order__external_id="S2", status="PENDING"
        ).exists()

        assert inventory.complete_work_order(s1_packing)

    def test_a_cancelled_preparation_does_not_hold_packaging(self, org_a):
        listing_product(org_a, stock_quantity=0)
        run(org_a, sale("S1", units=2))
        preparation = WorkOrder.objects.get(org=org_a, kind="PREPARATION")
        WorkOrder.objects.filter(pk=preparation.pk).update(status="CANCELLED")

        packing = WorkOrder.objects.get(org=org_a, kind="PACKAGING")
        assert inventory.complete_work_order(packing)

    def test_finished_packaging_sets_nothing_aside(self, org_a):
        listing_product(org_a, stock_quantity=1)
        run(org_a, sale("S1", units=3))
        inventory.complete_work_order(
            WorkOrder.objects.get(org=org_a, kind="PREPARATION")
        )
        allocated = item(org_a, "S1").stock_allocated

        assert inventory.complete_work_order(
            WorkOrder.objects.get(org=org_a, kind="PACKAGING")
        )

        # Packing makes no units: what is set aside stays as it was.
        assert item(org_a, "S1").stock_allocated == allocated == Decimal("3.00")

    def test_shipping_finishes_preparations_before_packaging(self, org_a):
        listing_product(org_a, stock_quantity=0)
        run(org_a, pack("P1", sale("P1-A", units=2), sale("P1-B", listing="MLM9")))

        run(
            org_a,
            pack(
                "P1",
                sale("P1-A", units=2, status="Entregado"),
                sale("P1-B", listing="MLM9", status="Entregado"),
            ),
        )

        statuses = {wo.kind: wo.status for wo in WorkOrder.objects.filter(org=org_a)}
        assert statuses == {"PREPARATION": "DONE", "PACKAGING": "DONE"}
        assert not WorkOrder.objects.filter(org=org_a, status="PENDING").exists()

    def test_the_users_example_three_products_four_units(self, org_a):
        """A sale of A x1, B x1 and C x2 with no stock: three preparation
        orders (A 1, B 1, C 2) and one packaging order for the 4 units."""
        for name in ("A", "B", "C"):
            listing_product(
                org_a, listing=f"MLM-{name}", variant="", name=name, stock_quantity=0
            )

        result = run(
            org_a,
            pack(
                "P1",
                sale("P1-1", listing="MLM-A", variant="", units=1),
                sale("P1-2", listing="MLM-B", variant="", units=1),
                sale("P1-3", listing="MLM-C", variant="", units=2),
            ),
        )

        assert preparations(org_a, "P1") == {"A": 1, "B": 1, "C": 2}
        assert packaging(org_a, "P1") == [(4, "PENDING")]
        assert (result.preparation_orders, result.packaging_orders) == (3, 1)


# ---------------------------------------------------------------------------
# Availability time, counted the way each channel counts it
# ---------------------------------------------------------------------------

FRIDAY = datetime.date(2026, 11, 13)


def amazon_sale(org, product, order_date=FRIDAY):
    """An Amazon sale through the channel-neutral sync (Amazon has no report
    parser yet)."""
    ChannelListing.objects.get_or_create(
        org=org, channel="AMAZON", listing_id="B00X", defaults={"product": product}
    )
    sync_channel_orders(
        org,
        "AMAZON",
        [
            ChannelOrder(
                external_id="AMZ-1",
                order_date=order_date,
                lines=[
                    ChannelLine(
                        external_id="AMZ-1-1",
                        quantity=Decimal("1"),
                        unit_price=Decimal("10"),
                        listing_id="B00X",
                        status="PENDING",
                    )
                ],
            )
        ],
    )
    return item(org, "AMZ-1-1")


def weekday_calendar(org, days=5):
    """The org's default calendar, open on the first `days` weekdays."""
    hours = {}
    for name in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday")[
        :days
    ]:
        hours[f"{name}_open"] = datetime.time(9)
        hours[f"{name}_close"] = datetime.time(17)
    return BusinessCalendar.objects.create(org=org, is_default=True, **hours)


@pytest.mark.django_db
class TestDueDatesByChannel:
    def test_mercadolibre_counts_calendar_days(self, org_a):
        listing_product(org_a, availability_days=3)

        run(org_a, sale("S1", date="13 de noviembre de 2026 10:00 hs."))

        # Friday plus 3 calendar days is Monday.
        assert item(org_a, "S1").due_date == datetime.date(2026, 11, 16)

    def test_amazon_counts_working_days(self, org_a):
        product = Product.objects.create(
            org=org_a, name="P", price=1, availability_days=3
        )

        line = amazon_sale(org_a, product)

        # Friday plus 3 working days skips the weekend: Wednesday.
        assert line.due_date == datetime.date(2026, 11, 18)
        assert {wo.due_date for wo in WorkOrder.objects.filter(org=org_a)} == {
            line.due_date
        }
        assert Order.objects.get(org=org_a).due_date == line.due_date

    def test_working_days_skip_the_orgs_holidays(self, org_a):
        calendar = weekday_calendar(org_a)
        BusinessHoliday.objects.create(
            org=org_a, calendar=calendar, date=datetime.date(2026, 11, 17), name="H"
        )
        product = Product.objects.create(
            org=org_a, name="P", price=1, availability_days=3
        )

        line = amazon_sale(org_a, product)

        assert line.due_date == datetime.date(2026, 11, 19)

    def test_working_days_follow_the_orgs_open_weekdays(self, org_a):
        weekday_calendar(org_a, days=6)
        product = Product.objects.create(
            org=org_a, name="P", price=1, availability_days=3
        )

        line = amazon_sale(org_a, product)

        # Open Saturdays: Saturday, Monday, Tuesday.
        assert line.due_date == datetime.date(2026, 11, 17)

    def test_a_calendar_open_on_no_day_falls_back_to_weekdays(self, org_a):
        BusinessCalendar.objects.create(org=org_a, is_default=True)
        product = Product.objects.create(
            org=org_a, name="P", price=1, availability_days=3
        )

        assert amazon_sale(org_a, product).due_date == datetime.date(2026, 11, 18)

    def test_no_availability_is_ready_the_same_day_on_any_channel(self, org_a):
        product = Product.objects.create(org=org_a, name="P", price=1)
        sunday = datetime.date(2026, 11, 15)

        assert amazon_sale(org_a, product, order_date=sunday).due_date == sunday

    @pytest.mark.parametrize(
        ("channel", "expected"),
        [
            ("MERCADOLIBRE", datetime.date(2026, 11, 23)),
            ("STORE", datetime.date(2026, 11, 23)),
            ("AMAZON", datetime.date(2026, 11, 27)),
        ],
    )
    def test_day_counter_per_channel(self, org_a, channel, expected):
        add_days = inventory.day_counter(org_a.id, channel)

        assert add_days(FRIDAY, 10) == expected


@pytest.mark.django_db
class TestChannelPreferences:
    """The day count is an admin preference; imported orders keep their dates."""

    FRIDAY_SALE = "13 de noviembre de 2026 10:00 hs."

    def _set(self, channel, day_count):
        ChannelPreference.objects.update_or_create(
            channel=channel, defaults={"availability_day_count": day_count}
        )

    def test_a_change_applies_to_sales_imported_after_it(self, org_a):
        listing_product(org_a, availability_days=3)
        self._set("MERCADOLIBRE", "CALENDAR")
        run(org_a, sale("S1", date=self.FRIDAY_SALE))

        self._set("MERCADOLIBRE", "WORKING")
        run(org_a, sale("S1", date=self.FRIDAY_SALE), sale("S2", date=self.FRIDAY_SALE))

        # S1 keeps its calendar-day date; S2, new, counts working days.
        assert item(org_a, "S1").due_date == datetime.date(2026, 11, 16)
        assert Order.objects.get(external_id="S1").due_date == datetime.date(
            2026, 11, 16
        )
        assert item(org_a, "S2").due_date == datetime.date(2026, 11, 18)
        assert Order.objects.get(external_id="S2").due_date == datetime.date(
            2026, 11, 18
        )

    def test_a_new_availability_time_does_not_move_imported_dates(self, org_a):
        product = listing_product(org_a, availability_days=3)
        run(org_a, sale("S1"))
        product.availability_days = 10
        product.save()

        run(org_a, sale("S1"), sale("S2"))

        assert item(org_a, "S1").due_date == datetime.date(2026, 11, 18)
        assert item(org_a, "S2").due_date == datetime.date(2026, 11, 25)

    def test_the_orders_due_date_is_not_recalculated_by_later_reports(self, org_a):
        listing_product(org_a, listing="MLM-SLOW", variant="", availability_days=5)
        rows = lambda slow_status: pack(  # noqa: E731
            "P1",
            sale("P1-A", listing="MLM-SLOW", variant="", status=slow_status),
            sale("P1-B"),
        )
        run(org_a, rows("Etiqueta impresa"))

        run(org_a, rows("Cancelada por el comprador"))

        assert Order.objects.get(org=org_a).due_date == datetime.date(2026, 11, 20)

    @pytest.mark.parametrize(
        ("statuses", "expected"),
        [
            # A cancelled item's date does not stretch the order's.
            (("Cancelada por el comprador", "Etiqueta impresa"), 15),
            # Every item cancelled: the order still has the date it had.
            (("Cancelada por el comprador", "Cancelada por el comprador"), 20),
            # Returned items were delivered: their date counts.
            (("Devolución finalizada", "Etiqueta impresa"), 20),
        ],
    )
    def test_an_orders_due_date_on_first_import(self, org_a, statuses, expected):
        listing_product(org_a, listing="MLM-SLOW", variant="", availability_days=5)
        slow, fast = statuses

        run(
            org_a,
            pack(
                "P1",
                sale("P1-A", listing="MLM-SLOW", variant="", status=slow),
                sale("P1-B", status=fast),
            ),
        )

        assert Order.objects.get(org=org_a).due_date == datetime.date(
            2026, 11, expected
        )

    def test_amazon_can_be_set_to_calendar_days(self, org_a):
        self._set("AMAZON", "CALENDAR")
        product = Product.objects.create(
            org=org_a, name="P", price=1, availability_days=3
        )

        assert amazon_sale(org_a, product).due_date == datetime.date(2026, 11, 16)

    @pytest.mark.parametrize(
        ("channel", "expected"), [("MERCADOLIBRE", "CALENDAR"), ("AMAZON", "WORKING")]
    )
    def test_without_a_preference_the_channels_default_applies(self, channel, expected):
        ChannelPreference.objects.all().delete()

        assert ChannelPreference.day_count(channel) == expected

    def test_the_migration_seeds_one_preference_per_channel(self):
        from django.apps import apps as django_apps

        migration = importlib.import_module("orders.migrations.0005_sales_channels")
        ChannelPreference.objects.all().delete()

        migration.seed_channel_preferences(django_apps, None)
        migration.seed_channel_preferences(django_apps, None)

        assert dict(
            ChannelPreference.objects.values_list("channel", "availability_day_count")
        ) == {"STORE": "CALENDAR", "MERCADOLIBRE": "CALENDAR", "AMAZON": "WORKING"}


# ---------------------------------------------------------------------------
# The import log: what was done, what needs doing, what was not imported
# ---------------------------------------------------------------------------


def messages(result, level):
    return [(entry.sale, entry.message) for entry in result.of_level(level)]


@pytest.mark.django_db
class TestImportLog:
    def test_new_product_defaults(self, org_a):
        row = sale("S1", listing="MLM7", variant="Talla : 2", price=350, sku=" ")

        result = run(org_a, row)

        product = item(org_a, "S1").product
        assert product.name == "Producto MLM7 (Talla : 2)"
        # From the report: the listing's title and variant.
        assert product.description == "Producto MLM7. Talla : 2"
        assert product.sku is None
        assert product.price == Decimal("350.00")
        assert product.currency == "MXN"
        assert product.category is None
        assert product.is_active is True
        assert product.stock_quantity is None
        assert product.availability_days is None
        assert product.org == org_a
        listing = ChannelListing.objects.get(org=org_a)
        assert listing.product_created is True
        # How it was made is a comment on the product, not its description.
        [note] = Comment.objects.filter(object_id=product.pk)
        assert note.comment == (
            "Created by the Mercado Libre import from listing MLM7, variant Talla : 2, "
            "priced at the unit price of the sale that created it."
        )
        assert (note.org, note.is_internal, note.commented_by) == (org_a, True, None)
        assert messages(result, ACTION_REQUIRED) == [
            (
                "S1",
                "Product 'Producto MLM7 (Talla : 2)' was created from listing MLM7: "
                "complete its stock, availability time and category, or point the "
                "listing at an existing product.",
            )
        ]

    def test_an_empty_price_creates_neither_order_nor_product(self, org_a):
        result = run(org_a, sale("S1", listing="MLM-NEW", price=""), sale("S2"))

        assert list(Order.objects.values_list("external_id", flat=True)) == ["S2"]
        assert not Product.objects.filter(name__contains="MLM-NEW").exists()
        assert not ChannelListing.objects.filter(listing_id="MLM-NEW").exists()
        assert messages(result, NOT_IMPORTED) == [
            (
                "S1",
                "Item S1: the unit price is empty. The price is mandatory, so "
                "neither the order nor its products were created.",
            )
        ]
        assert (result.orders_not_imported, result.products_created) == (1, 1)

    def test_an_empty_price_refuses_the_whole_pack(self, org_a):
        result = run(
            org_a,
            pack("P1", sale("P1-A", listing="MLM-A"), sale("P1-B", price="")),
        )

        assert not Order.objects.exists()
        assert not Product.objects.exists()
        assert messages(result, NOT_IMPORTED)[0][0] == "P1"
        assert (
            "Item P1-B: the unit price is empty."
            in messages(result, NOT_IMPORTED)[0][1]
        )

    def test_an_empty_price_refuses_the_sale_of_a_known_product_too(self, org_a):
        product = listing_product(org_a, stock_quantity=5)

        result = run(org_a, sale("S1", price=""))

        assert not Order.objects.exists()
        product.refresh_from_db()
        assert product.stock_quantity == 5
        assert result.orders_not_imported == 1

    def test_an_empty_price_on_a_known_sale_leaves_it_as_it_was(self, org_a):
        run(org_a, sale("S1", status="Etiqueta impresa"))

        result = run(org_a, sale("S1", status="Entregado", price=""))

        assert Order.objects.get(org=org_a).fulfillment_status == "PENDING"
        assert result.orders_not_imported == 1

    def test_a_zero_price_is_a_price(self, org_a):
        run(org_a, sale("S1", price=0))

        assert item(org_a, "S1").unit_price == 0

    def test_a_new_products_description_is_never_empty(self, org_a):
        row = sale("S1", listing="MLM5", variant=" ")
        row[10] = " "  # no title

        run(org_a, row)

        product = item(org_a, "S1").product
        assert (product.name, product.description) == ("MLM5", "MLM5")
        assert product.price == 400

    def test_a_product_found_by_sku_needs_no_follow_up(self, org_a):
        Product.objects.create(org=org_a, name="Own", sku="SKU-9", price=1)

        result = run(org_a, sale("S1", sku="SKU-9"))

        assert ChannelListing.objects.get(org=org_a).product_created is False
        assert not result.of_level(ACTION_REQUIRED)

    def test_changes_are_logged(self, org_a):
        listing_product(org_a, stock_quantity=1, name="Vestido")
        first = run(org_a, sale("S1", units=3))
        cancelled = run(org_a, sale("S1", units=3, status="Cancelaste la venta"))

        assert messages(first, CHANGE) == [
            ("S1", "Took 1 x Vestido from stock."),
            ("S1", "Created with 1 item(s), pending, due 2026-11-15."),
            ("S1", "Preparation order: 2 x Vestido, due 2026-11-15."),
            ("S1", "Packaging order: 3 unit(s), due 2026-11-15."),
        ]
        assert messages(cancelled, CHANGE) == [
            ("S1", "Put 1 x Vestido back in stock (cancelled before shipping)."),
            ("S1", "Status changed from pending to cancelled."),
        ]

    def test_an_unchanged_reimport_logs_nothing(self, org_a):
        run(org_a, sale("S1"))

        assert run(org_a, sale("S1")).entries == []

    def test_a_return_is_an_action_required(self, org_a):
        listing_product(org_a, name="Vestido")
        run(org_a, sale("S1", status="Entregado"))

        result = run(org_a, sale("S1", status="Devolución finalizada"))

        assert messages(result, ACTION_REQUIRED) == [
            (
                "S1",
                "Return inspection opened: 1 x Vestido. Accept or reject it in "
                "Return inspections when the goods are checked.",
            )
        ]

    def test_a_sale_that_fails_is_rolled_back_and_the_rest_imported(
        self, org_a, monkeypatch
    ):
        real_plan = inventory.plan_line

        def plan_line(line):
            if line.external_id == "S2":
                raise RuntimeError("disk on fire")
            return real_plan(line)

        monkeypatch.setattr(inventory, "plan_line", plan_line)

        # S3 sells S2's listing, so it must not reuse anything S2 made.
        result = run(
            org_a,
            sale("S1", listing="MLM1"),
            sale("S2", listing="MLM2"),
            sale("S3", listing="MLM2"),
        )

        assert sorted(Order.objects.values_list("external_id", flat=True)) == [
            "S1",
            "S3",
        ]
        s3 = item(org_a, "S3")
        assert Product.objects.filter(pk=s3.product_id).exists()
        assert ChannelListing.objects.filter(org=org_a, listing_id="MLM2").count() == 1
        assert (result.orders_created, result.orders_not_imported) == (2, 1)
        assert result.products_created == 2
        assert messages(result, NOT_IMPORTED) == [
            (
                "S2",
                "Failed while saving (RuntimeError: disk on fire). Nothing of this "
                "sale was saved.",
            )
        ]
        assert all(entry.sale != "S2" for entry in result.of_level(CHANGE))

    @pytest.mark.parametrize(
        ("rows", "reason"),
        [
            ([sale("S1", units=0)], "Item S1: the quantity must be above 0"),
            ([sale("S1", units=-2)], "Item S1: the quantity must be above 0"),
            ([sale("S1", price=10**14)], "Item S1: the unit price is out of range."),
            (
                [pack("S1", sale("X"), sale("X"))],
                "Item X appears twice.",
            ),
        ],
    )
    def test_invalid_sales_are_not_imported_with_the_reason(self, org_a, rows, reason):
        result = run(org_a, *rows, sale("OK"))

        assert list(Order.objects.values_list("external_id", flat=True)) == ["OK"]
        [(sale_id, message)] = messages(result, NOT_IMPORTED)
        assert sale_id == "S1"
        assert message.startswith(reason)
        assert result.orders_not_imported == 1

    @pytest.mark.parametrize(
        ("line", "total", "reason"),
        [
            ({"external_id": ""}, None, "An item has no sale number."),
            ({}, Decimal("1e14"), "The sale's total is out of range."),
        ],
    )
    def test_what_any_channels_parser_hands_over_is_checked(
        self, org_a, line, total, reason
    ):
        fields = {"external_id": "I1", "quantity": Decimal("1"), "unit_price": 0}
        data = ChannelOrder(
            external_id="S1",
            lines=[ChannelLine(**{**fields, **line})],
            total_amount=total,
        )

        result = sync_channel_orders(org_a, "MERCADOLIBRE", [data])

        assert messages(result, NOT_IMPORTED) == [("S1", reason)]
        assert not Order.objects.exists()

    def test_a_pack_with_no_products_is_not_imported(self, org_a):
        head, *_children = pack("P1", sale("P1-A"))

        result = run(org_a, head, sale("S2"))

        assert messages(result, NOT_IMPORTED) == [("P1", "It has no items.")]
        assert list(Order.objects.values_list("external_id", flat=True)) == ["S2"]

    def test_a_sale_twice_in_one_report_is_a_warning(self, org_a):
        result = run(org_a, sale("S1"), sale("S1", status="Entregado"))

        assert ("S1", "The sale appears more than once; the last one wins.") in (
            messages(result, WARNING)
        )
        assert Order.objects.get(org=org_a).fulfillment_status == "DELIVERED"


# ---------------------------------------------------------------------------
# Returns after shipping wait on an inspection
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReturnInspections:
    def _returned(self, org, stock=2, units=1, status="Devolución finalizada"):
        product = listing_product(org, stock_quantity=stock)
        run(org, sale("S1", units=units))
        run(org, sale("S1", units=units, status="Entregado"))
        run(org, sale("S1", units=units, status=status))
        product.refresh_from_db()
        return product, ReturnInspection.objects.get(org=org)

    def test_a_return_after_delivery_opens_an_inspection(self, org_a):
        product, inspection = self._returned(org_a, units=2)

        assert product.stock_quantity == 0
        assert (inspection.quantity, inspection.restock_quantity) == (2, 2)
        assert inspection.product == product
        assert inspection.order == Order.objects.get(org=org_a)
        assert item(org_a, "S1").stock_allocated == 0

    def test_reimporting_a_return_neither_restocks_nor_reopens(self, org_a):
        product, _inspection = self._returned(org_a)

        result = run(org_a, sale("S1", status="Devolución finalizada"))

        product.refresh_from_db()
        assert product.stock_quantity == Decimal("1.00")
        assert result.returns_to_inspect == 0
        assert ReturnInspection.objects.filter(org=org_a).count() == 1

    def test_a_cancellation_after_shipping_is_inspected_too(self, org_a):
        product, inspection = self._returned(org_a, status="Cancelada por el comprador")

        assert product.stock_quantity == Decimal("1.00")
        assert inspection.status == "PENDING"

    def test_a_sale_first_seen_returned_is_history(self, org_a):
        listing_product(org_a, stock_quantity=2)

        run(org_a, sale("S1", status="Devolución finalizada"))

        assert not ReturnInspection.objects.exists()

    def test_a_history_sale_returned_later_is_inspected(self, org_a):
        listing_product(org_a, stock_quantity=2)
        run(org_a, sale("S1", status="Entregado"))

        run(org_a, sale("S1", status="Devolución en camino"))

        assert ReturnInspection.objects.get(org=org_a).quantity == 1

    def test_accepting_restocks_what_was_fit_for_sale(self, org_a):
        product, inspection = self._returned(org_a, stock=3, units=3)
        inspection.restock_quantity = Decimal("2")
        user = User.objects.create_user(email="checker@test.com", password="x")

        assert inventory.accept_return(inspection, user)

        product.refresh_from_db()
        inspection.refresh_from_db()
        assert product.stock_quantity == Decimal("2.00")
        assert (inspection.status, inspection.outcome) == ("DONE", "ACCEPTED")
        assert inspection.kind == "INSPECTION"
        assert inspection.completed_by == user
        assert inspection.completed_at is not None

    def test_rejecting_restocks_nothing(self, org_a):
        product, inspection = self._returned(org_a)

        assert inventory.reject_return(inspection, None)

        product.refresh_from_db()
        inspection.refresh_from_db()
        assert product.stock_quantity == Decimal("1.00")
        assert inspection.outcome == "REJECTED"

    def test_a_return_is_decided_once(self, org_a):
        product, inspection = self._returned(org_a)
        assert inventory.accept_return(inspection, None)

        stale = ReturnInspection.objects.get(pk=inspection.pk)
        stale.status = "PENDING"
        assert not inventory.accept_return(stale, None)
        assert not inventory.reject_return(stale, None)

        product.refresh_from_db()
        assert product.stock_quantity == Decimal("2.00")

    def test_restock_quantity_is_bounded_by_what_came_back(self, org_a):
        product, inspection = self._returned(org_a)
        inspection.restock_quantity = Decimal("50")

        inventory.accept_return(inspection, None)

        product.refresh_from_db()
        assert product.stock_quantity == Decimal("2.00")
        inspection.refresh_from_db()
        assert inspection.restock_quantity == Decimal("1.00")

    def test_restock_quantity_out_of_range_fails_validation(self, org_a):
        _product, inspection = self._returned(org_a)
        inspection.restock_quantity = Decimal("-1")

        with pytest.raises(ValidationError, match="between 0"):
            inspection.full_clean()

    def test_accepting_an_unlinked_item_changes_no_stock(self, org_a):
        run(org_a, sale("S1", listing=" "))
        run(org_a, sale("S1", listing=" ", status="Entregado"))
        run(org_a, sale("S1", listing=" ", status="Devolución finalizada"))
        inspection = ReturnInspection.objects.get(org=org_a)

        assert inspection.product is None
        assert inventory.accept_return(inspection, None)


# ---------------------------------------------------------------------------
# Malformed input and the command
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBadReports:
    def test_a_file_that_is_not_a_sales_report_is_refused(self, org_a):
        book = openpyxl.Workbook()
        book.active.append(["Nombre", "Precio"])
        book.active.append(["Algo", 1])
        buffer = io.BytesIO()
        book.save(buffer)

        with pytest.raises(ChannelImportError, match="no '# de venta'"):
            import_channel_report(org_a, "MERCADOLIBRE", "x.xlsx", buffer.getvalue())
        assert not Order.objects.exists()

    def test_missing_required_columns_are_named(self, org_a):
        book = openpyxl.Workbook()
        book.active.append(["Ventas"])
        book.active.append(["# de venta", "Fecha de venta"])
        buffer = io.BytesIO()
        book.save(buffer)

        with pytest.raises(ChannelImportError, match="missing the columns Estado"):
            import_channel_report(org_a, "MERCADOLIBRE", "x.xlsx", buffer.getvalue())

    def test_unreadable_file_is_refused(self, org_a):
        with pytest.raises(ChannelImportError, match="could not be opened"):
            import_channel_report(org_a, "MERCADOLIBRE", "x.xlsx", b"not a zip")

    def test_wrong_extension_is_refused(self, org_a):
        with pytest.raises(ChannelImportError, match=".xlsx or .csv"):
            import_channel_report(org_a, "MERCADOLIBRE", "x.pdf", b"data")

    def test_channel_without_an_importer_is_refused(self, org_a):
        with pytest.raises(ChannelImportError, match="no report importer for Amazon"):
            import_channel_report(org_a, "AMAZON", "x.xlsx", report(sale("S1")))

    def test_truncated_pack_is_imported_with_a_warning(self, org_a):
        head, child, _missing = pack("P1", sale("P1-A"), sale("P1-B"))

        result = run(org_a, head, child)

        assert Order.objects.get(org=org_a).line_items.count() == 1
        assert result.warnings == ["Expected 2 products, found 1."]

    def test_unreadable_sale_date_is_a_warning_not_a_failure(self, org_a):
        result = run(org_a, sale("S1", date="ayer"))

        order = Order.objects.get(org=org_a)
        assert order.order_date is None
        assert item(org_a, "S1").due_date is None
        assert "The sale date could not be read." in result.warnings

    def test_a_pack_stops_at_a_row_without_a_sale_id(self, org_a):
        head, child, _next = pack("P1", sale("P1-A"), sale("P1-B"))

        result = run(org_a, head, child, sale(" "), sale("S2"))

        assert Order.objects.get(org=org_a, external_id="P1").line_items.count() == 1
        assert Order.objects.filter(org=org_a, external_id="S2").exists()
        assert result.warnings == [
            "Expected 2 products, found 1.",
            "A row with no sale number after sale P1-A was skipped.",
        ]

    @pytest.mark.parametrize(
        "date", ["31 de febrero de 2026 10:00 hs.", "15 de brumario de 2026 10:00 hs."]
    )
    def test_impossible_sale_dates_are_unreadable(self, org_a, date):
        result = run(org_a, sale("S1", date=date))

        assert Order.objects.get(org=org_a).order_date is None
        assert "The sale date could not be read." in result.warnings

    def test_a_real_excel_date_cell_is_read(self, org_a):
        run(org_a, sale("S1", date=datetime.datetime(2026, 11, 15, 10, 40)))

        assert Order.objects.get(org=org_a).order_date == datetime.date(2026, 11, 15)

    @pytest.mark.parametrize("shipped", ["30 de febrero | 10:00", "2 de brumario"])
    def test_an_unreadable_shipped_date_is_left_empty(self, org_a, shipped):
        run(org_a, sale("S1", status="En camino", shipped=shipped))

        assert Order.objects.get(org=org_a).shipped_date is None

    def test_unreadable_units_fall_back_to_one(self, org_a):
        row = sale("S1")
        row[4] = "n/a"

        run(org_a, row)

        line = item(org_a, "S1")
        assert (line.quantity, line.unit_price) == (1, 400)

    def test_an_unreadable_price_counts_as_empty(self, org_a):
        row = sale("S1")
        row[12] = "n/a"

        result = run(org_a, row)

        assert not Order.objects.exists()
        assert messages(result, NOT_IMPORTED)[0][1].startswith(
            "Item S1: the unit price is empty."
        )

    def test_a_row_shorter_than_the_header_reads_as_blank(self, org_a):
        run(org_a, sale("S1")[:13])

        order = Order.objects.get(org=org_a)
        assert (order.buyer_name, order.shipping_country) == ("", "")

    def test_rows_without_a_sale_id_are_skipped_and_logged(self, org_a):
        result = run(org_a, sale(" "), sale("S1"))

        assert Order.objects.filter(org=org_a).count() == 1
        assert result.warnings == [
            "A row with no sale number before the first sale was skipped."
        ]


@pytest.mark.django_db
class TestCommand:
    def test_imports_a_report_file(self, org_a, tmp_path, capsys):
        path = tmp_path / "ventas.xlsx"
        path.write_bytes(report(sale("S1")))

        call_command(
            "import_channel_orders",
            str(path),
            org=str(org_a.id),
            channel="mercadolibre",
        )

        out = capsys.readouterr().out
        assert "Orders: 1 created, 0 updated, 0 not imported." in out
        assert "Change (3):" in out
        assert "S1: Created with 1 item(s), pending, due 2026-11-15." in out
        assert Order.objects.filter(org=org_a, external_id="S1").exists()

    def test_unknown_org_is_an_error(self, tmp_path):
        path = tmp_path / "ventas.xlsx"
        path.write_bytes(report(sale("S1")))

        with pytest.raises(CommandError, match="No org found"):
            call_command(
                "import_channel_orders", str(path), org="nope", channel="MERCADOLIBRE"
            )

    def test_a_missing_file_is_an_error(self, org_a, tmp_path):
        with pytest.raises(CommandError, match="Cannot read"):
            call_command(
                "import_channel_orders",
                str(tmp_path / "nope.xlsx"),
                org=str(org_a.id),
                channel="MERCADOLIBRE",
            )

    def test_an_oversized_file_is_refused_before_reading_it(
        self, org_a, tmp_path, monkeypatch
    ):
        from orders.management.commands import import_channel_orders

        monkeypatch.setattr(import_channel_orders, "MAX_REPORT_BYTES", 100)
        path = tmp_path / "ventas.xlsx"
        path.write_bytes(report(sale("S1")))

        with pytest.raises(CommandError, match="10 MB"):
            call_command(
                "import_channel_orders",
                str(path),
                org=org_a.name,
                channel="MERCADOLIBRE",
            )
        assert not Order.objects.exists()

    def test_warnings_go_to_stderr(self, org_a, tmp_path, capsys):
        path = tmp_path / "ventas.xlsx"
        path.write_bytes(report(sale("S1", date="ayer")))

        call_command(
            "import_channel_orders", str(path), org=org_a.name, channel="MERCADOLIBRE"
        )

        assert "Warning (1):\n  S1: The sale date could not be read." in (
            capsys.readouterr().err
        )

    def test_sales_not_imported_are_flagged_on_stderr(self, org_a, tmp_path, capsys):
        path = tmp_path / "ventas.xlsx"
        path.write_bytes(report(sale("S1", price=""), sale("S2")))

        call_command(
            "import_channel_orders", str(path), org=org_a.name, channel="MERCADOLIBRE"
        )

        captured = capsys.readouterr()
        assert "Orders: 1 created, 0 updated, 1 not imported." in captured.out
        assert "Not imported (1):\n  S1: Item S1: the unit price is empty." in (
            captured.err
        )

    def test_an_ambiguous_org_name_is_an_error(self, org_a, tmp_path):
        Org.objects.create(name=org_a.name)
        path = tmp_path / "ventas.xlsx"
        path.write_bytes(report(sale("S1")))

        with pytest.raises(CommandError, match="Multiple orgs"):
            call_command(
                "import_channel_orders",
                str(path),
                org=org_a.name,
                channel="MERCADOLIBRE",
            )

    def test_a_bad_report_is_an_error(self, org_a, tmp_path):
        path = tmp_path / "ventas.xlsx"
        path.write_bytes(b"not a zip")

        with pytest.raises(CommandError, match="could not be opened"):
            call_command(
                "import_channel_orders",
                str(path),
                org=org_a.name,
                channel="MERCADOLIBRE",
            )


@pytest.mark.django_db
def test_orders_made_in_the_crm_need_no_channel_id(org_a):
    account = Account.objects.create(org=org_a, name="Walk-in")
    first = Order.objects.create(org=org_a, account=account, name="A")
    second = Order.objects.create(org=org_a, account=account, name="B")

    assert (first.channel, first.external_id) == ("STORE", "")
    assert second.external_id == ""


@pytest.mark.django_db
@pytest.mark.skipif(not SAMPLE_REPORT.exists(), reason="sample report not present")
def test_sample_mercadolibre_report(org_a):
    data = SAMPLE_REPORT.read_bytes()

    first = import_channel_report(org_a, "MERCADOLIBRE", SAMPLE_REPORT.name, data)
    again = import_channel_report(org_a, "MERCADOLIBRE", SAMPLE_REPORT.name, data)

    assert (first.orders_created, first.items_created) == (1455, 1498)
    assert first.orders_not_imported == 0
    assert first.warnings == []
    # Only sales still to be shipped get work orders: one packaging each, and,
    # with no stock on record yet, a preparation for each of their products.
    open_sales = Order.objects.filter(org=org_a, fulfillment_status="PENDING")
    assert first.packaging_orders == open_sales.count() == 7
    assert first.preparation_orders == (
        OrderLineItem.objects.filter(org=org_a, fulfillment_status="PENDING")
        .order_by()
        .values("order", "product")
        .distinct()
        .count()
    )
    assert (again.orders_created, again.items_created, again.products_created) == (
        0,
        0,
        0,
    )
    assert again.preparation_orders == again.packaging_orders == 0
    assert Order.objects.filter(org=org_a).count() == 1455
    assert not OrderLineItem.objects.filter(org=org_a, product=None).exists()


@pytest.mark.django_db
class TestWorkOrderAdmin:
    URL = "/admin/orders/workorder/"

    def _post(self, client, *work_orders, follow=False):
        return client.post(
            self.URL,
            {
                "action": "mark_done",
                "_selected_action": [str(wo.pk) for wo in work_orders],
            },
            follow=follow,
        )

    def test_mark_done_action_readies_the_item(self, org_a, staff_client):
        run(org_a, sale("S1", units=2))
        work_order = WorkOrder.objects.get(org=org_a, kind="PREPARATION")

        response = self._post(staff_client, work_order)

        assert response.status_code == 302
        work_order.refresh_from_db()
        assert work_order.status == "DONE"
        assert item(org_a, "S1").stock_allocated == Decimal("2.00")

    def test_packaging_with_open_preparation_is_refused_with_the_reason(
        self, org_a, staff_client
    ):
        run(org_a, sale("S1", units=2))
        packing = WorkOrder.objects.get(org=org_a, kind="PACKAGING")

        response = self._post(staff_client, packing, follow=True)

        packing.refresh_from_db()
        assert packing.status == "PENDING"
        content = response.content.decode()
        assert "0 work order(s) marked as done." in content
        assert "1 preparation order(s) of the sale are still open." in content

    def test_preparation_and_packaging_together_complete_in_order(
        self, org_a, staff_client
    ):
        run(org_a, sale("S1", units=2))
        preparation = WorkOrder.objects.get(org=org_a, kind="PREPARATION")
        packing = WorkOrder.objects.get(org=org_a, kind="PACKAGING")

        self._post(staff_client, preparation)
        self._post(staff_client, packing)

        packing.refresh_from_db()
        assert packing.status == "DONE"

    def test_an_inspection_is_not_marked_done(self, org_a, staff_client):
        run(org_a, sale("S1", status="Entregado"))
        run(org_a, sale("S1", status="Devolución finalizada"))
        inspection = WorkOrder.objects.get(org=org_a, kind="INSPECTION")

        response = self._post(staff_client, inspection, follow=True)

        inspection.refresh_from_db()
        assert inspection.status == "PENDING"
        assert b"is accepted or rejected, not marked done" in response.content

    def test_inspections_are_listed_with_the_work_orders(self, org_a, staff_client):
        run(org_a, sale("S1", status="Entregado"))
        run(org_a, sale("S1", status="Devolución finalizada"))

        response = staff_client.get(f"{self.URL}?kind__exact=INSPECTION")

        assert found(response) == 1

    def test_mark_done_needs_staff(self, org_a, client):
        run(org_a, sale("S1"))
        work_order = WorkOrder.objects.get(org=org_a, kind="PREPARATION")
        client.force_login(User.objects.create_user(email="u@test.com", password="x"))

        response = self._post(client, work_order)

        assert response.status_code == 302
        assert response.url.startswith("/admin/login/")
        work_order.refresh_from_db()
        assert work_order.status == "PENDING"


# ---------------------------------------------------------------------------
# Admin: seeing and querying orders, and the returns maintenance screen
# ---------------------------------------------------------------------------


def choose_scope(client, scope):
    """Work in an org (or "instance") in the admin, as the chooser would."""
    session = client.session
    session["admin_scope"] = scope if scope == "instance" else str(scope.pk)
    session.save()


@pytest.fixture
def staff_client(client, org_a):
    """A superuser working in org_a in the admin."""
    client.force_login(
        User.objects.create_superuser(email="staff@test.com", password="x")
    )
    choose_scope(client, org_a)
    return client


_MONTH_NAMES = (
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre "
    "noviembre diciembre"
).split()


def spanish(date):
    """A sale date as the Mercado Libre report writes it."""
    return f"{date.day} de {_MONTH_NAMES[date.month - 1]} de {date.year} 10:00 hs."


def found(response):
    assert response.status_code == 200
    return response.context["cl"].result_count


@pytest.mark.django_db
class TestOrderAdmin:
    URL = "/admin/orders/order/"

    @pytest.fixture
    def orders(self, org_a):
        listing_product(org_a, listing="MLM1", variant="", stock_quantity=0)
        run(
            org_a,
            sale("S1", listing="MLM1", variant=""),
            sale("S2", listing="MLM2", status="Entregado"),
            pack("P1", sale("P1-A", listing="MLM3"), sale("P1-B", listing="MLM4")),
        )
        account = Account.objects.create(org=org_a, name="Walk-in")
        Order.objects.create(org=org_a, account=account, name="Counter sale")

    def test_list_shows_channel_orders_with_their_counts(self, staff_client, orders):
        response = staff_client.get(self.URL)

        assert found(response) == 4
        rows = {o.external_id: o for o in response.context["cl"].result_list}
        assert (rows["P1"].items, rows["P1"].pending_work) == (2, 3)
        assert (rows["S2"].items, rows["S2"].pending_work) == (1, 0)
        assert b"Mercado Libre P1" in response.content

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("channel__exact=MERCADOLIBRE", 3),
            ("channel__exact=STORE", 1),
            ("fulfillment_status__exact=DELIVERED", 1),
            ("status__exact=ACTIVATED", 2),
            ("pending_work=yes", 2),
            ("pending_work=no", 2),
            ("due=overdue", 2),
            ("due=none", 1),
            ("q=P1-B", 1),
            ("q=MLM1", 1),
            ("q=Ana+Compradora", 2),
            ("q=Producto+MLM3", 1),
        ],
    )
    def test_filters_and_search(self, staff_client, orders, query, expected):
        assert found(staff_client.get(f"{self.URL}?{query}")) == expected

    def test_due_today_and_this_week(self, staff_client, org_a):
        today = timezone.localdate()
        listing_product(org_a, listing="MLM1", variant="", availability_days=3)
        run(
            org_a,
            sale("S1", date=spanish(today)),
            sale("S2", listing="MLM1", variant="", date=spanish(today)),
        )

        assert found(staff_client.get(f"{self.URL}?due=today")) == 1
        assert found(staff_client.get(f"{self.URL}?due=week")) == 2

    def test_returns_to_inspect_filter(self, staff_client, org_a):
        run(org_a, sale("S1", status="Entregado"), sale("S2", status="Entregado"))
        run(org_a, sale("S1", status="Devolución finalizada"))

        assert found(staff_client.get(f"{self.URL}?pending_returns=yes")) == 1

    def test_a_channel_order_shows_its_report_fields_read_only(
        self, staff_client, orders
    ):
        order = Order.objects.get(external_id="P1")

        response = staff_client.get(f"{self.URL}{order.pk}/change/")

        assert response.status_code == 200
        readonly = set(response.context["adminform"].readonly_fields)
        assert {"channel", "external_id", "buyer_name", "status"} <= readonly
        inline = response.context["inline_admin_formsets"][0]
        assert not inline.has_add_permission
        assert b"P1-A" in response.content

    def test_a_store_order_stays_editable(self, staff_client, orders):
        order = Order.objects.get(channel="STORE")

        response = staff_client.get(f"{self.URL}{order.pk}/change/")

        readonly = set(response.context["adminform"].readonly_fields)
        assert "status" not in readonly
        assert response.context["inline_admin_formsets"][0].has_add_permission

    def test_line_items_and_work_orders_are_queryable(self, staff_client, orders):
        items = staff_client.get("/admin/orders/orderlineitem/?q=MLM3")
        work = staff_client.get("/admin/orders/workorder/?kind__exact=PREPARATION")

        assert found(items) == 1
        assert found(work) == 3

    def test_needs_staff(self, client, orders):
        client.force_login(User.objects.create_user(email="u@test.com", password="x"))

        response = client.get(self.URL)

        assert response.status_code == 302
        assert response.url.startswith("/admin/login/")


@pytest.mark.django_db
class TestReturnInspectionAdmin:
    URL = "/admin/orders/returninspection/"

    @pytest.fixture
    def inspection(self, org_a):
        listing_product(org_a, stock_quantity=3)
        for status in ("Etiqueta impresa", "Entregado", "Devolución finalizada"):
            run(org_a, sale("S1", units=3, status=status))
        return ReturnInspection.objects.get(org=org_a)

    def _stock(self, inspection):
        inspection.product.refresh_from_db()
        return inspection.product.stock_quantity

    def test_pending_returns_are_listed(self, staff_client, inspection):
        assert found(staff_client.get(f"{self.URL}?status__exact=PENDING")) == 1

    def test_accept_button_restocks_the_units_fit_for_sale(
        self, staff_client, inspection
    ):
        page = staff_client.get(f"{self.URL}{inspection.pk}/change/")
        assert b"Accept and restock" in page.content

        response = staff_client.post(
            f"{self.URL}{inspection.pk}/change/",
            {"restock_quantity": "2", "notes": "One torn", "_accept_return": "1"},
        )

        assert response.status_code == 302
        inspection.refresh_from_db()
        assert (inspection.status, inspection.outcome) == ("DONE", "ACCEPTED")
        assert inspection.notes == "One torn"
        assert inspection.completed_by.email == "staff@test.com"
        assert self._stock(inspection) == Decimal("2.00")

    def test_reject_button_restocks_nothing(self, staff_client, inspection):
        staff_client.post(
            f"{self.URL}{inspection.pk}/change/",
            {"restock_quantity": "3", "notes": "", "_reject_return": "1"},
        )

        inspection.refresh_from_db()
        assert inspection.outcome == "REJECTED"
        assert self._stock(inspection) == 0

    def test_too_many_units_is_refused(self, staff_client, inspection):
        response = staff_client.post(
            f"{self.URL}{inspection.pk}/change/",
            {"restock_quantity": "9", "notes": "", "_accept_return": "1"},
        )

        assert response.status_code == 200
        assert b"between 0 and the returned quantity" in response.content
        inspection.refresh_from_db()
        assert inspection.status == "PENDING"
        assert self._stock(inspection) == 0

    def test_a_decided_return_is_read_only(self, staff_client, inspection):
        inventory.accept_return(inspection, None)

        page = staff_client.get(f"{self.URL}{inspection.pk}/change/")
        staff_client.post(
            f"{self.URL}{inspection.pk}/change/",
            {"restock_quantity": "1", "notes": "edited", "_accept_return": "1"},
        )

        assert b"Accept and restock" not in page.content
        readonly = set(page.context["adminform"].readonly_fields)
        assert {"restock_quantity", "notes"} <= readonly
        inspection.refresh_from_db()
        assert (inspection.restock_quantity, inspection.notes) == (3, "")
        assert self._stock(inspection) == Decimal("3.00")

    def test_save_notes_keeps_the_return_pending(self, staff_client, inspection):
        staff_client.post(
            f"{self.URL}{inspection.pk}/change/",
            {"restock_quantity": "1", "notes": "Waiting on courier", "_continue": "1"},
        )

        inspection.refresh_from_db()
        assert (inspection.status, inspection.notes) == (
            "PENDING",
            "Waiting on courier",
        )
        assert self._stock(inspection) == 0

    def test_a_stale_page_cannot_decide_twice(self, staff_client, inspection):
        # Accepted in another tab after this page was opened.
        inventory.accept_return(inspection, None)

        response = staff_client.post(
            f"{self.URL}{inspection.pk}/change/", {"_reject_return": "1"}, follow=True
        )

        assert b"This return was already decided." in response.content
        inspection.refresh_from_db()
        assert inspection.outcome == "ACCEPTED"
        assert self._stock(inspection) == Decimal("3.00")

    def test_list_reject_action(self, staff_client, inspection):
        staff_client.post(
            self.URL,
            {"action": "reject_selected", "_selected_action": [str(inspection.pk)]},
        )

        inspection.refresh_from_db()
        assert inspection.outcome == "REJECTED"
        assert self._stock(inspection) == 0

    def test_list_actions_accept_and_reject(self, staff_client, inspection):
        response = staff_client.post(
            self.URL,
            {"action": "accept_selected", "_selected_action": [str(inspection.pk)]},
        )

        assert response.status_code == 302
        inspection.refresh_from_db()
        assert inspection.outcome == "ACCEPTED"
        assert self._stock(inspection) == Decimal("3.00")

    def test_inspections_are_not_made_or_deleted_by_hand(
        self, staff_client, inspection
    ):
        assert staff_client.get(f"{self.URL}add/").status_code == 403
        assert staff_client.get(f"{self.URL}{inspection.pk}/delete/").status_code == 403

    def test_needs_staff(self, client, inspection):
        client.force_login(User.objects.create_user(email="u@test.com", password="x"))

        response = client.post(
            f"{self.URL}{inspection.pk}/change/",
            {"restock_quantity": "3", "notes": "", "_accept_return": "1"},
        )

        assert response.status_code == 302
        inspection.refresh_from_db()
        assert inspection.status == "PENDING"


@pytest.mark.django_db
class TestImportAdmin:
    URL = "/admin/orders/order/import/"

    def _post(self, client, org, data, name="ventas.xlsx", follow=False):
        upload = io.BytesIO(data)
        upload.name = name
        return client.post(
            self.URL,
            {"org": str(org.id), "channel": "MERCADOLIBRE", "report": upload},
            follow=follow,
        )

    def test_the_orders_list_links_to_the_import(self, staff_client):
        response = staff_client.get("/admin/orders/order/")

        assert self.URL.encode() in response.content

    def test_the_form_offers_the_channels_with_an_importer(self, staff_client, org_a):
        response = staff_client.get(self.URL)

        assert response.status_code == 200
        choices = dict(response.context["form"].fields["channel"].choices)
        assert choices == {"MERCADOLIBRE": "Mercado Libre"}

    def test_an_import_keeps_its_run_and_shows_it(self, staff_client, org_a):
        response = self._post(
            staff_client, org_a, report(sale("S1"), sale("S2", units=0)), follow=True
        )

        run_ = ImportRun.objects.get(org=org_a)
        assert response.redirect_chain[-1][0] == (
            f"/admin/orders/importrun/{run_.pk}/change/"
        )
        assert Order.objects.filter(org=org_a, external_id="S1").exists()
        assert (run_.source, run_.outcome, run_.channel) == (
            "ADMIN",
            "WITH_ISSUES",
            "MERCADOLIBRE",
        )
        assert run_.created_by.email == "staff@test.com"
        assert run_.counts["orders_created"] == 1
        assert run_.counts["orders_not_imported"] == 1
        content = response.content.decode()
        assert "Imported, with issues: ventas.xlsx." in content
        assert "Not imported (1)" in content
        assert "Action required (1)" in content

    def test_sales_not_imported_are_flagged_to_the_user(self, staff_client, org_a):
        response = self._post(
            staff_client, org_a, report(sale("S1", price=""), sale("S2")), follow=True
        )

        run_ = ImportRun.objects.get(org=org_a)
        content = response.content.decode()
        assert "1 sale(s) were not imported." in content
        assert (
            f'href="/admin/orders/importrunentry/?run__id__exact={run_.pk}'
            '&amp;level__exact=NOT_IMPORTED">See which, and why</a>'
        ) in content
        entries = staff_client.get(
            f"/admin/orders/importrunentry/?run__id__exact={run_.pk}"
            "&level__exact=NOT_IMPORTED"
        )
        assert b"the unit price is empty" in entries.content

    def test_a_clean_import_raises_no_flag(self, staff_client, org_a):
        response = self._post(staff_client, org_a, report(sale("S1")), follow=True)

        assert b"were not imported" not in response.content

    def test_a_file_that_cannot_be_read_imports_nothing_and_says_why(
        self, staff_client, org_a
    ):
        response = self._post(staff_client, org_a, b"not a zip", follow=True)

        run_ = ImportRun.objects.get(org=org_a)
        assert run_.outcome == "FAILED"
        assert run_.error == (
            "That .xlsx file could not be opened. Save it again, or export it as .csv."
        )
        assert not run_.entries.exists()
        assert b"That .xlsx file could not be opened." in response.content
        assert not Order.objects.exists()

    def test_an_oversized_file_is_refused(self, staff_client, org_a, monkeypatch):
        from orders import admin as orders_admin

        monkeypatch.setattr(orders_admin, "MAX_REPORT_BYTES", 10)

        response = self._post(staff_client, org_a, report(sale("S1")))

        assert b"The report exceeds the 10 MB limit." in response.content
        assert not Order.objects.exists()
        assert not ImportRun.objects.exists()

    def test_only_the_chosen_org_is_offered(self, staff_client, org_a, org_b):
        form = staff_client.get(self.URL).context["form"]
        assert list(form.fields["org"].queryset) == [org_a]

        response = self._post(staff_client, org_b, report(sale("S1")))

        assert response.status_code == 200
        assert "org" in response.context["form"].errors
        assert not Order.objects.exists()

    def test_instance_mode_imports_into_the_org_picked(
        self, staff_client, org_a, org_b
    ):
        choose_scope(staff_client, "instance")

        self._post(staff_client, org_b, report(sale("S1")))

        assert Order.objects.get(external_id="S1").org == org_b
        assert ImportRun.objects.get().org == org_b

    def test_needs_permission_to_add_orders(self, client, org_a):
        user = User.objects.create_user(email="s@test.com", password="x")
        user.is_staff = True
        user.save()
        Profile.objects.create(user=user, org=org_a, role="ADMIN")
        client.force_login(user)
        choose_scope(client, org_a)

        response = self._post(client, org_a, report(sale("S1")))

        assert response.status_code == 403
        assert not Order.objects.exists()

    def test_needs_staff(self, client, org_a):
        client.force_login(User.objects.create_user(email="u@test.com", password="x"))

        response = self._post(client, org_a, report(sale("S1")))

        assert response.status_code == 302
        assert not Order.objects.exists()


@pytest.mark.django_db
class TestImportRuns:
    """Every import is kept with its log, from the admin or the command."""

    def test_the_command_keeps_its_run(self, org_a, tmp_path, capsys):
        path = tmp_path / "ventas.xlsx"
        data = report(sale("S1"))
        path.write_bytes(data)

        call_command(
            "import_channel_orders", str(path), org=org_a.name, channel="MERCADOLIBRE"
        )

        run_ = ImportRun.objects.get(org=org_a)
        assert (run_.source, run_.outcome, run_.file_name) == (
            "COMMAND",
            "SUCCESS",
            "ventas.xlsx",
        )
        assert run_.file_size == len(data)
        assert run_.file_sha256 == hashlib.sha256(data).hexdigest()
        assert run_.started_at <= run_.finished_at
        assert f"Import run {run_.pk}: Imported." in capsys.readouterr().out
        levels = list(run_.entries.values_list("level", flat=True))
        assert levels.count("CHANGE") == 3
        assert levels.count("ACTION_REQUIRED") == 1

    def test_a_failed_command_run_is_kept_too(self, org_a, tmp_path):
        path = tmp_path / "ventas.xlsx"
        path.write_bytes(b"not a zip")

        with pytest.raises(CommandError, match="could not be opened"):
            call_command(
                "import_channel_orders",
                str(path),
                org=org_a.name,
                channel="MERCADOLIBRE",
            )

        assert ImportRun.objects.get(org=org_a).outcome == "FAILED"

    def test_the_log_keeps_its_order_and_sales(self, org_a):
        run_, result = record_import(
            org_a, "MERCADOLIBRE", "v.xlsx", report(sale("S1"), sale("S2")), "ADMIN"
        )

        stored = list(run_.entries.values_list("level", "sale", "message"))
        assert stored == [(e.level, e.sale, e.message) for e in result.entries]
        assert [p for p in run_.entries.values_list("position", flat=True)] == list(
            range(len(stored))
        )

    @pytest.mark.parametrize(
        ("rows", "outcome"),
        [
            ((sale("S1"),), "SUCCESS"),
            ((sale("S1", date="ayer"),), "WITH_ISSUES"),
            ((sale("S1", units=0),), "WITH_ISSUES"),
        ],
    )
    def test_outcome(self, org_a, rows, outcome):
        run_, _result = record_import(
            org_a, "MERCADOLIBRE", "v.xlsx", report(*rows), "ADMIN"
        )

        assert run_.outcome == outcome

    def test_the_run_page_links_to_its_log_by_level(self, staff_client, org_a):
        run_, _ = record_import(
            org_a,
            "MERCADOLIBRE",
            "v.xlsx",
            report(sale("S1"), sale("S2", units=0)),
            "ADMIN",
        )

        page = staff_client.get(f"/admin/orders/importrun/{run_.pk}/change/")
        entries = staff_client.get(
            f"/admin/orders/importrunentry/?run__id__exact={run_.pk}"
            "&level__exact=NOT_IMPORTED"
        )

        assert page.status_code == 200
        assert f"?run__id__exact={run_.pk}&amp;level__exact=NOT_IMPORTED".encode() in (
            page.content
        )
        assert found(entries) == 1
        assert b"the quantity must be above 0" in entries.content

    def test_runs_are_read_only(self, staff_client, org_a):
        run_, _ = record_import(
            org_a, "MERCADOLIBRE", "v.xlsx", report(sale("S1")), "ADMIN"
        )

        assert staff_client.get("/admin/orders/importrun/add/").status_code == 403
        assert (
            staff_client.get(f"/admin/orders/importrun/{run_.pk}/delete/").status_code
            == 403
        )
        staff_client.post(
            f"/admin/orders/importrun/{run_.pk}/change/", {"file_name": "other"}
        )
        run_.refresh_from_db()
        assert run_.file_name == "v.xlsx"

    def test_another_orgs_runs_are_not_shown(self, staff_client, org_a, org_b):
        theirs, _ = record_import(
            org_b, "MERCADOLIBRE", "v.xlsx", report(sale("S1")), "ADMIN"
        )

        assert found(staff_client.get("/admin/orders/importrun/")) == 0
        assert (
            staff_client.get(f"/admin/orders/importrun/{theirs.pk}/change/").status_code
            == 302
        )
        assert found(staff_client.get("/admin/orders/importrunentry/")) == 0


def _friday_orders(org):
    """Three Mercado Libre sales and one Amazon sale, imported Friday rules."""
    product = listing_product(org, availability_days=3, stock_quantity=0)
    run(
        org,
        sale("OLD", date="10 de noviembre de 2026 10:00 hs."),
        sale("S1", date="13 de noviembre de 2026 10:00 hs."),
        sale("S2", date="13 de noviembre de 2026 10:00 hs.", status="Entregado"),
    )
    amazon_sale(org, product)
    return product


@pytest.mark.django_db
class TestRecalculateDueDates:
    URL = "/admin/orders/order/recalculate/"

    def _post(self, client, org, channel="MERCADOLIBRE", from_date="2026-11-12"):
        return client.post(
            self.URL,
            {"org": str(org.pk), "channel": channel, "from_date": from_date},
            follow=True,
        )

    def test_the_orders_list_links_to_it(self, staff_client):
        response = staff_client.get("/admin/orders/order/")

        assert self.URL.encode() in response.content

    def test_recalculates_the_channels_orders_from_the_date(self, staff_client, org_a):
        _friday_orders(org_a)
        ChannelPreference.objects.update_or_create(
            channel="MERCADOLIBRE", defaults={"availability_day_count": "WORKING"}
        )

        response = self._post(staff_client, org_a)

        # Friday plus 3 working days is Wednesday.
        wednesday = datetime.date(2026, 11, 18)
        assert item(org_a, "S1").due_date == wednesday
        assert Order.objects.get(external_id="S1").due_date == wednesday
        assert item(org_a, "S2").due_date == wednesday
        assert b"Recalculated 2 Mercado Libre order(s) from 2026-11-12: 2 changed." in (
            response.content
        )

    def test_delivered_and_cancelled_orders_are_recalculated_too(
        self, staff_client, org_a
    ):
        """Their due dates stay relevant: penalties for late delivery (a
        future module) are computed from them, so they must follow the
        rules like any other order's."""
        listing_product(org_a, availability_days=3)
        friday = "13 de noviembre de 2026 10:00 hs."
        run(
            org_a,
            sale("D1", date=friday, status="Entregado"),
            sale("C1", date=friday, status="Cancelada por el comprador"),
        )
        ChannelPreference.objects.update_or_create(
            channel="MERCADOLIBRE", defaults={"availability_day_count": "WORKING"}
        )

        self._post(staff_client, org_a)

        wednesday = datetime.date(2026, 11, 18)
        assert item(org_a, "D1").due_date == wednesday
        assert item(org_a, "C1").due_date == wednesday
        assert Order.objects.get(external_id="D1").due_date == wednesday
        assert Order.objects.get(external_id="C1").due_date == wednesday

    def test_open_work_orders_follow_the_new_date(self, staff_client, org_a):
        _friday_orders(org_a)
        ChannelPreference.objects.update_or_create(
            channel="MERCADOLIBRE", defaults={"availability_day_count": "WORKING"}
        )

        self._post(staff_client, org_a)

        work = WorkOrder.objects.filter(org=org_a, order__external_id="S1")
        assert {wo.due_date for wo in work} == {datetime.date(2026, 11, 18)}

    def test_finished_work_orders_keep_their_date(self, staff_client, org_a):
        _friday_orders(org_a)
        preparation = WorkOrder.objects.get(
            org=org_a, order__external_id="S1", kind="PREPARATION"
        )
        inventory.complete_work_order(preparation)
        ChannelPreference.objects.update_or_create(
            channel="MERCADOLIBRE", defaults={"availability_day_count": "WORKING"}
        )

        self._post(staff_client, org_a)

        preparation.refresh_from_db()
        assert preparation.due_date == datetime.date(2026, 11, 16)

    def test_a_new_availability_time_is_applied(self, staff_client, org_a):
        product = _friday_orders(org_a)
        product.availability_days = 10
        product.save()

        self._post(staff_client, org_a)

        assert item(org_a, "S1").due_date == datetime.date(2026, 11, 23)

    def test_orders_before_the_date_other_channels_and_orgs_are_untouched(
        self, staff_client, org_a, org_b
    ):
        _friday_orders(org_a)
        other = listing_product(org_b, availability_days=3)
        run(org_b, sale("S1", date="13 de noviembre de 2026 10:00 hs."))
        ChannelPreference.objects.update_or_create(
            channel="MERCADOLIBRE", defaults={"availability_day_count": "WORKING"}
        )

        self._post(staff_client, org_a)

        assert item(org_a, "OLD").due_date == datetime.date(2026, 11, 13)
        assert item(org_a, "AMZ-1-1").due_date == datetime.date(2026, 11, 18)
        theirs = OrderLineItem.objects.get(org=org_b, external_id="S1")
        assert theirs.due_date == datetime.date(2026, 11, 16)
        assert other.org == org_b

    def test_nothing_to_change_says_so(self, staff_client, org_a):
        _friday_orders(org_a)

        response = self._post(staff_client, org_a)

        assert b"Recalculated 2 Mercado Libre order(s) from 2026-11-12: 0 changed." in (
            response.content
        )

    def test_a_future_date_is_refused(self, staff_client, org_a):
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)

        response = self._post(staff_client, org_a, from_date=tomorrow.isoformat())

        assert response.context["form"].errors["from_date"] == [
            "The date cannot be in the future."
        ]

    def test_a_malformed_date_is_refused(self, staff_client, org_a):
        response = self._post(staff_client, org_a, from_date="13/13/2026")

        assert "from_date" in response.context["form"].errors

    def test_another_org_is_refused(self, staff_client, org_a, org_b):
        listing_product(org_b, availability_days=3)
        run(org_b, sale("S1", date="13 de noviembre de 2026 10:00 hs."))
        ChannelPreference.objects.update_or_create(
            channel="MERCADOLIBRE", defaults={"availability_day_count": "WORKING"}
        )

        response = self._post(staff_client, org_b)

        assert "org" in response.context["form"].errors
        theirs = OrderLineItem.objects.get(org=org_b, external_id="S1")
        assert theirs.due_date == datetime.date(2026, 11, 16)

    def test_needs_permission_to_change_orders(self, client, org_a):
        user = User.objects.create_user(email="s@test.com", password="x")
        user.is_staff = True
        user.save()
        Profile.objects.create(user=user, org=org_a, role="ADMIN")
        client.force_login(user)
        choose_scope(client, org_a)

        response = client.post(
            self.URL,
            {
                "org": str(org_a.pk),
                "channel": "MERCADOLIBRE",
                "from_date": "2026-11-12",
            },
        )

        assert response.status_code == 403

    def test_needs_staff(self, client, org_a):
        client.force_login(User.objects.create_user(email="u@test.com", password="x"))

        response = client.get(self.URL)

        assert response.status_code == 302
        assert response.url.startswith("/admin/login/")


@pytest.mark.django_db
class TestChannelPreferenceAdmin:
    URL = "/admin/orders/channelpreference/"

    def test_lists_each_channel(self, staff_client):
        for channel, day_count in DEFAULT_DAY_COUNT.items():
            ChannelPreference.objects.get_or_create(
                channel=channel, defaults={"availability_day_count": day_count}
            )

        assert found(staff_client.get(self.URL)) == 3

    def test_an_admin_changes_how_a_channel_counts(self, staff_client, org_a):
        preference, _ = ChannelPreference.objects.get_or_create(
            channel="MERCADOLIBRE", defaults={"availability_day_count": "CALENDAR"}
        )

        response = staff_client.post(
            f"{self.URL}{preference.pk}/change/",
            {"channel": "MERCADOLIBRE", "availability_day_count": "WORKING"},
        )

        assert response.status_code == 302
        assert ChannelPreference.day_count("MERCADOLIBRE") == "WORKING"

    def test_preferences_cannot_be_deleted(self, staff_client):
        preference, _ = ChannelPreference.objects.get_or_create(
            channel="AMAZON", defaults={"availability_day_count": "WORKING"}
        )

        response = staff_client.get(f"{self.URL}{preference.pk}/delete/")

        assert response.status_code == 403
