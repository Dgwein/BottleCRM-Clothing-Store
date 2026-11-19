"""Catalog serializers.

Every id a client sends is looked up inside `context["org"]` only (`OrgPK`),
so another org's id is refused with a 400 exactly like an id that does not
exist. `org` itself is never a field: the views pass it to `save()`.
"""

from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from catalog.models import (
    Attribute,
    AttributeOption,
    CatalogProduct,
    Category,
    CategoryAttribute,
    ChannelAttribute,
    ImportRun,
    Listing,
    ListingVariant,
    SalesChannel,
)
from invoices.models import Product

PACKAGE = {"max_digits": 8, "decimal_places": 2, "min_value": 0, "allow_null": True}


class OrgPK(serializers.PrimaryKeyRelatedField):
    """A primary key resolved among the request org's rows only."""

    def __init__(self, model, **kwargs):
        self.model = model
        super().__init__(queryset=model.objects.none(), **kwargs)

    def get_queryset(self):
        org = self.context.get("org")
        if org is None:
            return self.model.objects.none()
        return self.model.objects.filter(org=org)


# Reads ------------------------------------------------------------------------


class SalesChannelSerializer(serializers.ModelSerializer):
    """An online channel has a market id and no address; a physical store the
    other way round. A channel that publishes listings stays online."""

    class Meta:
        model = SalesChannel
        fields = (
            "id",
            "name",
            "code",
            "kind",
            "site",
            "address_line",
            "city",
            "state",
            "postcode",
            "country",
            "phone",
            "is_active",
            "late_penalty",
            "created_at",
        )
        read_only_fields = ("id", "created_at")

    def validate(self, attrs):
        def final(name):
            return attrs.get(name, getattr(self.instance, name, "") or "")

        kind = final("kind") or SalesChannel.ONLINE
        if kind == SalesChannel.STORE:
            if final("site"):
                raise serializers.ValidationError(
                    {"site": ["A physical store has no market id."]}
                )
            instance = self.instance
            if instance is not None and instance.kind == SalesChannel.ONLINE:
                if instance.listings.exists() or instance.attribute_mappings.exists():
                    raise serializers.ValidationError(
                        {
                            "kind": [
                                "This channel has listings or attribute codes, "
                                "so it stays online."
                            ]
                        }
                    )
        else:
            given = [f for f in SalesChannel.ADDRESS_FIELDS if final(f)]
            if given:
                raise serializers.ValidationError(
                    {given[0]: ["Only a physical store has an address or phone."]}
                )
        return attrs


class SalesChannelRefSerializer(serializers.ModelSerializer):
    """A channel as a sale records it: enough to name it."""

    class Meta:
        model = SalesChannel
        fields = ("id", "name", "kind")
        read_only_fields = fields


class AttributeOptionSerializer(serializers.ModelSerializer):
    aliases = serializers.ListField(
        child=serializers.CharField(max_length=150), required=False
    )

    class Meta:
        model = AttributeOption
        fields = ("id", "value", "aliases", "sort_order")
        read_only_fields = ("id",)

    def validate_aliases(self, value):
        from catalog.text import normalize_key

        return sorted({normalize_key(v) for v in value if v.strip()})[:50]


class ChannelAttributeSerializer(serializers.ModelSerializer):
    channel_code = serializers.CharField(source="channel.code", read_only=True)

    class Meta:
        model = ChannelAttribute
        fields = ("id", "channel", "channel_code", "external_code", "external_domain")
        read_only_fields = fields


