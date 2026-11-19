import datetime
import logging

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from common.admin_mixins import OrgScopedAdmin
from common.admin_scope import Scope, activate, admin_org, allowed_orgs
from orders.channel_import import LEVELS, MAX_REPORT_BYTES, record_import
from orders.channels import PARSERS
from orders.inventory import (
    accept_return,
    complete_work_order,
    day_counter,
    recalculate_due_dates,
    reject_return,
    why_not_completable,
)
from orders.models import (
    SALES_CHANNELS,
    ChannelListing,
    ChannelPreference,
    ImportRun,
    ImportRunEntry,
    Order,
    OrderLineItem,
    ReturnInspection,
    WorkOrder,
)

logger = logging.getLogger(__name__)

# What an import writes on a channel order. Edits here would be overwritten by
# the next report, so they are read-only on channel orders.
CHANNEL_OWNED_FIELDS = (
    "name",
    "order_number",
    "status",
    "currency",
    "subtotal",
    "total_amount",
    "order_date",
    "activated_date",
    "shipped_date",
    "shipping_address_line",
    "shipping_city",
    "shipping_state",
    "shipping_postcode",
    "shipping_country",
    "channel_status",
    "channel_status_detail",
    "fulfillment_status",
    "buyer_name",
    "due_date",
)
_WORK = ("PREPARATION", "PACKAGING")


def _is_channel_order(order):
    return order is not None and order.channel != "STORE"


def _count(model, **filters):
    """Per-order row count as a subquery, so two counts never multiply."""
    rows = (
        model.objects.filter(order=OuterRef("pk"), **filters)
        .order_by()
        .values("order")
        .annotate(n=Count("pk"))
        .values("n")[:1]
    )
    return Coalesce(Subquery(rows, output_field=IntegerField()), 0)


def _orgs_for(request):
    """The orgs this admin request may act on: the session's org, or in
    instance mode every org the user may administer."""
    org = admin_org(request)
    if org is not None:
        return allowed_orgs(request.user).filter(pk=org.pk)
    return allowed_orgs(request.user)


class DueDateFilter(admin.SimpleListFilter):
    """Due dates of what is still open. `open_lookup` says what open means."""

    title = "due date"
    parameter_name = "due"
    open_lookup = {"fulfillment_status": "PENDING"}

    def lookups(self, request, model_admin):
        return (
            ("overdue", "Overdue"),
            ("today", "Due today"),
            ("week", "Due in the next 7 days"),
            ("none", "No due date"),
        )

    def queryset(self, request, queryset):
        today = timezone.localdate()
        still_open = queryset.filter(**self.open_lookup)
        if self.value() == "overdue":
            return still_open.filter(due_date__lt=today)
        if self.value() == "today":
            return still_open.filter(due_date=today)
        if self.value() == "week":
            return still_open.filter(
                due_date__gte=today,
                due_date__lte=today + datetime.timedelta(days=7),
            )
        if self.value() == "none":
            return queryset.filter(due_date__isnull=True)
        return queryset


class WorkOrderDueDateFilter(DueDateFilter):
    open_lookup = {"status": "PENDING"}


class PendingWorkFilter(admin.SimpleListFilter):
    title = "pending work orders"
    parameter_name = "pending_work"

    def lookups(self, request, model_admin):
        return (("yes", "Yes"), ("no", "No"))

    def queryset(self, request, queryset):
        pending = Q(pending_work__gt=0)
        if self.value() == "yes":
            return queryset.filter(pending)
        if self.value() == "no":
            return queryset.exclude(pending)
        return queryset


class PendingReturnFilter(admin.SimpleListFilter):
    title = "returns to inspect"
    parameter_name = "pending_returns"

    def lookups(self, request, model_admin):
        return (("yes", "Yes"),)

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(pending_returns__gt=0)
        return queryset


