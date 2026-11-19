"""Catalog endpoints, all under /api/catalog/.

Any member reads the catalog (reps price line items from it); changing it is
org configuration, so every write is for an admin who is a person
(`may_configure`), the posture `/api/invoices/products/` already takes.
Every lookup is scoped to the request org, and a row of another org answers
404 like a missing one.
"""

from django.db import transaction
from django.db.models import (
    Count,
    Max,
    Min,
    OuterRef,
    Q,
    RestrictedError,
    Subquery,
    Sum,
)
from django.db.models.functions import Coalesce
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.imports import ImportRefused, import_previewed, sources, start_import
from catalog.models import (
    Attribute,
    AttributeOption,
    CatalogProduct,
    Category,
    CategoryAttribute,
    ImportRun,
    Listing,
    ListingVariant,
    SalesChannel,
)
from catalog.queries import (
    filter_products,
    narrow_variants,
    parse_attribute_filters,
    parse_in_stock,
    parse_sizes,
)
from catalog.serializers import (
    AttributeOptionSerializer,
    AttributeSerializer,
    CatalogProductListSerializer,
    CatalogProductSerializer,
    CatalogProductWriteSerializer,
    CatalogVariantSerializer,
    CatalogVariantWriteSerializer,
    CategoryAttributeSerializer,
    CategorySerializer,
    ImportRunDetailSerializer,
    ImportRunSerializer,
    ImportSourceSerializer,
    ListingSerializer,
    ListingVariantWriteSerializer,
    ListingWriteSerializer,
    OptionWriteSerializer,
    SalesChannelSerializer,
    VariantListingSerializer,
    axes_by_category,
)
from catalog.services import (
    CatalogError,
    apply_attributes,
    channel_deletion_refusal,
    deletion_refusal,
    product_deletion_refusal,
    refresh_variants,
    save_variant,
)
from common.lookups import get_scoped_or_404
from common.module_deletes import delete_refusal
from common.pagination import CappedLimitOffsetPagination
from common.permissions import HasOrgContext, may_configure
from common.throttles import ImportThrottle
from common.validators import uuid_param
from invoices.models import Product

ADMIN_ONLY = "Only an administrator can change the product catalog."
ATTR_PARAM = OpenApiParameter(
    "attr",
    str,
    many=True,
    description="code:value, repeatable. One code's values are alternatives; "
    "different codes must all match.",
)
SIZE_PARAM = OpenApiParameter(
    "size", str, many=True, description="Repeatable; any of these sizes."
)
IN_STOCK_PARAM = OpenApiParameter(
    "in_stock", bool, description="true: stock above 0. false: none left."
)


def _page(name, child):
    return inline_serializer(
        name,
        {
            "count": serializers.IntegerField(),
            "next": serializers.URLField(allow_null=True),
            "previous": serializers.URLField(allow_null=True),
            "results": child,
        },
    )


def _results(name, child):
    return inline_serializer(name, {"results": child})


def _catalog_errors(exc: CatalogError):
    return ValidationError(exc.as_errors())


def _protected(message):
    return Response({"detail": message}, status=status.HTTP_409_CONFLICT)


class CatalogView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.org = request.profile.org

    def check_permissions(self, request):
        super().check_permissions(request)
        if request.method not in SAFE_METHODS and not may_configure(request.profile):
            self.permission_denied(request, message=ADMIN_ONLY)

    def ctx(self, **extra):
        return {"org": self.org, "request": self.request, **extra}


# Sales channels --------------------------------------------------------------------