class AttributeSerializer(serializers.ModelSerializer):
    options = AttributeOptionSerializer(many=True, read_only=True)
    channel_codes = ChannelAttributeSerializer(
        source="channel_mappings", many=True, read_only=True
    )

    class Meta:
        model = Attribute
        fields = (
            "id",
            "code",
            "name",
            "data_type",
            "is_multivalued",
            "unit",
            "options",
            "channel_codes",
        )
        read_only_fields = ("id", "options", "channel_codes")

    def validate(self, attrs):
        data_type = attrs.get("data_type", getattr(self.instance, "data_type", None))
        multi = attrs.get(
            "is_multivalued", getattr(self.instance, "is_multivalued", False)
        )
        if multi and data_type != Attribute.OPTION:
            raise serializers.ValidationError(
                {"is_multivalued": ["Only option lists take several values."]}
            )
        if (
            self.instance is not None
            and "data_type" in attrs
            and attrs["data_type"] != self.instance.data_type
            and self.instance.values.exists()
        ):
            raise serializers.ValidationError(
                {"data_type": ["Products already use this attribute."]}
            )
        return attrs

    def validate_code(self, value):
        org = self.context["org"]
        clash = Attribute.objects.filter(org=org, code=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("Another attribute has this code.")
        return value


class CategoryAttributeSerializer(serializers.ModelSerializer):
    attribute = OrgPK(Attribute)
    code = serializers.CharField(source="attribute.code", read_only=True)
    name = serializers.CharField(source="attribute.name", read_only=True)
    data_type = serializers.CharField(source="attribute.data_type", read_only=True)

    class Meta:
        model = CategoryAttribute
        fields = (
            "id",
            "attribute",
            "code",
            "name",
            "data_type",
            "role",
            "is_required",
            "sort_order",
        )
        read_only_fields = ("id", "code", "name", "data_type")


class CategorySerializer(serializers.ModelSerializer):
    parent = OrgPK(Category, allow_null=True, required=False)
    attributes = CategoryAttributeSerializer(
        source="attribute_links", many=True, read_only=True
    )
    product_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ("id", "name", "parent", "attributes", "product_count")
        read_only_fields = ("id", "attributes", "product_count")

    def get_product_count(self, obj) -> int:
        annotated = getattr(obj, "product_count", None)
        return annotated if annotated is not None else obj.products.count()

    def validate_name(self, value):
        clash = Category.objects.filter(org=self.context["org"], name=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("Another category has this name.")
        return value

    def validate_parent(self, value):
        node = value
        while node is not None and self.instance is not None:
            if node.pk == self.instance.pk:
                raise serializers.ValidationError(
                    "A category cannot sit inside itself."
                )
            node = node.parent
        return value


class CategoryRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name")
        read_only_fields = fields


class AttributeValueSerializer(serializers.Serializer):
    """Every stored row of one attribute, folded into one entry. `value` is a
    string, number or bool, or a list of strings for a multi-valued list."""

    code = serializers.CharField()
    name = serializers.CharField()
    data_type = serializers.CharField()
    value = serializers.JSONField()
    unit = serializers.CharField()


def fold_values(rows):
    """`AttributeValue` rows (one owner) as `AttributeValueSerializer` dicts."""
    folded = {}
    for row in rows:
        attribute = row.attribute
        if row.option_id:
            item = row.option.value
        elif row.value_boolean is not None:
            item = row.value_boolean
        elif row.value_number is not None:
            number = row.value_number.normalize()
            item = int(number) if number == number.to_integral() else float(number)
        else:
            item = row.value_text
        entry = folded.setdefault(
            attribute.code,
            {
                "code": attribute.code,
                "name": attribute.name,
                "data_type": attribute.data_type,
                "value": [] if attribute.is_multivalued else None,
                "unit": row.unit,
            },
        )
        if attribute.is_multivalued:
            entry["value"].append(item)
        else:
            entry["value"] = item
    return sorted(folded.values(), key=lambda e: e["name"])


class VariantListingSerializer(serializers.ModelSerializer):
    """Where a variant is published, under the channel's ids."""

    listing = serializers.UUIDField(source="listing_id", read_only=True)
    channel = serializers.CharField(source="listing.channel.code", read_only=True)
    listing_external_id = serializers.CharField(
        source="listing.external_id", read_only=True
    )
    listing_status = serializers.CharField(source="listing.status", read_only=True)

    class Meta:
        model = ListingVariant
        fields = (
            "id",
            "listing",
            "channel",
            "listing_external_id",
            "listing_status",
            "external_variation_id",
            "external_product_id",
            "external_sku",
        )
        read_only_fields = fields


class CatalogVariantSerializer(serializers.ModelSerializer):
    """One variant: its size and stock, its other axis values, its own
    characteristics, its one price and the listings that publish it."""

    product = serializers.UUIDField(source="catalog_product_id", read_only=True)
    axes = serializers.SerializerMethodField()
    attributes = serializers.SerializerMethodField()
    listings = VariantListingSerializer(
        source="listing_variants", many=True, read_only=True
    )

    class Meta:
        model = Product
        fields = (
            "id",
            "product",
            "name",
            "size",
            "stock",
            "price",
            "currency",
            "sku",
            "gtin",
            "is_active",
            "axes",
            "attributes",
            "package_height_cm",
            "package_width_cm",
            "package_depth_cm",
            "package_weight_kg",
            "listings",
        )
        read_only_fields = fields

    def _axis_codes(self, obj):
        """The axis codes of the variant's category. Views pass them for every
        category in `context["axes_by_category"]` so a list costs no query
        per row."""
        by_category = self.context.get("axes_by_category")
        if not obj.catalog_product_id:
            return set()
        category_id = obj.catalog_product.category_id
        if by_category is None or category_id not in by_category:
            return set(
                CategoryAttribute.objects.filter(
                    category_id=category_id, role=CategoryAttribute.VARIANT_AXIS
                ).values_list("attribute__code", flat=True)
            )
        return by_category[category_id]

    @extend_schema_field({"type": "object", "additionalProperties": {"type": "string"}})
    def get_axes(self, obj):
        axis_codes = self._axis_codes(obj)
        axes = {}
        for row in obj.attribute_values.all():
            code = row.attribute.code
            if code in axis_codes:
                shown = row.display()
                axes[code] = f"{axes[code]}, {shown}" if code in axes else shown
        return axes

    @extend_schema_field(AttributeValueSerializer(many=True))
    def get_attributes(self, obj):
        axis_codes = self._axis_codes(obj)
        return fold_values(
            row
            for row in obj.attribute_values.all()
            if row.attribute.code not in axis_codes
        )


def sizes_in_order(variants):
    """The distinct sizes of `variants`, youngest first."""
    seen = {}
    for variant in variants:
        seen.setdefault(variant.size, variant.size_sort)
    return sorted(seen, key=lambda size: (seen[size], size))


def axes_by_category(category_ids):
    """`{category_id: {axis code, ...}}`, the context `CatalogVariantSerializer` reads."""
    found = {category_id: set() for category_id in category_ids}
    for category_id, code in CategoryAttribute.objects.filter(
        category_id__in=list(found), role=CategoryAttribute.VARIANT_AXIS
    ).values_list("category_id", "attribute__code"):
        found[category_id].add(code)
    return found


class ListingSerializer(serializers.ModelSerializer):
    channel_code = serializers.CharField(source="channel.code", read_only=True)
    channel_name = serializers.CharField(source="channel.name", read_only=True)
    status = serializers.CharField(read_only=True)
    condition = serializers.CharField(read_only=True)
    variant_count = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = (
            "id",
            "channel",
            "channel_code",
            "channel_name",
            "product",
            "external_id",
            "external_group_id",
            "external_category_id",
            "external_domain",
            "title",
            "status",
            "condition",
            "listing_type",
            "shipping_mode",
            "free_shipping",
            "warranty",
            "handling_days",
            "variant_count",
        )
        read_only_fields = fields

    def get_variant_count(self, obj) -> int:
        annotated = getattr(obj, "variant_count", None)
        return annotated if annotated is not None else obj.variants.count()


class AxisSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    is_required = serializers.BooleanField()
    options = serializers.ListField(child=serializers.CharField())


class CatalogProductListSerializer(serializers.ModelSerializer):
    category = CategoryRefSerializer(read_only=True)
    variant_count = serializers.IntegerField(read_only=True)
    active_variant_count = serializers.IntegerField(read_only=True)
    stock = serializers.IntegerField(read_only=True)
    sizes = serializers.SerializerMethodField()
    listing_count = serializers.IntegerField(read_only=True)
    price_min = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True, allow_null=True
    )
    price_max = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True, allow_null=True
    )
    currency = serializers.CharField(read_only=True, allow_null=True)

    class Meta:
        model = CatalogProduct
        fields = (
            "id",
            "name",
            "category",
            "is_active",
            "variant_count",
            "active_variant_count",
            "stock",
            "sizes",
            "listing_count",
            "price_min",
            "price_max",
            "currency",
            "created_at",
        )
        read_only_fields = fields

    def get_sizes(self, obj) -> list[str]:
        return self.context.get("sizes_by_product", {}).get(obj.pk, [])


