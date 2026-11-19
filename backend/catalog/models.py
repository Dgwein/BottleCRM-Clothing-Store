"""The product catalog: products with variants, described by shared attributes,
and sold through sales channels (online ones and physical stores).

A `CatalogProduct` is what the store thinks of as one product (a publication
family on Mercado Libre). Its variants are `invoices.Product` rows linked back
through `Product.catalog_product`, so every existing line item, picker and
price already works at variant level: a variant has one price, in the org's
currency, whatever channel it is sold through. A standalone `invoices.Product`
with no `catalog_product` is the single-dimension product the CRM always had.

Characteristics are not columns. Each is an `Attribute` shared by every
category that uses it, and a product or variant stores an `AttributeValue` row
only for the attributes it actually has. See docs/architecture/catalog.md for the model.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from common.base import BaseOrgModel
from common.utils import COUNTRIES
from common.validators import flexible_phone_validator

ORG_INDEX = models.Index(fields=["org", "-created_at"])


class SalesChannel(BaseOrgModel):
    """Where the store sells: an online channel (Mercado Libre, Amazon, its
    own web shop) or a physical store. It is not an `Account`: an invoice is
    addressed to the customer who pays it and records the channel the sale
    came through (`Invoice.sales_channel`). Every channel sells the whole
    catalog at the variant's one price. Only an online channel publishes
    listings under its own ids and names attributes its own way."""

    ONLINE = "ONLINE"
    STORE = "STORE"
    KINDS = ((ONLINE, "Online"), (STORE, "Physical store"))

    name = models.CharField(_("Name"), max_length=100)
    code = models.SlugField(_("Code"), max_length=50)
    kind = models.CharField(_("Kind"), max_length=10, choices=KINDS, default=ONLINE)
    site = models.CharField(
        _("Site"),
        max_length=50,
        blank=True,
        default="",
        help_text="An online channel's own market id, such as MLM.",
    )
    address_line = models.CharField(
        _("Address"), max_length=255, blank=True, default=""
    )
    city = models.CharField(_("City"), max_length=255, blank=True, default="")
    state = models.CharField(_("State"), max_length=255, blank=True, default="")
    postcode = models.CharField(_("Postal code"), max_length=64, blank=True, default="")
    country = models.CharField(
        _("Country"), max_length=3, choices=COUNTRIES, blank=True, default=""
    )
    phone = models.CharField(
        _("Phone"),
        max_length=25,
        blank=True,
        default="",
        validators=[flexible_phone_validator],
    )
    is_active = models.BooleanField(_("Is active"), default=True)

    ADDRESS_FIELDS = ("address_line", "city", "state", "postcode", "country", "phone")

    class Meta:
        db_table = "catalog_sales_channel"
        ordering = ("name",)
        indexes = [ORG_INDEX]
        constraints = [
            models.UniqueConstraint(
                fields=["org", "code"], name="catalog_sales_channel_unique_code"
            ),
            models.CheckConstraint(
                condition=Q(kind="ONLINE") | Q(site=""),
                name="catalog_sales_channel_site_is_online",
            ),
        ]

    def __str__(self):
        return self.name


class Category(BaseOrgModel):
    """A master category (Vestidos, Mamelucos). A channel's category ids live
    on each `Listing`, because one master category spans several of them."""

    name = models.CharField(_("Name"), max_length=150)
    parent = models.ForeignKey(
        "self",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="children",
    )

    class Meta:
        db_table = "catalog_category"
        verbose_name_plural = "Categories"
        ordering = ("name",)
        indexes = [ORG_INDEX]
        constraints = [
            models.UniqueConstraint(
                fields=["org", "name"], name="catalog_category_unique_name"
            )
        ]

    def __str__(self):
        return self.name


class Attribute(BaseOrgModel):
    """One characteristic, defined once and shared by every category using it."""

    TEXT = "TEXT"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    OPTION = "OPTION"
    DATA_TYPES = (
        (TEXT, "Text"),
        (NUMBER, "Number"),
        (BOOLEAN, "Yes or no"),
        (OPTION, "Option list"),
    )

    code = models.SlugField(_("Code"), max_length=64)
    name = models.CharField(_("Name"), max_length=150)
    data_type = models.CharField(_("Data type"), max_length=10, choices=DATA_TYPES)
    is_multivalued = models.BooleanField(
        _("Takes several values"),
        default=False,
        help_text="Only option lists can hold several values, one row per option.",
    )
    unit = models.CharField(_("Default unit"), max_length=20, blank=True, default="")

    class Meta:
        db_table = "catalog_attribute"
        ordering = ("name",)
        indexes = [ORG_INDEX]
        constraints = [
            models.UniqueConstraint(
                fields=["org", "code"], name="catalog_attribute_unique_code"
            ),
            models.CheckConstraint(
                condition=Q(is_multivalued=False) | Q(data_type="OPTION"),
                name="catalog_attribute_multi_is_option",
            ),
        ]

    def __str__(self):
        return self.name


class AttributeOption(BaseOrgModel):
    """An allowed value of an option-list attribute. `normalized` is the match
    key (see `catalog.text.normalize_key`), so "Genérica" and "generica" are
    one option. `aliases` holds further normalized keys that mean this option,
    such as a channel's spelling of a size."""

    attribute = models.ForeignKey(
        Attribute, on_delete=models.CASCADE, related_name="options"
    )
    value = models.CharField(_("Value"), max_length=150)
    normalized = models.CharField(max_length=150, editable=False)
    aliases = models.JSONField(default=list, blank=True)
    sort_order = models.IntegerField(_("Sort order"), default=0)

    class Meta:
        db_table = "catalog_attribute_option"
        ordering = ("sort_order", "value")
        indexes = [ORG_INDEX]
        constraints = [
            models.UniqueConstraint(
                fields=["attribute", "normalized"],
                name="catalog_option_unique_value",
            )
        ]

    def __str__(self):
        return self.value

    def save(self, *args, **kwargs):
        from catalog.text import normalize_key

        self.normalized = normalize_key(self.value)
        super().save(*args, **kwargs)


