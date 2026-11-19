"""The one write path for attribute values and variant identity.

The API and the importer both go through here, so a value is typed the same
way whichever door it came in by, and a variant's `variant_key` and name are
always recomputed from its axis values.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction

from catalog.models import (
    Attribute,
    AttributeOption,
    AttributeValue,
    CategoryAttribute,
)
from catalog.text import UNIQUE_SIZE, canonical_size, normalize_key

TRUE_WORDS = {"si", "yes", "true", "1"}
FALSE_WORDS = {"no", "false", "0"}
MAX_TEXT = 500


class CatalogError(ValueError):
    """A refusal whose message is safe to show, keyed by the offending field."""

    def __init__(self, field, message, *, existing=None):
        super().__init__(message)
        self.field = field
        self.message = message
        # For a clash: the sibling variant that already has these values.
        self.existing = existing

    def as_errors(self):
        return {self.field: [self.message]}


def find_option(attribute, raw):
    """The option of `attribute` that `raw` names, by value or alias."""
    key = normalize_key(raw)
    for option in attribute.options.all():
        if option.normalized == key or key in (option.aliases or []):
            return option
    return None


def get_or_create_option(attribute, raw, *, sort_order=0):
    option = find_option(attribute, raw)
    if option is None:
        option = AttributeOption.objects.create(
            org_id=attribute.org_id,
            attribute=attribute,
            value=str(raw).strip()[:150],
            sort_order=sort_order,
        )
        # Keep a prefetched options cache in step with the new row.
        cache = getattr(attribute, "_prefetched_objects_cache", {})
        if "options" in cache:
            cache["options"]._result_cache.append(option)
    return option


def _as_list(raw):
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [item for item in raw if item not in (None, "")]
    if raw == "":
        return []
    return [raw]


def parse_boolean(raw, field):
    if isinstance(raw, bool):
        return raw
    key = normalize_key(raw)
    if key in TRUE_WORDS:
        return True
    if key in FALSE_WORDS:
        return False
    raise CatalogError(field, f"'{raw}' is not yes or no.")


def parse_number(raw, field):
    if isinstance(raw, bool):
        raise CatalogError(field, "Expected a number.")
    try:
        number = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise CatalogError(field, f"'{raw}' is not a number.") from None
    if not number.is_finite() or abs(number) >= Decimal("1e10"):
        raise CatalogError(field, f"'{raw}' is not a usable number.")
    return number


def coerce_values(attribute, raw, *, create_options=False):
    """The value columns `raw` stands for, one dict per row to store.

    `raw` is what a client or a spreadsheet cell says: a string, a number, a
    bool, a list (multi-valued option lists only) or, for a number with a
    unit, `{"value": 35, "unit": "cm"}`. An empty value yields no rows.
    """
    field = attribute.code
    items = _as_list(raw)
    if not items:
        return []
    if len(items) > 1 and not attribute.is_multivalued:
        raise CatalogError(field, "This attribute takes one value.")

    rows = []
    for item in items:
        if attribute.data_type == Attribute.OPTION:
            if isinstance(item, (dict, list, bool)):
                raise CatalogError(field, "Expected an option.")
            text = str(item).strip()
            if not text:
                continue
            if create_options:
                option = get_or_create_option(attribute, text)
            else:
                option = find_option(attribute, text)
                if option is None:
                    raise CatalogError(field, f"'{text}' is not an option of {field}.")
            rows.append({"option": option})
        elif attribute.data_type == Attribute.NUMBER:
            unit = attribute.unit
            if isinstance(item, dict):
                unit = str(item.get("unit") or unit)[:20]
                item = item.get("value")
                if item in (None, ""):
                    continue
            rows.append({"value_number": parse_number(item, field), "unit": unit})
        elif attribute.data_type == Attribute.BOOLEAN:
            rows.append({"value_boolean": parse_boolean(item, field)})
        else:
            if isinstance(item, (dict, list)):
                raise CatalogError(field, "Expected text.")
            text = str(item).strip()
            if len(text) > MAX_TEXT:
                raise CatalogError(field, f"Keep it under {MAX_TEXT} characters.")
            if text:
                rows.append({"value_text": text})

    seen, unique_rows = set(), []
    for row in rows:
        marker = row.get("option") or tuple(sorted(row.items()))
        if marker not in seen:
            seen.add(marker)
            unique_rows.append(row)
    return unique_rows


def set_values(product, variant, attribute, rows):
    """Replace what `product` (or its `variant`) says for `attribute`."""
    set_many(product, variant, {attribute: rows})


def set_many(product, variant, values, *, clear=(), fresh=False):
    """Replace what `product` (or its `variant`) says for every attribute in
    `values` (`{attribute: rows}`), and drop what it says for those in
    `clear`: one delete and one insert, whatever the number of attributes.
    `fresh` says the owner was just created, so there is nothing to delete."""
    attributes = {*values, *clear}
    if attributes and not fresh:
        AttributeValue.objects.filter(
            product=product, variant=variant, attribute__in=attributes
        ).delete()
    AttributeValue.objects.bulk_create(
        AttributeValue(
            org_id=product.org_id,
            product=product,
            variant=variant,
            attribute=attribute,
            **row,
        )
        for attribute, rows in values.items()
        for row in rows
    )


def attributes_by_code(org, codes):
    found = {
        a.code: a
        for a in Attribute.objects.filter(
            org=org, code__in=list(codes)
        ).prefetch_related("options")
    }
    for code in codes:
        if code not in found:
            raise CatalogError("attributes", f"There is no attribute '{code}'.")
    return found


def apply_attributes(product, variant, payload, *, create_options=False):
    """Write `{code: raw}` onto `product`, or onto `variant` when given. A code
    whose value is empty has its rows removed: nothing is stored for an
    attribute a product does not have."""
    if not isinstance(payload, dict):
        raise CatalogError("attributes", "Send an object keyed by attribute code.")
    attributes = attributes_by_code(product.org, payload.keys())
    for code, raw in payload.items():
        attribute = attributes[code]
        rows = coerce_values(attribute, raw, create_options=create_options)
        set_values(product, variant, attribute, rows)


def axis_links(category):
    return list(
        CategoryAttribute.objects.filter(
            category=category, role=CategoryAttribute.VARIANT_AXIS
        )
        .select_related("attribute")
        .order_by("sort_order", "attribute__code")
    )


def row_display(row):
    """How one stored row reads, for names and keys."""
    if row.get("option") is not None:
        return row["option"].value
    if row.get("value_boolean") is not None:
        return "Sí" if row["value_boolean"] else "No"
    if row.get("value_number") is not None:
        text = f"{row['value_number'].normalize():f}"
        return f"{text} {row.get('unit', '')}".strip()
    return row.get("value_text", "")


def _stored_rows(variant, attributes):
    rows = {}
    if variant is None or variant.pk is None:
        return rows
    for value in AttributeValue.objects.filter(
        variant=variant, attribute__in=attributes
    ).select_related("option"):
        rows.setdefault(value.attribute_id, []).append(
            {
                "option": value.option,
                "value_text": value.value_text,
                "value_number": value.value_number,
                "value_boolean": value.value_boolean,
                "unit": value.unit,
            }
        )
    return rows


def variant_identity(product, links, axis_rows, size):
    """`(variant_key, name)` for a variant of `product` in `size` whose other
    axis values are `axis_rows` (`{attribute_id: [row, ...]}`), in the
    category's axis order. The name leaves out the default "Unique" size."""
    key_parts = [f"size={normalize_key(size)}"]
    labels = [] if size == UNIQUE_SIZE else [size]
    for link in links:
        rows = axis_rows.get(link.attribute_id) or []
        if not rows:
            continue
        shown = sorted(row_display(row) for row in rows)
        key_parts.append(
            f"{link.attribute.code}=" + ",".join(normalize_key(s) for s in shown)
        )
        labels.append(" ".join(shown))
    suffix = f" ({' / '.join(labels)})" if labels else ""
    name = (product.name[: max(0, 255 - len(suffix))] + suffix)[:255]
    return "|".join(key_parts)[:500], name