class SalesChannelListView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        responses=_results("SalesChannelList", SalesChannelSerializer(many=True)),
    )
    def get(self, request):
        channels = SalesChannel.objects.filter(org=self.org)
        return Response({"results": SalesChannelSerializer(channels, many=True).data})

    @extend_schema(
        tags=["Catalog"],
        request=SalesChannelSerializer,
        responses={201: SalesChannelSerializer},
    )
    def post(self, request):
        serializer = SalesChannelSerializer(data=request.data, context=self.ctx())
        serializer.is_valid(raise_exception=True)
        if SalesChannel.objects.filter(
            org=self.org, code=serializer.validated_data["code"]
        ).exists():
            raise ValidationError({"code": ["Another sales channel has this code."]})
        channel = serializer.save(org=self.org)
        return Response(
            SalesChannelSerializer(channel).data, status=status.HTTP_201_CREATED
        )


class SalesChannelDetailView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        request=SalesChannelSerializer(partial=True),
        responses=SalesChannelSerializer,
    )
    def patch(self, request, pk):
        channel = get_scoped_or_404(SalesChannel, pk, self.org)
        serializer = SalesChannelSerializer(
            channel, data=request.data, partial=True, context=self.ctx()
        )
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data.get("code")
        if (
            code
            and SalesChannel.objects.filter(org=self.org, code=code)
            .exclude(pk=channel.pk)
            .exists()
        ):
            raise ValidationError({"code": ["Another sales channel has this code."]})
        return Response(SalesChannelSerializer(serializer.save()).data)

    @extend_schema(tags=["Catalog"], responses={204: None})
    def delete(self, request, pk):
        channel = get_scoped_or_404(SalesChannel, pk, self.org)
        # Invoices switched off keep their rows out of sight; name the module.
        hidden = delete_refusal(channel, channel.name)
        if hidden:
            return Response(hidden, status=status.HTTP_409_CONFLICT)
        refusal = channel_deletion_refusal(channel)
        if refusal:
            return _protected(refusal)
        channel.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# Attributes -------------------------------------------------------------------


def _attributes_qs(org):
    return Attribute.objects.filter(org=org).prefetch_related(
        "options", "channel_mappings__channel"
    )


class AttributeListView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        responses=_results("AttributeList", AttributeSerializer(many=True)),
    )
    def get(self, request):
        attributes = _attributes_qs(self.org)
        return Response({"results": AttributeSerializer(attributes, many=True).data})

    @extend_schema(
        tags=["Catalog"],
        request=AttributeSerializer,
        responses={201: AttributeSerializer},
    )
    def post(self, request):
        serializer = AttributeSerializer(data=request.data, context=self.ctx())
        serializer.is_valid(raise_exception=True)
        attribute = serializer.save(org=self.org)
        return Response(
            AttributeSerializer(attribute).data, status=status.HTTP_201_CREATED
        )


class AttributeDetailView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        request=AttributeSerializer(partial=True),
        responses=AttributeSerializer,
    )
    def patch(self, request, pk):
        attribute = get_scoped_or_404(Attribute, pk, self.org)
        serializer = AttributeSerializer(
            attribute, data=request.data, partial=True, context=self.ctx()
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(AttributeSerializer(_attributes_qs(self.org).get(pk=pk)).data)

    @extend_schema(tags=["Catalog"], responses={204: None})
    def delete(self, request, pk):
        attribute = get_scoped_or_404(Attribute, pk, self.org)
        if attribute.values.exists():
            return _protected("Products still use this attribute.")
        attribute.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AttributeOptionListView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        request=AttributeOptionSerializer,
        responses={201: AttributeOptionSerializer},
    )
    def post(self, request, pk):
        attribute = get_scoped_or_404(Attribute, pk, self.org)
        if attribute.data_type != Attribute.OPTION:
            raise ValidationError({"value": ["Only option lists have options."]})
        serializer = OptionWriteSerializer(
            data=request.data, context=self.ctx(attribute=attribute)
        )
        serializer.is_valid(raise_exception=True)
        option = serializer.save(org=self.org, attribute=attribute)
        return Response(
            AttributeOptionSerializer(option).data, status=status.HTTP_201_CREATED
        )