class CatalogProductSerializer(serializers.ModelSerializer):
    category = CategoryRefSerializer(read_only=True)
    sizes = serializers.SerializerMethodField()
    axes = serializers.SerializerMethodField()
    attributes = serializers.SerializerMethodField()
    variants = serializers.SerializerMethodField()
    listings = ListingSerializer(many=True, read_only=True)

    class Meta:
        model = CatalogProduct
        fields = (
            "id",
            "name",
            "description",
            "category",
            "is_active",
            "sizes",
            "axes",
            "attributes",
            "variants",
            "listings",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def _links(self, obj):
        if not hasattr(self, "_axis_links"):
            self._axis_links = list(
                CategoryAttribute.objects.filter(
                    category_id=obj.category_id, role=CategoryAttribute.VARIANT_AXIS
                )
                .select_related("attribute")
                .order_by("sort_order", "attribute__code")
            )
        return self._axis_links

    def get_sizes(self, obj) -> list[str]:
        return sizes_in_order(obj.variants.all())

    @extend_schema_field(AxisSerializer(many=True))
    def get_axes(self, obj):
        links = self._links(obj)
        used = {}
        for variant in obj.variants.all():
            for row in variant.attribute_values.all():
                used.setdefault(row.attribute_id, {})[row.display()] = (
                    row.option.sort_order if row.option_id else 0
                )
        return [
            {
                "code": link.attribute.code,
                "name": link.attribute.name,
                "is_required": link.is_required,
                "options": sorted(
                    used.get(link.attribute_id, {}),
                    key=lambda v, seen=used.get(link.attribute_id, {}): (seen[v], v),
                ),
            }
            for link in links
            if link.attribute_id in used or link.is_required
        ]

    @extend_schema_field(AttributeValueSerializer(many=True))
    def get_attributes(self, obj):
        return fold_values(
            row for row in obj.attribute_values.all() if row.variant_id is None
        )

    @extend_schema_field(CatalogVariantSerializer(many=True))
    def get_variants(self, obj):
        codes = {link.attribute.code for link in self._links(obj)}
        return CatalogVariantSerializer(
            obj.variants.all(),
            many=True,
            context={"axes_by_category": {obj.category_id: codes}},
        ).data


# Writes -----------------------------------------------------------------------


class AttributesPayloadField(serializers.JSONField):
    """`{attribute_code: value}`. Values are checked against each attribute's
    type in `catalog.services`; a null or empty value removes the attribute."""

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not isinstance(value, dict):
            raise serializers.ValidationError("Send an object keyed by attribute code.")
        if len(value) > 100:
            raise serializers.ValidationError("Send at most 100 attributes at once.")
        return value


class CatalogProductWriteSerializer(serializers.ModelSerializer):
    category = OrgPK(Category)
    attributes = AttributesPayloadField(required=False, write_only=True)

    class Meta:
        model = CatalogProduct
        fields = ("name", "description", "category", "is_active", "attributes")

    def validate_category(self, value):
        if (
            self.instance is not None
            and value.pk != self.instance.category_id
            and self.instance.variants.exists()
        ):
            raise serializers.ValidationError(
                "A product with variants keeps its category: its variants are "
                "told apart by that category's axes."
            )
        return value


class CatalogVariantWriteSerializer(serializers.ModelSerializer):
    """A variant's columns. A variant always has a price above zero; size
    defaults to "Unique" and stock to 0. The currency is not sent: every
    price is in the org's currency."""

    price = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )
    size = serializers.CharField(max_length=50, required=False, allow_blank=True)
    stock = serializers.IntegerField(
        min_value=0, max_value=2_000_000_000, required=False
    )
    sku = serializers.CharField(
        max_length=100, required=False, allow_null=True, allow_blank=True
    )
    package_height_cm = serializers.DecimalField(**PACKAGE, required=False)
    package_width_cm = serializers.DecimalField(**PACKAGE, required=False)
    package_depth_cm = serializers.DecimalField(**PACKAGE, required=False)
    package_weight_kg = serializers.DecimalField(
        max_digits=8, decimal_places=3, min_value=0, allow_null=True, required=False
    )
    attributes = AttributesPayloadField(required=False, write_only=True)

    class Meta:
        model = Product
        fields = (
            "price",
            "size",
            "stock",
            "sku",
            "gtin",
            "is_active",
            "package_height_cm",
            "package_width_cm",
            "package_depth_cm",
            "package_weight_kg",
            "attributes",
        )

    def validate_sku(self, value):
        if not value:
            return None
        clash = Product.objects.filter(org=self.context["org"], sku=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("Another product has this SKU.")
        return value


class ListingWriteSerializer(serializers.ModelSerializer):
    channel = OrgPK(SalesChannel)
    product = OrgPK(CatalogProduct)

    class Meta:
        model = Listing
        fields = (
            "channel",
            "product",
            "external_id",
            "external_group_id",
            "external_category_id",
            "external_domain",
            "title",
            "status",
            "condition",
            "listing_type",
            "shipping_mode",
            "free_shipping",
            "warranty",
            "handling_days",
        )

    def validate_channel(self, value):
        if value.kind != SalesChannel.ONLINE:
            raise serializers.ValidationError(
                "A physical store has no listings: it sells the whole catalog."
            )
        return value

    def validate(self, attrs):
        channel = attrs.get("channel", getattr(self.instance, "channel", None))
        external_id = attrs.get(
            "external_id", getattr(self.instance, "external_id", None)
        )
        clash = Listing.objects.filter(channel=channel, external_id=external_id)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                {
                    "external_id": [
                        "This sales channel already has a listing with this id."
                    ]
                }
            )
        return attrs


