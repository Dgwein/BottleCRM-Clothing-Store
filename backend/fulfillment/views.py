"""`/api/fulfillment/`: channels, delivery options, sales, their work orders,
the queues and their dispatch.

Working an order belongs to the teams assigned to its type (`access.py`):
preparation, packaging and delivery each check the caller's level before
anything else; inspection is served by the returns project. Sales are read by anyone on one of those teams. Channels
and delivery options are read by every member and changed by admins; so are
the assignments. Registering a sale is admin work, open to an app member so the
channel sync can do it. See docs/api/fulfillment.md.
"""

from django.db.models import Prefetch, ProtectedError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import SalesChannel
from common.lookups import get_scoped_or_404
from common.money import org_currency
from common.permissions import HasOrgContext, IsOrgAdmin, is_org_admin, may_configure
from common.validators import choice_list_param
from fulfillment import access, queues, services
from fulfillment.models import (
    DISPATCH_MODES,
    DISPATCH_STATUS,
    SERVED_TYPES,
    DeliveryOption,
    DeliveryOptionDay,
    DispatchOrder,
    DispatchOrderItem,
    PackagingOrder,
    PreparationOrder,
    QueueAssignment,
    Sale,
)
from fulfillment.serializers import (
    DeliveryOptionDaySerializer,
    DeliveryOptionSerializer,
    DispatchOrderSerializer,
    DispatchPlanSerializer,
    DispatchRecalculationRequestSerializer,
    DispatchRecalculationSerializer,
    ManualDispatchSerializer,
    QueueAssignmentSerializer,
    QueueItemSerializer,
    QueueSummarySerializer,
    SaleCreateSerializer,
    SalesChannelSerializer,
    SaleSerializer,
)

TAGS = ["Fulfillment"]
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

ADMINS_CONFIGURE = "Only admins can change channels, delivery options and their days."
ADMINS_REGISTER = "Only admins can register sales."
IN_USE = "Other records still use this, so it cannot be deleted."
NO_TEAM = "None of your teams handles fulfilment orders."

PAGE_PARAMS = [
    OpenApiParameter("limit", OpenApiTypes.INT, OpenApiParameter.QUERY),
    OpenApiParameter("offset", OpenApiTypes.INT, OpenApiParameter.QUERY),
]


def _invalid(errors):
    return Response(
        {"error": True, "errors": errors}, status=status.HTTP_400_BAD_REQUEST
    )


def _conflict(message):
    return Response({"detail": message}, status=status.HTTP_409_CONFLICT)


def _count(params, name, default):
    """A non-negative integer query parameter; anything else is `default`."""
    try:
        value = int(params.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _page(request, queryset):
    """``(rows, envelope)``: one page of ``queryset`` and the paging figures
    (``count``, ``limit``, ``offset``) to answer with it."""
    limit = min(_count(request.query_params, "limit", DEFAULT_LIMIT), MAX_LIMIT)
    offset = _count(request.query_params, "offset", 0)
    page = queryset[offset : offset + limit]
    return page, {"count": queryset.count(), "limit": limit, "offset": offset}


def _flag(params, name):
    """A ``true``/``false`` query parameter, false when absent."""
    value = (params.get(name) or "false").strip().lower()
    if value not in ("true", "false", "1", "0"):
        raise ValidationError({name: ["Use true or false."]})
    return value in ("true", "1")


PAGING = {
    "count": serializers.IntegerField(),
    "limit": serializers.IntegerField(),
    "offset": serializers.IntegerField(),
}


def _require_configure(request):
    if not may_configure(request.profile):
        raise PermissionDenied(ADMINS_CONFIGURE)


def _require(request, order_type, level):
    if not access.may(request.profile, order_type, level):
        raise PermissionDenied(access.refusal(order_type, level))


def _require_any(request):
    if not access.levels(request.profile):
        raise PermissionDenied(NO_TEAM)


def _sales(org):
    return (
        Sale.objects.filter(org=org)
        .select_related(
            "order",
            "channel",
            "packaging_order",
            "dispatch_item__dispatch_order__delivery_option",
        )
        .prefetch_related("preparation_orders")
    )


def _sale_out(org, pk):
    return Response(SaleSerializer(_sales(org).get(pk=pk)).data)


def _dispatch_orders(org):
    return (
        DispatchOrder.objects.filter(org=org)
        .select_related("delivery_option")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=DispatchOrderItem.objects.select_related(
                    "sale__order", "sale__channel"
                ).order_by("created_at", "id"),
            )
        )
    )