class AttributeOptionDetailView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        request=AttributeOptionSerializer(partial=True),
        responses=AttributeOptionSerializer,
    )
    def patch(self, request, pk):
        option = get_scoped_or_404(AttributeOption, pk, self.org)
        serializer = OptionWriteSerializer(
            option,
            data=request.data,
            partial=True,
            context=self.ctx(attribute=option.attribute),
        )
        serializer.is_valid(raise_exception=True)
        option = serializer.save()
        if "value" in serializer.validated_data:
            for product in CatalogProduct.objects.filter(
                org=self.org, variants__attribute_values__option=option
            ).distinct():
                refresh_variants(product)
        return Response(AttributeOptionSerializer(option).data)

    @extend_schema(tags=["Catalog"], responses={204: None})
    def delete(self, request, pk):
        option = get_scoped_or_404(AttributeOption, pk, self.org)
        if option.values.exists():
            return _protected("Products still use this option.")
        option.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# Categories -------------------------------------------------------------------


def _categories_qs(org):
    return (
        Category.objects.filter(org=org)
        .annotate(product_count=Count("products"))
        .prefetch_related("attribute_links__attribute")
    )


class CategoryListView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        responses=_results("CategoryList", CategorySerializer(many=True)),
    )
    def get(self, request):
        categories = _categories_qs(self.org)
        return Response({"results": CategorySerializer(categories, many=True).data})

    @extend_schema(
        tags=["Catalog"],
        request=CategorySerializer,
        responses={201: CategorySerializer},
    )
    def post(self, request):
        serializer = CategorySerializer(data=request.data, context=self.ctx())
        serializer.is_valid(raise_exception=True)
        category = serializer.save(org=self.org)
        category = _categories_qs(self.org).get(pk=category.pk)
        return Response(
            CategorySerializer(category).data, status=status.HTTP_201_CREATED
        )


class CategoryDetailView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        request=CategorySerializer(partial=True),
        responses=CategorySerializer,
    )
    def patch(self, request, pk):
        category = get_scoped_or_404(Category, pk, self.org)
        serializer = CategorySerializer(
            category, data=request.data, partial=True, context=self.ctx()
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if "name" in serializer.validated_data:
            Product.objects.filter(catalog_product__category=category).update(
                category=category.name[:100]
            )
        return Response(CategorySerializer(_categories_qs(self.org).get(pk=pk)).data)

    @extend_schema(tags=["Catalog"], responses={204: None})
    def delete(self, request, pk):
        category = get_scoped_or_404(Category, pk, self.org)
        try:
            category.delete()
        except RestrictedError:
            return _protected("Products or other categories still use this category.")
        return Response(status=status.HTTP_204_NO_CONTENT)


def _axes_change_refused(category, attribute):
    """Variant keys are built from a category's axes, so adding or removing an
    axis after variants exist would leave those keys stale."""
    if Product.objects.filter(catalog_product__category=category).exists():
        raise ValidationError(
            {
                "role": [
                    f"{category.name} already has variants, so its variant axes "
                    f"are fixed ({attribute.code} cannot change role)."
                ]
            }
        )


class CategoryAttributeListView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        request=CategoryAttributeSerializer,
        responses={201: CategoryAttributeSerializer},
    )
    def post(self, request, pk):
        category = get_scoped_or_404(Category, pk, self.org)
        serializer = CategoryAttributeSerializer(data=request.data, context=self.ctx())
        serializer.is_valid(raise_exception=True)
        attribute = serializer.validated_data["attribute"]
        if CategoryAttribute.objects.filter(
            category=category, attribute=attribute
        ).exists():
            raise ValidationError({"attribute": ["The category already uses it."]})
        if serializer.validated_data["role"] == CategoryAttribute.VARIANT_AXIS:
            _axes_change_refused(category, attribute)
        link = serializer.save(org=self.org, category=category)
        return Response(
            CategoryAttributeSerializer(link).data, status=status.HTTP_201_CREATED
        )


