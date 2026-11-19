from django.contrib import admin

from catalog.models import (
    Attribute,
    AttributeOption,
    CatalogProduct,
    Category,
    CategoryAttribute,
    ChannelAttribute,
    Listing,
    ListingVariant,
    SalesChannel,
)


class OrgInlinesAdmin(admin.ModelAdmin):
    """Inline rows take their org from the record they hang off."""

    def save_formset(self, request, form, formset, change):
        for obj in formset.save(commit=False):
            obj.org_id = form.instance.org_id
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()


@admin.register(SalesChannel)
class SalesChannelAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "site", "org")
    list_filter = ("org",)


class CategoryAttributeInline(admin.TabularInline):
    model = CategoryAttribute
    extra = 0
    autocomplete_fields = ("attribute",)
    exclude = ("org", "created_by", "updated_by")


@admin.register(Category)
class CategoryAdmin(OrgInlinesAdmin):
    list_display = ("name", "parent", "org")
    list_filter = ("org",)
    search_fields = ("name",)
    inlines = (CategoryAttributeInline,)


class AttributeOptionInline(admin.TabularInline):
    model = AttributeOption
    extra = 0
    exclude = ("org", "created_by", "updated_by")


class ChannelAttributeInline(admin.TabularInline):
    model = ChannelAttribute
    extra = 0
    exclude = ("org", "created_by", "updated_by")


@admin.register(Attribute)
class AttributeAdmin(OrgInlinesAdmin):
    list_display = ("code", "name", "data_type", "is_multivalued", "org")
    list_filter = ("org", "data_type")
    search_fields = ("code", "name")
    inlines = (AttributeOptionInline, ChannelAttributeInline)


@admin.register(CatalogProduct)
class CatalogProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "is_active", "org")
    list_filter = ("org", "category", "is_active")
    search_fields = ("name",)


class ListingVariantInline(admin.TabularInline):
    model = ListingVariant
    extra = 0
    raw_id_fields = ("variant",)
    exclude = ("org", "created_by", "updated_by")


@admin.register(Listing)
class ListingAdmin(OrgInlinesAdmin):
    list_display = ("external_id", "channel", "product", "status", "org")
    list_filter = ("org", "channel", "status")
    search_fields = ("external_id", "title")
    raw_id_fields = ("product",)
    inlines = (ListingVariantInline,)