def _list_config(request, rows, serializer_class, key):
    org = request.profile.org
    rows = rows.filter(org=org).order_by("name", "id")
    data = serializer_class(rows, many=True, context={"org": org}).data
    return Response({key: data})


def _create_config(request, serializer_class):
    _require_configure(request)
    org = request.profile.org
    serializer = serializer_class(data=request.data, context={"org": org})
    if not serializer.is_valid():
        return _invalid(serializer.errors)
    serializer.save(org=org)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


def _read_config(request, model, serializer_class, pk):
    org = request.profile.org
    row = get_scoped_or_404(model, pk, org)
    return Response(serializer_class(row, context={"org": org}).data)


def _update_config(request, model, serializer_class, pk):
    _require_configure(request)
    org = request.profile.org
    row = get_scoped_or_404(model, pk, org)
    serializer = serializer_class(
        row, data=request.data, partial=True, context={"org": org}
    )
    if not serializer.is_valid():
        return _invalid(serializer.errors)
    serializer.save()
    return Response(serializer.data)


def _delete_config(request, model, pk):
    _require_configure(request)
    row = get_scoped_or_404(model, pk, request.profile.org)
    try:
        row.delete()
    except ProtectedError:
        return _conflict(IN_USE)
    return Response(status=status.HTTP_204_NO_CONTENT)


class ChannelListView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=TAGS,
        operation_id="fulfillment_channels_list",
        responses={
            200: inline_serializer(
                name="SalesChannelListResponse",
                fields={"channels": SalesChannelSerializer(many=True)},
            )
        },
    )
    def get(self, request):
        return _list_config(
            request, SalesChannel.objects.all(), SalesChannelSerializer, "channels"
        )

    @extend_schema(
        tags=TAGS,
        request=SalesChannelSerializer,
        responses={201: SalesChannelSerializer},
    )
    def post(self, request):
        return _create_config(request, SalesChannelSerializer)


class ChannelDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=TAGS, responses={200: SalesChannelSerializer})
    def get(self, request, pk):
        return _read_config(request, SalesChannel, SalesChannelSerializer, pk)

    @extend_schema(
        tags=TAGS,
        request=SalesChannelSerializer(partial=True),
        responses={200: SalesChannelSerializer},
    )
    def patch(self, request, pk):
        return _update_config(request, SalesChannel, SalesChannelSerializer, pk)

    @extend_schema(tags=TAGS, responses={204: None})
    def delete(self, request, pk):
        return _delete_config(request, SalesChannel, pk)


class DeliveryOptionListView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=TAGS,
        operation_id="fulfillment_delivery_options_list",
        responses={
            200: inline_serializer(
                name="DeliveryOptionListResponse",
                fields={"delivery_options": DeliveryOptionSerializer(many=True)},
            )
        },
    )
    def get(self, request):
        return _list_config(
            request,
            DeliveryOption.objects.select_related("channel"),
            DeliveryOptionSerializer,
            "delivery_options",
        )

    @extend_schema(
        tags=TAGS,
        request=DeliveryOptionSerializer,
        responses={201: DeliveryOptionSerializer},
    )
    def post(self, request):
        return _create_config(request, DeliveryOptionSerializer)


class DeliveryOptionDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=TAGS, responses={200: DeliveryOptionSerializer})
    def get(self, request, pk):
        return _read_config(request, DeliveryOption, DeliveryOptionSerializer, pk)

    @extend_schema(
        tags=TAGS,
        request=DeliveryOptionSerializer(partial=True),
        responses={200: DeliveryOptionSerializer},
    )
    def patch(self, request, pk):
        return _update_config(request, DeliveryOption, DeliveryOptionSerializer, pk)

    @extend_schema(tags=TAGS, responses={204: None})
    def delete(self, request, pk):
        return _delete_config(request, DeliveryOption, pk)


class DeliveryOptionDayListView(APIView):
    """An option's dated free and extra working days."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=TAGS,
        operation_id="fulfillment_delivery_option_days_list",
        responses={
            200: inline_serializer(
                name="DeliveryOptionDayListResponse",
                fields={"days": DeliveryOptionDaySerializer(many=True)},
            )
        },
    )
    def get(self, request, pk):
        option = get_scoped_or_404(DeliveryOption, pk, request.profile.org)
        days = option.days.order_by("date", "id")
        return Response({"days": DeliveryOptionDaySerializer(days, many=True).data})

    @extend_schema(
        tags=TAGS,
        request=DeliveryOptionDaySerializer,
        responses={201: DeliveryOptionDaySerializer},
    )
    def post(self, request, pk):
        _require_configure(request)
        option = get_scoped_or_404(DeliveryOption, pk, request.profile.org)
        serializer = DeliveryOptionDaySerializer(
            data=request.data, context={"delivery_option": option}
        )
        if not serializer.is_valid():
            return _invalid(serializer.errors)
        serializer.save(org=option.org, delivery_option=option)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class DeliveryOptionDayDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=TAGS, responses={204: None})
    def delete(self, request, pk, day_pk):
        _require_configure(request)
        option = get_scoped_or_404(DeliveryOption, pk, request.profile.org)
        get_scoped_or_404(
            DeliveryOptionDay, day_pk, request.profile.org, delivery_option=option
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SaleListView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=TAGS,
        operation_id="fulfillment_sales_list",
        parameters=PAGE_PARAMS,
        responses={
            200: inline_serializer(
                name="SaleListResponse",
                fields={"sales": SaleSerializer(many=True), **PAGING},
            )
        },
    )
    def get(self, request):
        _require_any(request)
        rows = _sales(request.profile.org).order_by("-created_at", "-id")
        page, paging = _page(request, rows)
        return Response({"sales": SaleSerializer(page, many=True).data, **paging})

    @extend_schema(
        tags=TAGS, request=SaleCreateSerializer, responses={201: SaleSerializer}
    )
    def post(self, request):
        if not is_org_admin(request.profile):
            raise PermissionDenied(ADMINS_REGISTER)
        org = request.profile.org
        serializer = SaleCreateSerializer(data=request.data, context={"org": org})
        if not serializer.is_valid():
            return _invalid(serializer.errors)
        values = serializer.validated_data
        sale = services.register_sale(
            values["order"],
            values["channel"],
            values["committed_delivery_at"],
            values["preparations"],
        )
        response = _sale_out(org, sale.pk)
        response.status_code = status.HTTP_201_CREATED
        return response


class SaleDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=TAGS, responses={200: SaleSerializer})
    def get(self, request, pk):
        _require_any(request)
        org = request.profile.org
        return _sale_out(org, get_scoped_or_404(Sale, pk, org).pk)


def _complete(request, pk, order_type, model, complete):
    _require(request, order_type, "HANDLE")
    org = request.profile.org
    work_order = get_scoped_or_404(model, pk, org)
    try:
        complete(work_order)
    except services.Refused as refusal:
        return _conflict(str(refusal))
    return _sale_out(org, work_order.sale_id)


class PreparationCompleteView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=TAGS, request=None, responses={200: SaleSerializer})
    def post(self, request, pk):
        return _complete(
            request, pk, "PREPARATION", PreparationOrder, services.complete_preparation
        )


class PackagingCompleteView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=TAGS, request=None, responses={200: SaleSerializer})
    def post(self, request, pk):
        return _complete(
            request, pk, "PACKAGING", PackagingOrder, services.complete_packaging
        )


def _sale_fields(sale):
    return {
        "sale": sale.id,
        "order": sale.order_id,
        "order_name": sale.order.name,
        "channel": sale.channel_id,
        "channel_name": sale.channel.name,
        "committed_delivery_at": sale.committed_delivery_at,
    }


def _unassigned(computed):
    return [_sale_fields(computed.sales[i]) for i in computed.plan.unassigned]


DISPATCH_FILTERS = [
    OpenApiParameter("delivery_option", OpenApiTypes.UUID, many=True),
    OpenApiParameter("channel", OpenApiTypes.UUID, many=True),
    OpenApiParameter(
        "mode", OpenApiTypes.STR, enum=[m for m, _ in DISPATCH_MODES], many=True
    ),
    OpenApiParameter("departs_from", OpenApiTypes.DATE),
    OpenApiParameter("departs_to", OpenApiTypes.DATE),
    OpenApiParameter("search", OpenApiTypes.STR),
    OpenApiParameter("ordering", OpenApiTypes.STR, enum=["departs", "-departs"]),
    *PAGE_PARAMS,
]
WORK_FILTERS = [
    OpenApiParameter("channel", OpenApiTypes.UUID, many=True),
    OpenApiParameter("due_from", OpenApiTypes.DATE),
    OpenApiParameter("due_to", OpenApiTypes.DATE),
    OpenApiParameter("search", OpenApiTypes.STR),
    OpenApiParameter(
        "ordering", OpenApiTypes.STR, enum=["due", "-due", "created", "-created"]
    ),
    *PAGE_PARAMS,
]
NON_WORKING = OpenApiParameter("allow_non_working_days", OpenApiTypes.BOOL)


def _dispatch_page(request, rows, key):
    page, paging = _page(request, queues.filter_dispatch(rows, request.query_params))
    return {key: DispatchOrderSerializer(page, many=True).data, **paging}


class DispatchOrderListView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=TAGS,
        operation_id="fulfillment_dispatch_orders_list",
        parameters=[
            OpenApiParameter(
                "status",
                OpenApiTypes.STR,
                enum=[value for value, _ in DISPATCH_STATUS],
                many=True,
            ),
            *DISPATCH_FILTERS,
        ],
        responses={
            200: inline_serializer(
                name="DispatchOrderListResponse",
                fields={
                    "dispatch_orders": DispatchOrderSerializer(many=True),
                    **PAGING,
                },
            )
        },
    )
    def get(self, request):
        _require(request, "DELIVERY", "VIEW")
        rows = _dispatch_orders(request.profile.org)
        wanted = choice_list_param(
            request.query_params, "status", [v for v, _ in DISPATCH_STATUS]
        )
        if wanted:
            rows = rows.filter(status__in=wanted)
        return Response(_dispatch_page(request, rows, "dispatch_orders"))

    @extend_schema(
        tags=TAGS,
        operation_id="fulfillment_dispatch_orders_recalculate",
        request=DispatchRecalculationRequestSerializer,
        responses={200: DispatchRecalculationSerializer},
    )
    def post(self, request):
        """Create or recalculate the automatic dispatch orders: every ready sale
        not on a person's order, planned by the rules."""
        _require(request, "DELIVERY", "HANDLE")
        body = DispatchRecalculationRequestSerializer(data=request.data)
        if not body.is_valid():
            return _invalid(body.errors)
        org = request.profile.org
        computed = services.create_dispatch_orders(
            org, body.validated_data["allow_non_working_days"]
        )
        rows = _dispatch_orders(org).filter(status="OPEN").order_by("departs_at", "id")
        payload = {
            "generated_at": computed.generated_at,
            "currency": org_currency(org),
            "exhaustive": computed.plan.exhaustive,
            "dispatch_orders": rows,
            "unassigned": _unassigned(computed),
        }
        return Response(DispatchRecalculationSerializer(payload).data)