class CategoryAttributeDetailView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        request=CategoryAttributeSerializer(partial=True),
        responses=CategoryAttributeSerializer,
    )
    def patch(self, request, pk):
        link = get_scoped_or_404(CategoryAttribute, pk, self.org)
        serializer = CategoryAttributeSerializer(
            link, data=request.data, partial=True, context=self.ctx()
        )
        serializer.is_valid(raise_exception=True)
        if "attribute" in serializer.validated_data and (
            serializer.validated_data["attribute"] != link.attribute
        ):
            raise ValidationError({"attribute": ["Remove the link and add another."]})
        role = serializer.validated_data.get("role", link.role)
        if role != link.role:
            _axes_change_refused(link.category, link.attribute)
        return Response(CategoryAttributeSerializer(serializer.save()).data)

    @extend_schema(tags=["Catalog"], responses={204: None})
    def delete(self, request, pk):
        link = get_scoped_or_404(CategoryAttribute, pk, self.org)
        if link.role == CategoryAttribute.VARIANT_AXIS:
            _axes_change_refused(link.category, link.attribute)
        link.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# Products ---------------------------------------------------------------------


def _product_list_qs(org):
    listings = (
        Listing.objects.filter(product=OuterRef("pk"))
        .order_by()
        .values("product")
        .annotate(n=Count("pk"))
        .values("n")
    )
    active = Q(variants__is_active=True)
    return (
        CatalogProduct.objects.filter(org=org)
        .select_related("category")
        .annotate(
            variant_count=Count("variants"),
            active_variant_count=Count("variants", filter=active),
            stock=Coalesce(Sum("variants__stock"), 0),
            listing_count=Coalesce(Subquery(listings), 0),
            price_min=Min("variants__price", filter=active),
            price_max=Max("variants__price", filter=active),
            currency=Max("variants__currency"),
        )
    )


def _sizes_by_product(products):
    rows = (
        Product.objects.filter(catalog_product__in=[p.pk for p in products])
        .order_by()
        .values_list("catalog_product_id", "size", "size_sort")
        .distinct()
    )
    found = {}
    for product_id, size, order in sorted(rows, key=lambda r: (r[2], r[1])):
        found.setdefault(product_id, []).append(size)
    return found


VARIANT_ORDER = ("catalog_product__name", "catalog_product_id", "size_sort", "name")


def _variant_prefetch(queryset):
    return queryset.select_related("catalog_product").prefetch_related(
        "attribute_values__attribute",
        "attribute_values__option",
        "listing_variants__listing__channel",
    )


def _product_detail(org, pk):
    found = get_scoped_or_404(CatalogProduct, pk, org)
    product = (
        CatalogProduct.objects.select_related("category")
        .prefetch_related(
            "attribute_values__attribute",
            "attribute_values__option",
            "listings__channel",
        )
        .get(pk=found.pk)
    )
    variants = _variant_prefetch(product.variants.order_by("size_sort", "name"))
    product._prefetched_objects_cache["variants"] = variants
    return product