def save_variant(
    product,
    variant,
    attributes,
    fields,
    *,
    create_options=False,
    require_axes=True,
    known=None,
    links=None,
):
    """Create (`variant` None) or update one variant of `product`.

    `attributes` is `{code: raw}` for the variant's own values, axes included;
    `fields` are `invoices.Product` columns (price, size, stock, ...).
    Everything is checked before the first write: unknown codes, bad values, a
    missing required axis and a clash with another variant's size and axis
    values. `known` is `{code: attribute}` the caller already holds, with
    options prefetched, and `links` the category's axis links (an import's),
    so neither is read again.
    """
    from invoices.models import Product

    if not isinstance(attributes, dict):
        raise CatalogError("attributes", "Send an object keyed by attribute code.")
    known = known or {}
    missing = [code for code in attributes if code not in known]
    by_code = {code: known[code] for code in attributes if code in known}
    if missing:
        by_code.update(attributes_by_code(product.org, missing))
    coerced = {
        by_code[code]: coerce_values(by_code[code], raw, create_options=create_options)
        for code, raw in attributes.items()
    }
    links = axis_links(product.category) if links is None else links
    axis_ids = {link.attribute_id for link in links}
    axis_rows = _stored_rows(variant, [link.attribute for link in links])
    for attribute, rows in coerced.items():
        if attribute.id in axis_ids:
            axis_rows[attribute.id] = rows
    if require_axes:
        missing = [
            link.attribute.code
            for link in links
            if link.is_required and not axis_rows.get(link.attribute_id)
        ]
        if missing:
            raise CatalogError(
                "attributes", "A variant needs a value for: " + ", ".join(missing)
            )
    if "size" in fields:
        size = canonical_size(fields["size"])
    else:
        size = variant.size if variant is not None else UNIQUE_SIZE
    key, name = variant_identity(product, links, axis_rows, size)
    clash = Product.objects.filter(catalog_product=product, variant_key=key)
    if variant is not None and variant.pk is not None:
        clash = clash.exclude(pk=variant.pk)
    if existing := clash.first():
        raise CatalogError(
            "attributes",
            "Another variant of this product has this size and these values.",
            existing=existing,
        )

    with transaction.atomic():
        fresh = variant is None
        if fresh:
            variant = Product(org_id=product.org_id, catalog_product=product)
        for field, value in fields.items():
            setattr(variant, field, value)
        variant.size = size
        variant.variant_key = key
        variant.name = name
        variant.category = product.category.name[:100]
        variant.save()
        set_many(product, variant, coerced, fresh=fresh)
    return variant


