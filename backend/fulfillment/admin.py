"""Setup only: every state change (completing, creating and closing dispatch
orders) goes through `fulfillment.services` from the API, where its rules are
checked."""

from django.contrib import admin

from fulfillment import services
from fulfillment.models import (
    DeliveryOption,
    DeliveryOptionDay,
    DispatchOrder,
    DispatchOrderItem,
    PackagingOrder,
    PreparationOrder,
    QueueAssignment,
    Sale,
    SalesChannel,
)


class _ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SalesChannel)
class SalesChannelAdmin(admin.ModelAdmin):
    list_display = ("name", "late_penalty", "org", "created_at")
    search_fields = ("name",)
    raw_id_fields = ("org",)


class DeliveryOptionDayInline(admin.TabularInline):
    model = DeliveryOptionDay
    extra = 0
    fields = ("date", "kind", "opens_at", "note")


@admin.register(DeliveryOption)
class DeliveryOptionAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "kind",
        "channel",
        "weekday",
        "departure_time",
        "dispatch_minutes",
        "cost",
        "capacity",
        "is_active",
        "org",
    )
    list_filter = ("kind", "is_active", "weekday")
    search_fields = ("name", "location")
    raw_id_fields = ("org", "channel")
    inlines = [DeliveryOptionDayInline]


class PreparationOrderInline(admin.TabularInline):
    model = PreparationOrder
    extra = 0
    fields = ("description", "line_item", "status", "completed_at")
    readonly_fields = ("status", "completed_at")
    raw_id_fields = ("line_item",)


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("order", "channel", "committed_delivery_at", "org", "created_at")
    search_fields = ("order__name", "order__order_number")
    raw_id_fields = ("org", "order", "channel")
    inlines = [PreparationOrderInline]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        services.open_packaging(obj)


@admin.register(QueueAssignment)
class QueueAssignmentAdmin(admin.ModelAdmin):
    list_display = ("team", "order_type", "access", "org")
    list_filter = ("order_type", "access")
    raw_id_fields = ("org", "team")


@admin.register(PackagingOrder)
class PackagingOrderAdmin(_ReadOnlyAdmin):
    list_display = ("sale", "status", "completed_at", "org")
    list_filter = ("status",)


class DispatchOrderItemInline(admin.TabularInline):
    model = DispatchOrderItem
    extra = 0
    fields = ("sale", "estimated_delivery_at", "late", "cost_share", "penalty")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DispatchOrder)
class DispatchOrderAdmin(_ReadOnlyAdmin):
    list_display = (
        "delivery_option",
        "departs_at",
        "status",
        "cost",
        "closed_at",
        "org",
    )
    list_filter = ("status",)
    inlines = [DispatchOrderItemInline]