class ProductListView(CatalogView, CappedLimitOffsetPagination):
    @extend_schema(
        tags=["Catalog"],
        parameters=[
            OpenApiParameter("search", str),
            OpenApiParameter("category", str),
            OpenApiParameter("channel", str),
            OpenApiParameter("is_active", bool),
            SIZE_PARAM,
            IN_STOCK_PARAM,
            ATTR_PARAM,
        ],
        operation_id="catalog_products_list",
        responses=_page("CatalogProductPage", CatalogProductListSerializer(many=True)),
    )
    def get(self, request):
        params = request.query_params
        queryset = CatalogProduct.objects.filter(org=self.org)
        if search := params.get("search", "").strip():
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(variants__sku__icontains=search)
                | Q(listings__external_id__iexact=search)
            )
        if category := uuid_param(params, "category"):
            queryset = queryset.filter(category_id=category)
        if channel := uuid_param(params, "channel"):
            queryset = queryset.filter(listings__channel_id=channel)
        if params.get("is_active") in ("true", "false"):
            queryset = queryset.filter(is_active=params["is_active"] == "true")
        queryset = filter_products(
            queryset,
            self.org,
            parse_attribute_filters(params, self.org),
            parse_sizes(params),
            parse_in_stock(params),
        )
        ids = queryset.order_by().values("pk").distinct()
        products = _product_list_qs(self.org).filter(pk__in=ids).order_by("name")
        page = self.paginate_queryset(products, request, view=self)
        context = {"sizes_by_product": _sizes_by_product(page)}
        return Response(
            {
                "count": self.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": CatalogProductListSerializer(
                    page, many=True, context=context
                ).data,
            }
        )

    @extend_schema(
        tags=["Catalog"],
        request=CatalogProductWriteSerializer,
        responses={201: CatalogProductSerializer},
    )
    def post(self, request):
        serializer = CatalogProductWriteSerializer(
            data=request.data, context=self.ctx()
        )
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        attributes = data.pop("attributes", None)
        try:
            with transaction.atomic():
                product = CatalogProduct.objects.create(org=self.org, **data)
                if attributes:
                    apply_attributes(product, None, attributes)
        except CatalogError as exc:
            raise _catalog_errors(exc) from None
        return Response(
            CatalogProductSerializer(_product_detail(self.org, product.pk)).data,
            status=status.HTTP_201_CREATED,
        )


class ProductDetailView(CatalogView):
    @extend_schema(tags=["Catalog"], responses=CatalogProductSerializer)
    def get(self, request, pk):
        return Response(CatalogProductSerializer(_product_detail(self.org, pk)).data)

    @extend_schema(
        tags=["Catalog"],
        request=CatalogProductWriteSerializer(partial=True),
        responses=CatalogProductSerializer,
    )
    def patch(self, request, pk):
        product = get_scoped_or_404(CatalogProduct, pk, self.org)
        serializer = CatalogProductWriteSerializer(
            product, data=request.data, partial=True, context=self.ctx()
        )
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        attributes = data.pop("attributes", None)
        try:
            with transaction.atomic():
                for field, value in data.items():
                    setattr(product, field, value)
                product.save()
                if attributes:
                    apply_attributes(product, None, attributes)
                if "name" in data:
                    refresh_variants(product)
        except CatalogError as exc:
            raise _catalog_errors(exc) from None
        return Response(CatalogProductSerializer(_product_detail(self.org, pk)).data)

    @extend_schema(tags=["Catalog"], responses={204: None})
    def delete(self, request, pk):
        product = get_scoped_or_404(CatalogProduct, pk, self.org)
        refusal = product_deletion_refusal(product)
        if refusal:
            return _protected(refusal)
        product.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _variant_fields(org, data):
    """The variant columns to write. The currency is always the org's: the
    catalog holds one price per variant, in one currency."""
    fields = {k: v for k, v in data.items() if k != "attributes"}
    if "sku" in fields and not fields["sku"]:
        fields["sku"] = None
    fields["currency"] = org.default_currency or None
    return fields


class VariantCreateView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        request=CatalogVariantWriteSerializer,
        responses={201: CatalogVariantSerializer},
    )
    def post(self, request, pk):
        product = get_scoped_or_404(CatalogProduct, pk, self.org)
        serializer = CatalogVariantWriteSerializer(
            data=request.data, context=self.ctx()
        )
        serializer.is_valid(raise_exception=True)
        try:
            variant = save_variant(
                product,
                None,
                serializer.validated_data.get("attributes", {}),
                _variant_fields(self.org, serializer.validated_data),
            )
        except CatalogError as exc:
            raise _catalog_errors(exc) from None
        variant = _variant_prefetch(Product.objects.filter(pk=variant.pk)).get()
        return Response(
            CatalogVariantSerializer(variant).data, status=status.HTTP_201_CREATED
        )