class DispatchOrderManualView(APIView):
    """A dispatch order from sales a person chose (``MANUAL``), or from chosen
    sales with the rest of the run filled by the plan (``HYBRID``)."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=TAGS,
        request=ManualDispatchSerializer,
        responses={201: DispatchOrderSerializer},
    )
    def post(self, request):
        _require(request, "DELIVERY", "HANDLE")
        org = request.profile.org
        body = ManualDispatchSerializer(data=request.data, context={"org": org})
        if not body.is_valid():
            return _invalid(body.errors)
        values = body.validated_data
        try:
            order = services.create_manual_dispatch_order(
                org,
                values["sales"],
                option=values.get("delivery_option"),
                fill=values["mode"] == "HYBRID",
                allow_non_working_days=values["allow_non_working_days"],
            )
        except services.Refused as refusal:
            return _conflict(str(refusal))
        row = _dispatch_orders(org).get(pk=order.pk)
        return Response(
            DispatchOrderSerializer(row).data, status=status.HTTP_201_CREATED
        )


class DispatchOrderDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=TAGS, responses={200: DispatchOrderSerializer})
    def get(self, request, pk):
        _require(request, "DELIVERY", "VIEW")
        org = request.profile.org
        get_scoped_or_404(DispatchOrder, pk, org)
        return Response(DispatchOrderSerializer(_dispatch_orders(org).get(pk=pk)).data)

    @extend_schema(tags=TAGS, responses={204: None})
    def delete(self, request, pk):
        """Release an open dispatch order: its sales are ready again."""
        _require(request, "DELIVERY", "HANDLE")
        dispatch_order = get_scoped_or_404(DispatchOrder, pk, request.profile.org)
        try:
            services.release_dispatch_order(dispatch_order)
        except services.Refused as refusal:
            return _conflict(str(refusal))
        return Response(status=status.HTTP_204_NO_CONTENT)


class DispatchOrderCloseView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=TAGS, request=None, responses={200: DispatchOrderSerializer})
    def post(self, request, pk):
        _require(request, "DELIVERY", "CLOSE")
        org = request.profile.org
        dispatch_order = get_scoped_or_404(DispatchOrder, pk, org)
        try:
            services.close_dispatch_order(dispatch_order)
        except services.Refused as refusal:
            return _conflict(str(refusal))
        row = _dispatch_orders(org).get(pk=dispatch_order.pk)
        return Response(DispatchOrderSerializer(row).data)


class DispatchPlanView(APIView):
    """The plan from the current values, as a preview: it writes nothing.
    Dispatch orders are written by `POST /dispatch-orders/`."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=TAGS,
        parameters=[NON_WORKING],
        responses={200: DispatchPlanSerializer},
    )
    def get(self, request):
        _require(request, "DELIVERY", "VIEW")
        org = request.profile.org
        computed = services.compute_plan(
            org, _flag(request.query_params, "allow_non_working_days")
        )
        result = computed.plan
        departures = {}
        assignments = []
        for a in result.assignments:
            option = computed.options[a.option_id]
            assignments.append(
                {
                    **_sale_fields(computed.sales[a.shipment_id]),
                    "delivery_option": option.id,
                    "departs_at": a.departs_at,
                    "arrives_at": a.arrives_at,
                    "late": a.late,
                    "cost_share": a.cost_share,
                    "penalty": a.penalty,
                }
            )
            departure = departures.setdefault(
                (option.id, a.departs_at),
                {
                    "delivery_option": option,
                    "departs_at": a.departs_at,
                    "arrives_at": a.arrives_at,
                    "sales": [],
                },
            )
            departure["sales"].append(a.shipment_id)
        payload = {
            "generated_at": computed.generated_at,
            "currency": org_currency(org),
            "exhaustive": result.exhaustive,
            "total_cost": result.total_cost,
            "total_penalty": result.total_penalty,
            "total": result.total_cost + result.total_penalty,
            "departures": sorted(
                departures.values(),
                key=lambda d: (d["departs_at"], d["delivery_option"].name),
            ),
            "assignments": assignments,
            "unassigned": _unassigned(computed),
        }
        return Response(DispatchPlanSerializer(payload).data)