class CategoryAttribute(BaseOrgModel):
    """Which attributes a category uses, and how.

    A variant axis (color, fabric design) is one of the mandatory fields whose
    combination, with the variant's size, identifies a variant, and so its
    price. Size is a column on the variant, not an attribute. A characteristic
    describes the product and does not split it into variants.
    """

    VARIANT_AXIS = "VARIANT_AXIS"
    CHARACTERISTIC = "CHARACTERISTIC"
    ROLES = ((VARIANT_AXIS, "Variant axis"), (CHARACTERISTIC, "Characteristic"))

    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="attribute_links"
    )
    attribute = models.ForeignKey(
        Attribute, on_delete=models.CASCADE, related_name="category_links"
    )
    role = models.CharField(_("Role"), max_length=20, choices=ROLES)
    is_required = models.BooleanField(_("Is required"), default=False)
    sort_order = models.IntegerField(_("Sort order"), default=0)

    class Meta:
        db_table = "catalog_category_attribute"
        ordering = ("sort_order",)
        indexes = [ORG_INDEX]
        constraints = [
            models.UniqueConstraint(
                fields=["category", "attribute"],
                name="catalog_category_attribute_unique",
            )
        ]


class ChannelAttribute(BaseOrgModel):
    """How an online channel names an attribute: Mercado Libre's `MAIN_MATERIAL` in
    dresses and `MATERIALS` in aprons both feed `material`. The importer reads
    these before its built-in defaults, so remapping a code here changes what
    the next import does. A blank `external_domain` applies to every domain."""

    channel = models.ForeignKey(
        SalesChannel, on_delete=models.CASCADE, related_name="attribute_mappings"
    )
    attribute = models.ForeignKey(
        Attribute, on_delete=models.CASCADE, related_name="channel_mappings"
    )
    external_code = models.CharField(_("Channel code"), max_length=100)
    external_domain = models.CharField(
        _("Channel domain"), max_length=100, blank=True, default=""
    )

    class Meta:
        db_table = "catalog_channel_attribute"
        ordering = ("external_code",)
        indexes = [ORG_INDEX]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_code", "external_domain"],
                name="catalog_channel_attribute_unique",
            )
        ]