def _variants_qs(org):
    return Product.objects.filter(org=org, catalog_product__isnull=False)


class VariantListView(CatalogView, CappedLimitOffsetPagination):
    @extend_schema(
        tags=["Catalog"],
        parameters=[
            OpenApiParameter("product", str),
            OpenApiParameter("category", str),
            OpenApiParameter("search", str),
            OpenApiParameter("is_active", bool),
            SIZE_PARAM,
            IN_STOCK_PARAM,
            ATTR_PARAM,
        ],
        operation_id="catalog_variants_list",
        responses=_page("CatalogVariantPage", CatalogVariantSerializer(many=True)),
    )
    def get(self, request):
        params = request.query_params
        queryset = _variants_qs(self.org)
        if product := uuid_param(params, "product"):
            queryset = queryset.filter(catalog_product_id=product)
        if category := uuid_param(params, "category"):
            queryset = queryset.filter(catalog_product__category_id=category)
        if search := params.get("search", "").strip():
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(sku__icontains=search)
            )
        if params.get("is_active") in ("true", "false"):
            queryset = queryset.filter(is_active=params["is_active"] == "true")
        queryset = narrow_variants(
            queryset,
            parse_attribute_filters(params, self.org),
            parse_sizes(params),
            parse_in_stock(params),
        )
        queryset = _variant_prefetch(queryset.order_by(*VARIANT_ORDER))
        page = self.paginate_queryset(queryset, request, view=self)
        context = {
            "axes_by_category": axes_by_category(
                {v.catalog_product.category_id for v in page}
            )
        }
        return Response(
            {
                "count": self.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": CatalogVariantSerializer(
                    page, many=True, context=context
                ).data,
            }
        )


class VariantDetailView(CatalogView):
    def _get(self, pk):
        found = get_scoped_or_404(Product, pk, self.org, catalog_product__isnull=False)
        return _variant_prefetch(Product.objects.filter(pk=found.pk)).get()

    @extend_schema(tags=["Catalog"], responses=CatalogVariantSerializer)
    def get(self, request, pk):
        return Response(CatalogVariantSerializer(self._get(pk)).data)

    @extend_schema(
        tags=["Catalog"],
        request=CatalogVariantWriteSerializer(partial=True),
        responses=CatalogVariantSerializer,
    )
    def patch(self, request, pk):
        variant = self._get(pk)
        serializer = CatalogVariantWriteSerializer(
            variant, data=request.data, partial=True, context=self.ctx()
        )
        serializer.is_valid(raise_exception=True)
        product = CatalogProduct.objects.select_related("category").get(
            pk=variant.catalog_product_id
        )
        try:
            save_variant(
                product,
                variant,
                serializer.validated_data.get("attributes", {}),
                _variant_fields(self.org, serializer.validated_data),
            )
        except CatalogError as exc:
            raise _catalog_errors(exc) from None
        return Response(CatalogVariantSerializer(self._get(pk)).data)

    @extend_schema(tags=["Catalog"], responses={204: None})
    def delete(self, request, pk):
        variant = self._get(pk)
        refusal = deletion_refusal(variant.name, [variant.pk])
        if refusal:
            return _protected(refusal)
        variant.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


_SIZE_ROWS = inline_serializer(
    "CatalogSize",
    {"size": serializers.CharField(), "variant_count": serializers.IntegerField()},
    many=True,
)


class SizeListView(CatalogView):
    """The sizes the org's variants come in, youngest first, for pickers and
    filters. Only sizes some variant has: no product needs all of them."""

    @extend_schema(
        tags=["Catalog"],
        parameters=[OpenApiParameter("category", str)],
        responses=_results("CatalogSizeList", _SIZE_ROWS),
    )
    def get(self, request):
        variants = _variants_qs(self.org)
        if category := uuid_param(request.query_params, "category"):
            variants = variants.filter(catalog_product__category_id=category)
        rows = (
            variants.order_by()
            .values("size", "size_sort")
            .annotate(variant_count=Count("pk"))
            .order_by("size_sort", "size")
        )
        return Response(
            {
                "results": [
                    {"size": r["size"], "variant_count": r["variant_count"]}
                    for r in rows
                ]
            }
        )