class QueueAssignmentListView(APIView):
    """Which teams handle which order types. Admins only."""

    permission_classes = (IsAuthenticated, HasOrgContext, IsOrgAdmin)

    @extend_schema(
        tags=TAGS,
        operation_id="fulfillment_queue_assignments_list",
        responses={
            200: inline_serializer(
                name="QueueAssignmentListResponse",
                fields={"assignments": QueueAssignmentSerializer(many=True)},
            )
        },
    )
    def get(self, request):
        org = request.profile.org
        rows = (
            QueueAssignment.objects.filter(org=org)
            .select_related("team")
            .order_by("order_type", "team__name", "id")
        )
        data = QueueAssignmentSerializer(rows, many=True, context={"org": org}).data
        return Response({"assignments": data})

    @extend_schema(
        tags=TAGS,
        request=QueueAssignmentSerializer,
        responses={201: QueueAssignmentSerializer},
    )
    def post(self, request):
        return _create_config(request, QueueAssignmentSerializer)


class QueueAssignmentDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext, IsOrgAdmin)

    @extend_schema(
        tags=TAGS,
        request=QueueAssignmentSerializer(partial=True),
        responses={200: QueueAssignmentSerializer},
    )
    def patch(self, request, pk):
        return _update_config(request, QueueAssignment, QueueAssignmentSerializer, pk)

    @extend_schema(tags=TAGS, responses={204: None})
    def delete(self, request, pk):
        return _delete_config(request, QueueAssignment, pk)


