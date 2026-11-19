"""Finding products and variants by attribute value.

A variant has a value when it stores one itself, or when its product stores
one and the variant does not override that attribute. Filters ask it that way,
so "size 2A" finds the variant and "material Algodón" finds every variant of a
product that says so once.
"""

from django.db.models import Exists, OuterRef, Q
from rest_framework.exceptions import ValidationError

from catalog.models import Attribute, AttributeValue
from catalog.services import CatalogError, find_option, parse_boolean, parse_number
from catalog.text import canonical_size

MAX_FILTERS = 10


def parse_attribute_filters(params, org):
    """`?attr=size:2A&attr=size:3A&attr=color:Rojo` as
    `[(attribute, [value, ...]), ...]`: values of one code are alternatives,
    different codes must all hold. An unknown code is a 400, since silently
    dropping it would widen the result."""
    raw = params.getlist("attr") if hasattr(params, "getlist") else []
    if len(raw) > MAX_FILTERS:
        raise ValidationError({"attr": [f"Use at most {MAX_FILTERS} filters."]})
    wanted = {}
    for item in raw:
        code, sep, value = str(item).partition(":")
        if not sep or not code.strip() or not value.strip():
            raise ValidationError({"attr": ["Write each filter as code:value."]})
        wanted.setdefault(code.strip(), []).append(value.strip())
    if not wanted:
        return []
    attributes = {
        a.code: a
        for a in Attribute.objects.filter(
            org=org, code__in=list(wanted)
        ).prefetch_related("options")
    }
    unknown = sorted(set(wanted) - set(attributes))
    if unknown:
        raise ValidationError({"attr": [f"There is no attribute '{unknown[0]}'."]})
    return [(attributes[code], values) for code, values in wanted.items()]


def value_condition(attribute, values):
    """A `Q` on `AttributeValue` matching any of `values` for `attribute`."""
    condition = Q(pk__in=[])
    try:
        for value in values:
            if attribute.data_type == Attribute.OPTION:
                option = find_option(attribute, value)
                if option is not None:
                    condition |= Q(option=option)
            elif attribute.data_type == Attribute.BOOLEAN:
                condition |= Q(value_boolean=parse_boolean(value, attribute.code))
            elif attribute.data_type == Attribute.NUMBER:
                condition |= Q(value_number=parse_number(value, attribute.code))
            else:
                condition |= Q(value_text__iexact=value)
    except CatalogError as exc:
        raise ValidationError({"attr": [exc.message]}) from None
    return Q(attribute=attribute) & condition


def filter_variants(queryset, filters):
    """Narrow a queryset of variants (`invoices.Product`) to those matching
    every `(attribute, values)` filter, counting inherited product values."""
    for attribute, values in filters:
        match = value_condition(attribute, values)
        own = AttributeValue.objects.filter(match, variant=OuterRef("pk"))
        inherited = AttributeValue.objects.filter(
            match, product=OuterRef("catalog_product"), variant__isnull=True
        )
        overridden = AttributeValue.objects.filter(
            variant=OuterRef("pk"), attribute=attribute
        )
        queryset = queryset.filter(
            Exists(own) | (Exists(inherited) & ~Exists(overridden))
        )
    return queryset


def parse_sizes(params):
    """`?size=2A&size=3A`, each in the store's spelling, so "2 Años" is 2A."""
    raw = params.getlist("size") if hasattr(params, "getlist") else []
    if len(raw) > 50:
        raise ValidationError({"size": ["Ask for at most 50 sizes."]})
    return sorted({canonical_size(value) for value in raw if value.strip()})


def parse_in_stock(params):
    """`?in_stock=true` (stock above 0) or `false` (none left); else no filter."""
    value = params.get("in_stock")
    return {"true": True, "false": False}.get(value)


def narrow_variants(queryset, filters=(), sizes=(), in_stock=None):
    """`filter_variants`, plus the size and stock columns."""
    queryset = filter_variants(queryset, filters)
    if sizes:
        queryset = queryset.filter(size__in=sizes)
    if in_stock is True:
        queryset = queryset.filter(stock__gt=0)
    elif in_stock is False:
        queryset = queryset.filter(stock=0)
    return queryset


def filter_products(queryset, org, filters=(), sizes=(), in_stock=None):
    """Narrow a queryset of `CatalogProduct` to those with one variant that
    matches every filter at once (a product matches "2A and Rojo" only when a
    single variant is both). A product with no variants yet can match only by
    its own attribute values, and never a size or stock filter."""
    if not (filters or sizes or in_stock is not None):
        return queryset
    from invoices.models import Product

    variants = narrow_variants(
        Product.objects.filter(org=org, catalog_product__isnull=False),
        filters,
        sizes,
        in_stock,
    )
    matching = Q(pk__in=variants.values("catalog_product_id"))
    if sizes or in_stock is not None or not filters:
        return queryset.filter(matching)
    has_variant = Product.objects.filter(catalog_product=OuterRef("pk"))
    own = Q()
    for attribute, values in filters:
        own &= Q(
            Exists(
                AttributeValue.objects.filter(
                    value_condition(attribute, values),
                    product=OuterRef("pk"),
                    variant__isnull=True,
                )
            )
        )
    return queryset.filter(matching | (own & ~Exists(has_variant)))