# Listings ---------------------------------------------------------------------


def _listings_qs(org):
    return (
        Listing.objects.filter(org=org)
        .select_related("channel")
        .annotate(variant_count=Count("variants"))
    )


class ListingListView(CatalogView, CappedLimitOffsetPagination):
    @extend_schema(
        tags=["Catalog"],
        parameters=[
            OpenApiParameter("channel", str),
            OpenApiParameter("product", str),
            OpenApiParameter("status", str),
        ],
        responses=_page("CatalogListingPage", ListingSerializer(many=True)),
    )
    def get(self, request):
        params = request.query_params
        queryset = _listings_qs(self.org)
        if channel := uuid_param(params, "channel"):
            queryset = queryset.filter(channel_id=channel)
        if product := uuid_param(params, "product"):
            queryset = queryset.filter(product_id=product)
        if params.get("status") in dict(Listing.STATUSES):
            queryset = queryset.filter(status=params["status"])
        page = self.paginate_queryset(
            queryset.order_by("external_id"), request, view=self
        )
        return Response(
            {
                "count": self.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": ListingSerializer(page, many=True).data,
            }
        )

    @extend_schema(
        tags=["Catalog"],
        request=ListingWriteSerializer,
        responses={201: ListingSerializer},
    )
    def post(self, request):
        serializer = ListingWriteSerializer(data=request.data, context=self.ctx())
        serializer.is_valid(raise_exception=True)
        listing = serializer.save(org=self.org)
        return Response(
            ListingSerializer(_listings_qs(self.org).get(pk=listing.pk)).data,
            status=status.HTTP_201_CREATED,
        )


class ListingDetailView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        request=ListingWriteSerializer(partial=True),
        responses=ListingSerializer,
    )
    def patch(self, request, pk):
        listing = get_scoped_or_404(Listing, pk, self.org)
        serializer = ListingWriteSerializer(
            listing, data=request.data, partial=True, context=self.ctx()
        )
        serializer.is_valid(raise_exception=True)
        product = serializer.validated_data.get("product")
        if (
            product is not None
            and product != listing.product
            and listing.variants.exists()
        ):
            raise ValidationError(
                {"product": ["Remove the listing's variants before moving it."]}
            )
        serializer.save()
        return Response(ListingSerializer(_listings_qs(self.org).get(pk=pk)).data)

    @extend_schema(tags=["Catalog"], responses={204: None})
    def delete(self, request, pk):
        get_scoped_or_404(Listing, pk, self.org).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _listing_variant(pk):
    return ListingVariant.objects.select_related("listing__channel").get(pk=pk)


class ListingVariantCreateView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        request=ListingVariantWriteSerializer,
        responses={201: VariantListingSerializer},
    )
    def post(self, request, pk):
        listing = get_scoped_or_404(Listing, pk, self.org)
        serializer = ListingVariantWriteSerializer(
            data=request.data, context=self.ctx(listing=listing)
        )
        serializer.is_valid(raise_exception=True)
        row = serializer.save(org=self.org, listing=listing)
        return Response(
            VariantListingSerializer(_listing_variant(row.pk)).data,
            status=status.HTTP_201_CREATED,
        )