class QueueListView(APIView):
    """The queues this app serves that the caller's teams may see, with the
    level they hold. Every member with access to a type shares its queue."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=TAGS,
        operation_id="fulfillment_queues_list",
        responses={
            200: inline_serializer(
                name="QueueListResponse",
                fields={"queues": QueueSummarySerializer(many=True)},
            )
        },
    )
    def get(self, request):
        org = request.profile.org
        held = access.levels(request.profile)
        rows = [
            {
                "order_type": order_type,
                "access": held[order_type],
                "pending": queues.pending(org, order_type),
            }
            for order_type in SERVED_TYPES
            if order_type in held
        ]
        return Response({"queues": QueueSummarySerializer(rows, many=True).data})


class WorkQueueView(APIView):
    """The preparation or the packaging queue."""

    permission_classes = (IsAuthenticated, HasOrgContext)
    order_type = None

    @extend_schema(
        tags=TAGS,
        parameters=WORK_FILTERS,
        responses={
            200: inline_serializer(
                name="WorkQueueResponse",
                fields={
                    "order_type": serializers.CharField(),
                    "items": QueueItemSerializer(many=True),
                    **PAGING,
                },
            )
        },
    )
    def get(self, request):
        _require(request, self.order_type, "VIEW")
        rows = queues.filter_work(
            queues.work_queue(request.profile.org, self.order_type),
            request.query_params,
        )
        page, paging = _page(request, rows)
        return Response(
            {
                "order_type": self.order_type,
                "items": QueueItemSerializer(page, many=True).data,
                **paging,
            }
        )


class DeliveryQueueView(APIView):
    """The delivery queue: open dispatch orders by departure, and how many
    ready sales are on none yet."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=TAGS,
        parameters=DISPATCH_FILTERS,
        responses={
            200: inline_serializer(
                name="DeliveryQueueResponse",
                fields={
                    "dispatch_orders": DispatchOrderSerializer(many=True),
                    **PAGING,
                    "unplanned_sales": serializers.IntegerField(),
                },
            )
        },
    )
    def get(self, request):
        _require(request, "DELIVERY", "VIEW")
        org = request.profile.org
        rows = _dispatch_orders(org).filter(status="OPEN")
        return Response(
            {
                **_dispatch_page(request, rows, "dispatch_orders"),
                "unplanned_sales": queues.unplanned_sales(org).count(),
            }
        )