class _ScopedOrgForm(forms.Form):
    """A form acting on one org the admin session may act on."""

    def __init__(self, *args, orgs, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["org"] = forms.ModelChoiceField(
            queryset=orgs, label="Organization", initial=orgs.first()
        )
        self.order_fields(["org"])


class ImportReportForm(_ScopedOrgForm):
    channel = forms.ChoiceField(
        choices=[(key, label) for key, label in SALES_CHANNELS if key in PARSERS]
    )
    report = forms.FileField(
        help_text="The channel's sales report, .xlsx, up to 10 MB. Import each "
        "new report as you receive it: sales already imported are updated."
    )

    def clean_report(self):
        upload = self.cleaned_data["report"]
        # One byte past the cap is enough to refuse without reading it all.
        data = upload.read(MAX_REPORT_BYTES + 1)
        if len(data) > MAX_REPORT_BYTES:
            raise forms.ValidationError("The report exceeds the 10 MB limit.")
        return upload.name, data


class RecalculateForm(_ScopedOrgForm):
    channel = forms.ChoiceField(choices=SALES_CHANNELS)
    from_date = forms.DateField(
        label="Sales from",
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Orders whose sale date is on or after this day are "
        "recalculated with the channel's current preference and each "
        "product's current availability time.",
    )

    def clean_from_date(self):
        value = self.cleaned_data["from_date"]
        if value > timezone.localdate():
            raise forms.ValidationError("The date cannot be in the future.")
        return value


class OrderLineItemInline(OrgScopedAdmin, admin.TabularInline):
    model = OrderLineItem
    extra = 1
    fields = (
        "product",
        "name",
        "description",
        "quantity",
        "unit_price",
        "discount_amount",
        "total",
        "sort_order",
        "external_id",
        "listing",
        "channel_status",
        "fulfillment_status",
        "due_date",
        "stock_allocated",
    )
    # Stock bookkeeping belongs to orders.inventory; editing it here would
    # make the next release restock the wrong amount.
    readonly_fields = ("total", "external_id", "listing", "stock_allocated")
    raw_id_fields = ("product",)

    def get_readonly_fields(self, request, obj=None):
        if _is_channel_order(obj):
            return self.fields
        return self.readonly_fields

    def get_extra(self, request, obj=None, **kwargs):
        return 0 if _is_channel_order(obj) else self.extra

    def has_add_permission(self, request, obj=None):
        return not _is_channel_order(obj) and super().has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not _is_channel_order(obj) and super().has_delete_permission(
            request, obj
        )


class WorkOrderInline(OrgScopedAdmin, admin.TabularInline):
    model = WorkOrder
    extra = 0
    fields = (
        "kind",
        "status",
        "outcome",
        "product",
        "line_item",
        "quantity",
        "due_date",
    )
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(OrgScopedAdmin, admin.ModelAdmin):
    list_display = (
        "name",
        "channel",
        "buyer_name",
        "account",
        "sales_channel",
        "status",
        "fulfillment_status",
        "order_date",
        "due_date",
        "item_count",
        "pending_work_count",
        "total_amount",
    )
    list_filter = (
        "channel",
        "status",
        "fulfillment_status",
        DueDateFilter,
        PendingWorkFilter,
        PendingReturnFilter,
        "order_date",
        "org",
    )
    search_fields = (
        "name",
        "order_number",
        "external_id",
        "buyer_name",
        "account__name",
        "line_items__external_id",
        "line_items__name",
        "line_items__product__name",
        "line_items__product__sku",
        "line_items__listing__listing_id",
    )
    list_select_related = ("account",)
    raw_id_fields = (
        "created_by",
        "org",
        "account",
        "contact",
        "opportunity",
        "sales_channel",
    )
    inlines = [OrderLineItemInline, WorkOrderInline]
    date_hierarchy = "order_date"
    ordering = ("-created_at",)
    change_list_template = "admin/orders/order/change_list.html"

    fieldsets = (
        (
            "Order Info",
            {
                "fields": (
                    "name",
                    "order_number",
                    "status",
                )
            },
        ),
        (
            "Sales Channel",
            {
                "fields": (
                    "channel",
                    "external_id",
                    "channel_status",
                    "channel_status_detail",
                    "fulfillment_status",
                    "buyer_name",
                    "due_date",
                    "last_synced_at",
                )
            },
        ),
        (
            "CRM Integration",
            {
                "fields": (
                    "account",
                    "contact",
                    "opportunity",
                    "sales_channel",
                )
            },
        ),
        (
            "Financial",
            {
                "fields": (
                    "currency",
                    "subtotal",
                    "discount_amount",
                    "tax_amount",
                    "total_amount",
                )
            },
        ),
        (
            "Dates",
            {
                "fields": (
                    "order_date",
                    "activated_date",
                    "shipped_date",
                )
            },
        ),
        (
            "Billing Address",
            {
                "fields": (
                    "billing_address_line",
                    "billing_city",
                    "billing_state",
                    "billing_postcode",
                    "billing_country",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Shipping Address",
            {
                "fields": (
                    "shipping_address_line",
                    "shipping_city",
                    "shipping_state",
                    "shipping_postcode",
                    "shipping_country",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Notes",
            {"fields": ("description",)},
        ),
        (
            "Organization",
            {"fields": ("org",)},
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                items=_count(OrderLineItem),
                pending_work=_count(WorkOrder, status="PENDING", kind__in=_WORK),
                pending_returns=_count(WorkOrder, status="PENDING", kind="INSPECTION"),
            )
        )

    def get_readonly_fields(self, request, obj=None):
        # Channel orders come from, and are matched on, the channel's report.
        fields = ("channel", "external_id", "last_synced_at")
        if _is_channel_order(obj):
            fields += CHANNEL_OWNED_FIELDS
        return fields

    def get_urls(self):
        return [
            path(
                "import/",
                self.admin_site.admin_view(self.import_report_view),
                name="orders_order_import",
            ),
            path(
                "recalculate/",
                self.admin_site.admin_view(self.recalculate_view),
                name="orders_order_recalculate",
            ),
            *super().get_urls(),
        ]

    def _page(self, request, template, title, form):
        context = {
            **self.admin_site.each_context(request),
            "title": title,
            "opts": self.model._meta,
            "form": form,
        }
        return TemplateResponse(request, template, context)

    def import_report_view(self, request):
        """Upload a channel report. The run and its whole log are kept; the
        page goes to the run, which shows what was imported and what was
        not, and why."""
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = ImportReportForm(
            request.POST or None, request.FILES or None, orgs=_orgs_for(request)
        )
        if request.method == "POST" and form.is_valid():
            org = form.cleaned_data["org"]
            channel = form.cleaned_data["channel"]
            file_name, data = form.cleaned_data["report"]
            with activate(Scope(org)):
                run, _result = record_import(org, channel, file_name, data, "ADMIN")
            logger.info(
                "Channel import run %s of %s into org %s by user %s: %s.",
                run.pk,
                channel,
                org.id,
                request.user.pk,
                run.outcome,
            )
            level = messages.ERROR if run.outcome == "FAILED" else messages.SUCCESS
            self.message_user(
                request,
                run.error or f"{run.get_outcome_display()}: {run.file_name}.",
                level,
            )
            not_imported = (run.counts or {}).get("orders_not_imported", 0)
            if not_imported:
                self.message_user(
                    request,
                    format_html(
                        '{} sale(s) were not imported. <a href="{}?run__id__exact={}'
                        '&amp;level__exact=NOT_IMPORTED">See which, and why</a>.',
                        not_imported,
                        reverse("admin:orders_importrunentry_changelist"),
                        run.pk,
                    ),
                    messages.WARNING,
                )
            return HttpResponseRedirect(
                reverse("admin:orders_importrun_change", args=[run.pk])
            )
        return self._page(
            request, "admin/orders/import_report.html", "Import channel report", form
        )

    def recalculate_view(self, request):
        """Set the due dates of one channel's orders from a sale date onwards
        again, from the current preference and availability times. Due dates
        are otherwise fixed at import."""
        if not self.has_change_permission(request):
            raise PermissionDenied
        form = RecalculateForm(request.POST or None, orgs=_orgs_for(request))
        if request.method == "POST" and form.is_valid():
            org = form.cleaned_data["org"]
            channel = form.cleaned_data["channel"]
            from_date = form.cleaned_data["from_date"]
            with activate(Scope(org)):
                add_days = day_counter(org.id, channel)
                orders = Order.objects.filter(
                    org=org, channel=channel, order_date__gte=from_date
                )
                total = changed = 0
                for order in orders.iterator():
                    with transaction.atomic():
                        changed += recalculate_due_dates(order, add_days)
                    total += 1
            logger.info(
                "Due dates of %s orders of %s in org %s from %s recalculated by "
                "user %s: %s changed.",
                total,
                channel,
                org.id,
                from_date,
                request.user.pk,
                changed,
            )
            self.message_user(
                request,
                f"Recalculated {total} {dict(SALES_CHANNELS)[channel]} order(s) "
                f"from {from_date}: {changed} changed.",
            )
            return HttpResponseRedirect(
                f"{reverse('admin:orders_order_changelist')}?channel__exact={channel}"
                f"&order_date__gte={from_date}"
            )
        return self._page(
            request,
            "admin/orders/recalculate.html",
            "Recalculate due dates",
            form,
        )

    @admin.display(description="Items", ordering="items")
    def item_count(self, obj):
        return obj.items

    @admin.display(description="Pending work", ordering="pending_work")
    def pending_work_count(self, obj):
        return obj.pending_work


@admin.register(OrderLineItem)
class OrderLineItemAdmin(OrgScopedAdmin, admin.ModelAdmin):
    list_display = (
        "order",
        "name",
        "description",
        "product",
        "quantity",
        "unit_price",
        "total",
        "fulfillment_status",
        "due_date",
        "stock_allocated",
    )
    list_filter = (
        "order__channel",
        "fulfillment_status",
        "fulfillment_planned",
        "order__status",
        "created_at",
    )
    search_fields = (
        "name",
        "description",
        "external_id",
        "order__name",
        "order__external_id",
        "product__name",
        "product__sku",
        "listing__listing_id",
    )
    list_select_related = ("order", "product")
    raw_id_fields = ("order", "product", "listing", "org")
    readonly_fields = ("stock_allocated", "fulfillment_planned")
    ordering = ("order", "sort_order")


@admin.register(ChannelListing)
class ChannelListingAdmin(OrgScopedAdmin, admin.ModelAdmin):
    """Point a listing's product at an existing product to merge the one the
    import created into it."""

    list_display = (
        "listing_id",
        "variant",
        "channel",
        "title",
        "product",
        "product_created",
        "org",
    )
    list_filter = ("channel", "product_created", "org")
    search_fields = ("listing_id", "variant", "title", "sku", "product__name")
    list_select_related = ("product", "org")
    raw_id_fields = ("product", "org")
    ordering = ("channel", "listing_id", "variant")


@admin.register(ChannelPreference)
class ChannelPreferenceAdmin(admin.ModelAdmin):
    """How each channel counts availability time, for the whole instance. A
    change applies to sales imported after it; "Recalculate due dates" on the
    orders list applies it to orders already imported."""

    list_display = ("channel", "availability_day_count", "updated_at")
    list_editable = ("availability_day_count",)
    ordering = ("channel",)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkOrder)
class WorkOrderAdmin(OrgScopedAdmin, admin.ModelAdmin):
    list_display = (
        "order",
        "kind",
        "status",
        "outcome",
        "quantity",
        "product",
        "line_item",
        "due_date",
        "org",
    )
    list_filter = (
        "kind",
        "status",
        WorkOrderDueDateFilter,
        "order__channel",
        "org",
    )
    search_fields = (
        "order__name",
        "order__external_id",
        "order__buyer_name",
        "product__name",
        "product__sku",
    )
    list_select_related = ("order", "product", "line_item", "org")
    raw_id_fields = ("order", "line_item", "product", "org")
    # Status moves through the actions, which keep stock in step.
    readonly_fields = ("status", "outcome", "completed_at", "completed_by")
    actions = ("mark_done",)
    ordering = ("due_date", "created_at")

    @admin.action(description="Mark selected work orders as done")
    def mark_done(self, request, queryset):
        done, refused = 0, []
        for work_order in queryset.select_related("order", "line_item"):
            with transaction.atomic():
                reason = why_not_completable(work_order)
                if reason is None and complete_work_order(work_order):
                    done += 1
                else:
                    refused.append(f"{work_order}: {reason}")
        self.message_user(request, f"{done} work order(s) marked as done.")
        if refused:
            self.message_user(
                request,
                "Not marked as done: " + " ".join(refused),
                messages.WARNING,
            )


@admin.register(ReturnInspection)
class ReturnInspectionAdmin(OrgScopedAdmin, admin.ModelAdmin):
    """The INSPECTION work orders: check goods that came back, then accept
    them into stock or reject them. The import opens them; checking the goods
    can take days."""

    list_display = (
        "order",
        "line_item",
        "product",
        "quantity",
        "restock_quantity",
        "status",
        "outcome",
        "channel_status",
        "created_at",
        "completed_at",
        "completed_by",
    )
    list_filter = ("status", "outcome", "order__channel", "created_at", "org")
    search_fields = (
        "order__name",
        "order__external_id",
        "order__buyer_name",
        "line_item__external_id",
        "product__name",
        "product__sku",
    )
    list_select_related = ("order", "line_item", "product", "completed_by")
    fields = (
        "order",
        "line_item",
        "channel_status",
        "product",
        "quantity",
        "restock_quantity",
        "notes",
        "status",
        "outcome",
        "completed_at",
        "completed_by",
    )
    actions = ("accept_selected", "reject_selected")
    ordering = ("-created_at",)
    change_form_template = "admin/orders/returninspection/change_form.html"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        fields = (
            "order",
            "line_item",
            "channel_status",
            "product",
            "quantity",
            "status",
            "outcome",
            "completed_at",
            "completed_by",
        )
        if obj is not None and obj.status != "PENDING":
            fields += ("restock_quantity", "notes")
        return fields

    @admin.display(description="Channel status")
    def channel_status(self, obj):
        return obj.line_item.channel_status if obj.line_item else ""

    def response_change(self, request, obj):
        decide = None
        if "_accept_return" in request.POST:
            decide, verb = accept_return, "accepted"
        elif "_reject_return" in request.POST:
            decide, verb = reject_return, "rejected"
        if decide is None:
            return super().response_change(request, obj)
        with transaction.atomic():
            decided = decide(obj, request.user)
        if decided:
            self.message_user(request, f"Return {verb}.")
        else:
            self.message_user(
                request, "This return was already decided.", messages.WARNING
            )
        return HttpResponseRedirect(request.path)

    def _decide_selected(self, request, queryset, decide, verb):
        decided = 0
        for inspection in queryset:
            with transaction.atomic():
                decided += decide(inspection, request.user)
        self.message_user(request, f"{decided} return(s) {verb}.")

    @admin.action(description="Accept selected returns into stock")
    def accept_selected(self, request, queryset):
        self._decide_selected(request, queryset, accept_return, "accepted")

    @admin.action(description="Reject selected returns (no restock)")
    def reject_selected(self, request, queryset):
        self._decide_selected(request, queryset, reject_return, "rejected")


class _ViewOnly:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ImportRun)
class ImportRunAdmin(_ViewOnly, OrgScopedAdmin, admin.ModelAdmin):
    """Every import, from the admin or the command line, with its log."""

    list_display = (
        "started_at",
        "channel",
        "file_name",
        "source",
        "outcome",
        "created_by",
        "orders",
        "org",
    )
    list_filter = ("outcome", "channel", "source", "started_at", "org")
    search_fields = ("file_name", "file_sha256", "entries__sale")
    list_select_related = ("created_by", "org")
    date_hierarchy = "started_at"
    fields = (
        "org",
        "channel",
        "source",
        "created_by",
        "file_name",
        "file_size",
        "file_sha256",
        "started_at",
        "finished_at",
        "outcome",
        "error",
        "summary",
        "log",
    )
    readonly_fields = fields

    @admin.display(description="Orders")
    def orders(self, obj):
        counts = obj.counts or {}
        if not counts:
            return "-"
        return (
            f"{counts.get('orders_created', 0)} created, "
            f"{counts.get('orders_updated', 0)} updated, "
            f"{counts.get('orders_not_imported', 0)} not imported"
        )

    @admin.display(description="Counts")
    def summary(self, obj):
        counts = obj.counts or {}
        return format_html_join(
            "",
            "<div>{}: {}</div>",
            ((k.replace("_", " "), v) for k, v in counts.items()),
        )

    @admin.display(description="Log")
    def log(self, obj):
        url = reverse("admin:orders_importrunentry_changelist")
        per_level = dict(
            obj.entries.order_by()
            .values_list("level")
            .annotate(n=Count("pk"))
            .values_list("level", "n")
        )
        if not per_level:
            return "No entries."
        return format_html_join(
            "",
            '<div><a href="{}?run__id__exact={}&amp;level__exact={}">{} ({})</a></div>',
            (
                (url, obj.pk, level, label, per_level[level])
                for level, label in LEVELS.items()
                if level in per_level
            ),
        ) + format_html(
            '<div><a href="{}?run__id__exact={}">All {} entries</a></div>',
            url,
            obj.pk,
            sum(per_level.values()),
        )


@admin.register(ImportRunEntry)
class ImportRunEntryAdmin(_ViewOnly, OrgScopedAdmin, admin.ModelAdmin):
    list_display = ("run", "level", "sale", "message")
    list_filter = ("level", "run__channel", "run__outcome", "org")
    search_fields = ("sale", "message")
    list_select_related = ("run",)
    ordering = ("-run__started_at", "position")
    list_per_page = 200