class ListingVariantDetailView(CatalogView):
    @extend_schema(
        tags=["Catalog"],
        request=ListingVariantWriteSerializer(partial=True),
        responses=VariantListingSerializer,
    )
    def patch(self, request, pk):
        row = get_scoped_or_404(ListingVariant, pk, self.org)
        serializer = ListingVariantWriteSerializer(
            row, data=request.data, partial=True, context=self.ctx(listing=row.listing)
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(VariantListingSerializer(_listing_variant(pk)).data)

    @extend_schema(tags=["Catalog"], responses={204: None})
    def delete(self, request, pk):
        get_scoped_or_404(ListingVariant, pk, self.org).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# Importing a channel's report -----------------------------------------------


class ImportView(CatalogView):
    """Imports are for admins only, reads included: a report quotes the rows
    of the files it read."""

    def check_permissions(self, request):
        super().check_permissions(request)
        if not may_configure(request.profile):
            self.permission_denied(request, message=ADMIN_ONLY)


def _refused(exc):
    if exc.field:
        return Response({exc.field: [str(exc)]}, status=exc.status)
    return Response({"detail": str(exc)}, status=exc.status)


_SOURCE_LIST = _results("ImportSourceList", ImportSourceSerializer(many=True))
_RUN_REQUEST = inline_serializer(
    "ImportRunStart",
    {
        "source": serializers.CharField(),
        "dry_run": serializers.BooleanField(),
        "notify": serializers.BooleanField(required=False),
        "characteristics": serializers.FileField(required=False),
        "prices": serializers.FileField(required=False),
    },
)


class ImportSourceListView(ImportView):
    @extend_schema(tags=["Catalog"], responses=_SOURCE_LIST)
    def get(self, request):
        return Response(
            {"results": ImportSourceSerializer(sources(self.org), many=True).data}
        )


class ImportRunListView(ImportView, CappedLimitOffsetPagination):
    parser_classes = (MultiPartParser,)

    def get_throttles(self):
        # Starting a run stores up to two 20 MB files; listing them is cheap.
        return [ImportThrottle()] if self.request.method == "POST" else []

    @extend_schema(
        tags=["Catalog"],
        operation_id="catalog_import_runs_list",
        responses=_page("ImportRunPage", ImportRunSerializer(many=True)),
    )
    def get(self, request):
        runs = ImportRun.objects.filter(org=self.org).select_related("created_by")
        page = self.paginate_queryset(runs, request, view=self)
        return self.get_paginated_response(ImportRunSerializer(page, many=True).data)

    @extend_schema(
        tags=["Catalog"],
        request=_RUN_REQUEST,
        responses={202: ImportRunDetailSerializer},
    )
    def post(self, request):
        """Check and store the files, queue the run, and answer at once; the
        run's page follows it. Nothing is stored when anything is refused."""
        truthy = ("true", "1", "on", "yes")
        try:
            run = start_import(
                self.org,
                request.user,
                (request.data.get("source") or "").strip(),
                request.FILES,
                dry_run=str(request.data.get("dry_run", "")).lower() in truthy,
                notify=str(request.data.get("notify", "")).lower() in truthy,
            )
        except ImportRefused as exc:
            return _refused(exc)
        return Response(
            ImportRunDetailSerializer(run).data, status=status.HTTP_202_ACCEPTED
        )


class ImportRunDetailView(ImportView):
    @extend_schema(tags=["Catalog"], responses=ImportRunDetailSerializer)
    def get(self, request, pk):
        run = get_scoped_or_404(ImportRun, pk, self.org)
        return Response(ImportRunDetailSerializer(run).data)


class ImportRunCommitView(ImportView):
    """Import the files a finished preview checked, without sending them
    again."""

    throttle_classes = (ImportThrottle,)

    @extend_schema(
        tags=["Catalog"],
        request=inline_serializer(
            "ImportPreviewed", {"notify": serializers.BooleanField(required=False)}
        ),
        responses={202: ImportRunDetailSerializer},
    )
    def post(self, request, pk):
        preview = get_scoped_or_404(ImportRun, pk, self.org)
        notify = str(request.data.get("notify", "")).lower() in ("true", "1", "on")
        try:
            run = import_previewed(preview, request.user, notify=notify)
        except ImportRefused as exc:
            return _refused(exc)
        return Response(
            ImportRunDetailSerializer(run).data, status=status.HTTP_202_ACCEPTED
        )