class ListingVariantWriteSerializer(serializers.ModelSerializer):
    variant = OrgPK(Product)

    class Meta:
        model = ListingVariant
        fields = (
            "variant",
            "external_variation_id",
            "external_product_id",
            "external_sku",
        )

    def validate_variant(self, value):
        listing = self.context["listing"]
        if value.catalog_product_id != listing.product_id:
            raise serializers.ValidationError(
                "That variant belongs to another product than the listing's."
            )
        clash = ListingVariant.objects.filter(listing=listing, variant=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("The listing already publishes it.")
        return value


class OptionWriteSerializer(AttributeOptionSerializer):
    def validate_value(self, value):
        from catalog.text import normalize_key

        attribute = self.context["attribute"]
        clash = AttributeOption.objects.filter(
            attribute=attribute, normalized=normalize_key(value)
        )
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("This attribute already has that option.")
        return value


# Imports ----------------------------------------------------------------------


class ImportFileSpecSerializer(serializers.Serializer):
    field = serializers.CharField()
    label = serializers.CharField()


class ImportSourceSerializer(serializers.Serializer):
    """A channel whose report can be imported (`available`), or one that
    cannot yet, and why."""

    code = serializers.CharField()
    name = serializers.CharField()
    available = serializers.BooleanField()
    reason = serializers.CharField()
    files = ImportFileSpecSerializer(many=True)
    channel = SalesChannelRefSerializer(allow_null=True)


class _EmptyCategorySerializer(serializers.Serializer):
    category = serializers.CharField()
    sheet = serializers.CharField()
    rows = serializers.IntegerField()
    rejected = serializers.IntegerField()


class ImportReportSerializer(serializers.Serializer):
    created = serializers.DictField(child=serializers.IntegerField())
    updated = serializers.DictField(child=serializers.IntegerField())
    notes = serializers.DictField(child=serializers.IntegerField())
    empty_categories = _EmptyCategorySerializer(many=True)
    warnings = serializers.ListField(child=serializers.CharField())
    rejected = serializers.ListField(child=serializers.CharField())


class ImportRunSerializer(serializers.ModelSerializer):
    source_name = serializers.SerializerMethodField()
    started_by = serializers.SerializerMethodField()
    files_kept = serializers.SerializerMethodField()
    file_names = serializers.DictField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = ImportRun
        fields = (
            "id",
            "source",
            "source_name",
            "dry_run",
            "status",
            "notify",
            "file_names",
            "files_kept",
            "started_by",
            "error",
            "created_at",
            "started_at",
            "finished_at",
            "emailed_at",
        )
        read_only_fields = fields

    def get_source_name(self, obj) -> str:
        from catalog.imports import SOURCES

        source = SOURCES.get(obj.source)
        return source.name if source else obj.source

    def get_started_by(self, obj) -> str:
        user = obj.created_by
        return (user.name or user.email) if user else ""

    def get_files_kept(self, obj) -> bool:
        """Whether a finished preview's files can still be imported."""
        return obj.dry_run and obj.status == ImportRun.DONE and obj.files.exists()


class ImportRunDetailSerializer(ImportRunSerializer):
    report = ImportReportSerializer(allow_null=True, read_only=True)

    class Meta(ImportRunSerializer.Meta):
        fields = (*ImportRunSerializer.Meta.fields, "report")
        read_only_fields = fields