def refresh_variants(product):
    """Rebuild every variant's name and key after the product's name or an
    option's wording changes."""
    links = axis_links(product.category)
    attributes = [link.attribute for link in links]
    for variant in product.variants.all():
        key, name = variant_identity(
            product, links, _stored_rows(variant, attributes), variant.size
        )
        if (key, name) != (variant.variant_key, variant.name):
            variant.variant_key, variant.name = key, name
            variant.save(
                update_fields=["variant_key", "name", "updated_at", "updated_by"]
            )


# What sells a product: (label, plural, line item model path, document field).
_SELLERS = (
    ("deal", "deals", "opportunity.OpportunityLineItem", "opportunity"),
    ("order", "orders", "orders.OrderLineItem", "order"),
    ("invoice", "invoices", "invoices.InvoiceLineItem", "invoice"),
    ("estimate", "estimates", "invoices.EstimateLineItem", "estimate"),
    (
        "recurring invoice",
        "recurring invoices",
        "invoices.RecurringInvoiceLineItem",
        "recurring_invoice",
    ),
)


def _listed(parts):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _sales_of(variant_ids):
    """`(documents, sold)`: "1 invoice"-style counts of the records selling any
    of `variant_ids`, and the ids among them that something sells."""
    from django.apps import apps

    documents, sold = [], set()
    for single, plural, model_path, field in _SELLERS:
        lines = apps.get_model(model_path).objects.filter(
            product_id__in=list(variant_ids)
        )
        count = lines.order_by().values(field).distinct().count()
        if count:
            documents.append(f"{count} {single if count == 1 else plural}")
            sold.update(lines.order_by().values_list("product_id", flat=True))
    return documents, sold


def deletion_refusal(name, variant_ids):
    """Why a variant called `name` cannot be deleted, or None. One that a
    deal, order, invoice, estimate or recurring invoice sells is kept, so
    those records still point at what they sold; retire it instead
    (`is_active`). A variant with no sales deletes, whatever its siblings
    sold."""
    documents, _ = _sales_of(variant_ids)
    if not documents:
        return None
    return (
        f"{name} is on {_listed(documents)}, so it cannot be deleted. Retire it "
        "instead: it stays on those records and leaves the pickers."
    )


def product_deletion_refusal(product):
    """Why `product` cannot be deleted, or None: it is kept while any of its
    variants has sales. The message names those variants, since the others
    can still be deleted one by one."""
    variants = list(product.variants.order_by("size_sort", "name"))
    documents, sold = _sales_of([v.pk for v in variants])
    if not documents:
        return None
    labels = []
    for variant in variants:
        if variant.pk in sold:
            label = variant.name.removeprefix(product.name).strip(" ()")
            labels.append(label or variant.size)
    shown = labels[:5] + ([f"{len(labels) - 5} more"] if len(labels) > 5 else [])
    return (
        f"{product.name} is on {_listed(documents)}, so it cannot be deleted. "
        f"Variants with sales: {_listed(shown)}. Retire the product instead, or "
        "delete its variants that have no sales one by one."
    )


def channel_deletion_refusal(channel):
    """Why a sales channel cannot be deleted, or None: not while it publishes
    listings, invoices, orders, fulfillment sales or delivery options refer to
    it. Switching it off keeps it on those records and out of the pickers."""
    from fulfillment.models import DeliveryOption, Sale
    from invoices.models import Invoice
    from orders.models import Order

    found = []
    for single, plural, count in (
        ("listing", "listings", channel.listings.count()),
        ("invoice", "invoices", Invoice.objects.filter(sales_channel=channel).count()),
        ("order", "orders", Order.objects.filter(sales_channel=channel).count()),
        ("sale", "sales", Sale.objects.filter(channel=channel).count()),
        (
            "delivery option",
            "delivery options",
            DeliveryOption.objects.filter(channel=channel).count(),
        ),
    ):
        if count:
            found.append(f"{count} {single if count == 1 else plural}")
    if not found:
        return None
    return (
        f"{channel.name} has {_listed(found)}, so it cannot be deleted. "
        "Switch it off instead."
    )