class CatalogProduct(BaseOrgModel):
    """A product as the store thinks of it, independent of any channel. Its
    sellable variants are `self.variants` (`invoices.Product`)."""

    name = models.CharField(_("Name"), max_length=255)
    description = models.TextField(_("Description"), blank=True, default="")
    category = models.ForeignKey(
        Category, on_delete=models.RESTRICT, related_name="products"
    )
    is_active = models.BooleanField(_("Is active"), default=True)

    class Meta:
        db_table = "catalog_product"
        verbose_name = "Catalog product"
        ordering = ("name",)
        indexes = [ORG_INDEX, models.Index(fields=["org", "category"])]

    def __str__(self):
        return self.name


class AttributeValue(BaseOrgModel):
    """One value of one attribute, on a product or on one of its variants.

    `variant` empty means the value holds for every variant of `product`; a
    variant's own row for the same attribute overrides it. Exactly one value
    column is filled, the one `attribute.data_type` names. A multi-valued
    option attribute has one row per option.
    """

    product = models.ForeignKey(
        CatalogProduct, on_delete=models.CASCADE, related_name="attribute_values"
    )
    variant = models.ForeignKey(
        "invoices.Product",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attribute_values",
    )
    attribute = models.ForeignKey(
        Attribute, on_delete=models.RESTRICT, related_name="values"
    )
    option = models.ForeignKey(
        AttributeOption,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="values",
    )
    value_text = models.CharField(max_length=500, blank=True, default="")
    value_number = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True
    )
    value_boolean = models.BooleanField(null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        db_table = "catalog_attribute_value"
        indexes = [
            ORG_INDEX,
            models.Index(fields=["attribute", "option"]),
        ]
        # One row per (owner, attribute), or per (owner, attribute, option)
        # for option lists. Split four ways because a NULL variant or option
        # never collides in a plain unique index.
        constraints = [
            models.UniqueConstraint(
                fields=["product", "attribute"],
                condition=Q(variant__isnull=True, option__isnull=True),
                name="catalog_value_unique_product_scalar",
            ),
            models.UniqueConstraint(
                fields=["product", "attribute", "option"],
                condition=Q(variant__isnull=True, option__isnull=False),
                name="catalog_value_unique_product_option",
            ),
            models.UniqueConstraint(
                fields=["variant", "attribute"],
                condition=Q(variant__isnull=False, option__isnull=True),
                name="catalog_value_unique_variant_scalar",
            ),
            models.UniqueConstraint(
                fields=["variant", "attribute", "option"],
                condition=Q(variant__isnull=False, option__isnull=False),
                name="catalog_value_unique_variant_option",
            ),
        ]

    def display(self):
        if self.option_id:
            return self.option.value
        if self.value_boolean is not None:
            return "Sí" if self.value_boolean else "No"
        if self.value_number is not None:
            number = self.value_number.normalize()
            text = f"{number:f}"
            return f"{text} {self.unit}".strip()
        return self.value_text


class Listing(BaseOrgModel):
    """A publication of a product on an online channel (an ML item, an Amazon
    ASIN). A physical store has none: it sells the whole catalog.

    On Mercado Libre a classic item holds several variations, while a "user
    product" item holds one and is grouped with its siblings by family
    (`external_group_id`). Both shapes are a listing with one or more
    `ListingVariant` offers.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    STATUSES = ((ACTIVE, "Active"), (INACTIVE, "Inactive"))
    NEW = "NEW"
    USED = "USED"
    CONDITIONS = ((NEW, "New"), (USED, "Used"))

    channel = models.ForeignKey(
        SalesChannel, on_delete=models.RESTRICT, related_name="listings"
    )
    product = models.ForeignKey(
        CatalogProduct, on_delete=models.CASCADE, related_name="listings"
    )
    external_id = models.CharField(_("Channel id"), max_length=100)
    external_group_id = models.CharField(
        _("Channel group id"), max_length=100, blank=True, default=""
    )
    external_category_id = models.CharField(
        _("Channel category"), max_length=100, blank=True, default=""
    )
    external_domain = models.CharField(
        _("Channel domain"), max_length=100, blank=True, default=""
    )
    title = models.CharField(_("Title"), max_length=255)
    status = models.CharField(
        _("Status"), max_length=10, choices=STATUSES, blank=True, default=""
    )
    condition = models.CharField(
        _("Condition"), max_length=10, choices=CONDITIONS, blank=True, default=""
    )
    listing_type = models.CharField(
        _("Listing type"), max_length=50, blank=True, default=""
    )
    shipping_mode = models.CharField(
        _("Shipping"), max_length=100, blank=True, default=""
    )
    free_shipping = models.BooleanField(_("Free shipping"), default=False)
    warranty = models.CharField(_("Warranty"), max_length=150, blank=True, default="")
    handling_days = models.PositiveSmallIntegerField(
        _("Days to ship"), null=True, blank=True
    )
    # When the channel last changed the publication, as its report says;
    # decides which of two publications of one variant is loaded.
    channel_updated_at = models.DateTimeField(
        _("Updated on the channel"), null=True, blank=True
    )

    class Meta:
        db_table = "catalog_listing"
        ordering = ("external_id",)
        indexes = [ORG_INDEX, models.Index(fields=["org", "product"])]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_id"],
                name="catalog_listing_unique_external_id",
            )
        ]

    def __str__(self):
        return f"{self.channel} {self.external_id}"


class ListingVariant(BaseOrgModel):
    """One variant published through one listing, under the channel's ids.

    Price and stock are the variant's own (`invoices.Product`), the same on
    every channel, so nothing here is priced.
    """

    listing = models.ForeignKey(
        Listing, on_delete=models.CASCADE, related_name="variants"
    )
    variant = models.ForeignKey(
        "invoices.Product", on_delete=models.CASCADE, related_name="listing_variants"
    )
    external_variation_id = models.CharField(max_length=100, blank=True, default="")
    external_product_id = models.CharField(max_length=100, blank=True, default="")
    external_sku = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        db_table = "catalog_listing_variant"
        indexes = [ORG_INDEX, models.Index(fields=["org", "external_product_id"])]
        constraints = [
            models.UniqueConstraint(
                fields=["listing", "variant"], name="catalog_listing_variant_unique"
            ),
        ]

    def clean(self):
        if self.variant_id and self.listing_id:
            if self.variant.catalog_product_id != self.listing.product_id:
                raise ValidationError(
                    {"variant": "The variant belongs to another product."}
                )


class ImportRun(BaseOrgModel):
    """One import of a channel's report, run in the background (a Celery
    task) so a large report does not hold a request open. A preview runs the
    whole import and rolls it back; its files are kept for a day so the same
    files can then be imported without sending them again. `created_by` is
    who started it, and who is emailed the report. See catalog/imports.py."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    STATUSES = (
        (QUEUED, "Queued"),
        (RUNNING, "Running"),
        (DONE, "Done"),
        (FAILED, "Failed"),
    )

    source = models.CharField(_("Source"), max_length=50)
    dry_run = models.BooleanField(_("Preview"), default=False)
    status = models.CharField(
        _("Status"), max_length=10, choices=STATUSES, default=QUEUED
    )
    notify = models.BooleanField(
        _("Email the report"),
        default=False,
        help_text="Asked for when it was started; a long run is emailed anyway.",
    )
    file_names = models.JSONField(default=dict, blank=True)
    report = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    emailed_at = models.DateTimeField(null=True, blank=True)
    # The preview whose files this import reuses.
    preview_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "catalog_import_run"
        ordering = ("-created_at",)
        indexes = [ORG_INDEX, models.Index(fields=["org", "status"])]

    def __str__(self):
        return f"{self.source} {'preview' if self.dry_run else 'import'} {self.status}"


class ImportFile(BaseOrgModel):
    """A file of an import run, kept in the database until the run no longer
    needs it: deleted when an import finishes, and a day after a preview."""

    run = models.ForeignKey(ImportRun, on_delete=models.CASCADE, related_name="files")
    field = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    content = models.BinaryField()

    class Meta:
        db_table = "catalog_import_file"
        indexes = [ORG_INDEX]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "field"], name="catalog_import_file_unique_field"
            )
        ]
