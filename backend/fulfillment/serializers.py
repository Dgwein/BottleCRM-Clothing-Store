from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from business_hours.calendar import get_default_calendar
from common.models import Teams
from fulfillment import services
from fulfillment.models import (
    CHANNEL_KINDS,
    ORDER_TYPES,
    WORK_STATUS,
    WORKDAY_FIELDS,
    DeliveryOption,
    DeliveryOptionDay,
    DispatchOrder,
    DispatchOrderItem,
    PreparationOrder,
    QueueAssignment,
    Sale,
    SalesChannel,
)
from orders.models import Order

CHANNEL_NEEDED = "A channel pickup or agency option needs its channel."
NAME_TAKEN = "Another channel already has this name."
ORDER_HAS_SALE = "This order already has a sale."
ORDER_CANCELLED = "A cancelled order cannot be fulfilled."
NOT_ON_ORDER = "This line item is not on the sale's order."


class _OrgScoped:
    """Limits each related field in ``scoped`` to the org in the context, so an
    id from another org reads as one that does not exist."""

    scoped: dict = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        org = self.context.get("org")
        for name, model in self.scoped.items():
            field = self.fields[name]
            # A list of ids scopes each one.
            target = getattr(field, "child_relation", field)
            target.queryset = model.objects.filter(org=org)


class SalesChannelSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesChannel
        fields = ("id", "name", "late_penalty", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")
        # Uniqueness is checked below against the caller's org.
        validators = []

    def validate_name(self, value):
        taken = SalesChannel.objects.filter(org=self.context["org"], name__iexact=value)
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise serializers.ValidationError(NAME_TAKEN)
        return value


NO_BUSINESS_HOURS = "This organization has no business hours to import."
IMPORT_ON_CREATE = "Business hours are imported when an option is created."


class DeliveryOptionSerializer(_OrgScoped, serializers.ModelSerializer):
    scoped = {"channel": SalesChannel}
    channel_name = serializers.SerializerMethodField()
    import_business_hours = serializers.BooleanField(
        write_only=True,
        required=False,
        default=False,
        help_text=(
            "On create: copy the org's business hours (weekly opening times and "
            "holidays from today on) as this option's own working days."
        ),
    )

    class Meta:
        model = DeliveryOption
        fields = (
            "id",
            "name",
            "kind",
            "channel",
            "channel_name",
            "location",
            "weekday",
            "departure_time",
            "dispatch_minutes",
            "cost",
            "capacity",
            "is_active",
            *WORKDAY_FIELDS,
            "import_business_hours",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def get_channel_name(self, obj) -> str | None:
        return obj.channel.name if obj.channel_id else None

    def validate_import_business_hours(self, value):
        if value and self.instance is not None:
            raise serializers.ValidationError(IMPORT_ON_CREATE)
        if value:
            self.calendar = get_default_calendar(self.context["org"].id)
            if self.calendar is None:
                raise serializers.ValidationError(NO_BUSINESS_HOURS)
        return value

    def create(self, validated_data):
        wanted = validated_data.pop("import_business_hours", False)
        option = super().create(validated_data)
        if wanted:
            services.import_business_hours(option, self.calendar)
        return option

    def update(self, instance, validated_data):
        validated_data.pop("import_business_hours", None)
        return super().update(instance, validated_data)

    def validate(self, attrs):
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        channel = attrs.get("channel", getattr(self.instance, "channel", None))
        if kind in CHANNEL_KINDS and channel is None:
            raise serializers.ValidationError({"channel": [CHANNEL_NEEDED]})
        return attrs


DATE_TAKEN = "This option already has a setting for this date."
FREE_HAS_NO_OPENING = "A free day has no opening time."


class DeliveryOptionDaySerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryOptionDay
        fields = ("id", "date", "kind", "opens_at", "note")
        read_only_fields = ("id",)
        # The date is checked below against the option in the context.
        validators = []

    def validate(self, attrs):
        if attrs["kind"] == "FREE" and attrs.get("opens_at") is not None:
            raise serializers.ValidationError({"opens_at": [FREE_HAS_NO_OPENING]})
        option = self.context["delivery_option"]
        if option.days.filter(date=attrs["date"]).exists():
            raise serializers.ValidationError({"date": [DATE_TAKEN]})
        return attrs


class PreparationOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = PreparationOrder
        fields = ("id", "line_item", "description", "status", "completed_at")
        read_only_fields = fields


class PackagingOrderSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=WORK_STATUS)
    completed_at = serializers.DateTimeField(allow_null=True)


class DispatchOrderItemSerializer(serializers.ModelSerializer):
    order = serializers.UUIDField(source="sale.order_id", read_only=True)
    order_name = serializers.CharField(source="sale.order.name", read_only=True)
    channel = serializers.UUIDField(source="sale.channel_id", read_only=True)
    channel_name = serializers.CharField(source="sale.channel.name", read_only=True)
    committed_delivery_at = serializers.DateTimeField(
        source="sale.committed_delivery_at", read_only=True
    )

    class Meta:
        model = DispatchOrderItem
        fields = (
            "id",
            "sale",
            "order",
            "order_name",
            "channel",
            "channel_name",
            "committed_delivery_at",
            "estimated_delivery_at",
            "late",
            "cost_share",
            "penalty",
        )
        read_only_fields = fields


class DispatchOrderSerializer(serializers.ModelSerializer):
    delivery_option_name = serializers.CharField(
        source="delivery_option.name", read_only=True
    )
    kind = serializers.CharField(source="delivery_option.kind", read_only=True)
    location = serializers.CharField(source="delivery_option.location", read_only=True)
    capacity = serializers.IntegerField(
        source="delivery_option.capacity", read_only=True, allow_null=True
    )
    items = DispatchOrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = DispatchOrder
        fields = (
            "id",
            "delivery_option",
            "delivery_option_name",
            "kind",
            "location",
            "capacity",
            "status",
            "mode",
            "departs_at",
            "cost",
            "items",
            "closed_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class SaleDispatchSerializer(serializers.Serializer):
    """Where a sale stands on its dispatch order; final once that is closed."""

    dispatch_order = serializers.UUIDField(source="dispatch_order_id")
    status = serializers.CharField(source="dispatch_order.status")
    delivery_option_name = serializers.CharField(
        source="dispatch_order.delivery_option.name"
    )
    departs_at = serializers.DateTimeField(source="dispatch_order.departs_at")
    estimated_delivery_at = serializers.DateTimeField()
    late = serializers.BooleanField()
    cost_share = serializers.DecimalField(max_digits=12, decimal_places=2)
    penalty = serializers.DecimalField(max_digits=12, decimal_places=2)


class SaleSerializer(serializers.ModelSerializer):
    order_name = serializers.CharField(source="order.name", read_only=True)
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    order_status = serializers.CharField(source="order.status", read_only=True)
    channel_name = serializers.CharField(source="channel.name", read_only=True)
    preparation_orders = PreparationOrderSerializer(many=True, read_only=True)
    packaging_order = serializers.SerializerMethodField()
    dispatch = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = (
            "id",
            "order",
            "order_name",
            "order_number",
            "order_status",
            "channel",
            "channel_name",
            "committed_delivery_at",
            "preparation_orders",
            "packaging_order",
            "dispatch",
            "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(PackagingOrderSerializer(allow_null=True))
    def get_packaging_order(self, obj):
        packaging = getattr(obj, "packaging_order", None)
        return None if packaging is None else PackagingOrderSerializer(packaging).data

    @extend_schema_field(SaleDispatchSerializer(allow_null=True))
    def get_dispatch(self, obj):
        item = getattr(obj, "dispatch_item", None)
        return None if item is None else SaleDispatchSerializer(item).data


class PreparationInputSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=255)
    line_item = serializers.UUIDField(required=False, allow_null=True)


class SaleCreateSerializer(_OrgScoped, serializers.Serializer):
    scoped = {"order": Order, "channel": SalesChannel}
    order = serializers.PrimaryKeyRelatedField(queryset=Order.objects.none())
    channel = serializers.PrimaryKeyRelatedField(queryset=SalesChannel.objects.none())
    committed_delivery_at = serializers.DateTimeField()
    preparations = PreparationInputSerializer(many=True, required=False)

    def validate_order(self, order):
        if order.status == "CANCELLED":
            raise serializers.ValidationError(ORDER_CANCELLED)
        if Sale.objects.filter(order=order).exists():
            raise serializers.ValidationError(ORDER_HAS_SALE)
        return order

    def validate(self, attrs):
        """Resolve each line item against the order, which is already the
        caller's org's, so an id from elsewhere is simply not on it."""
        order = attrs["order"]
        preparations = attrs.get("preparations", [])
        wanted = {p["line_item"] for p in preparations if p.get("line_item")}
        on_order = {li.id: li for li in order.line_items.filter(pk__in=wanted)}
        problems = {}
        for index, item in enumerate(preparations):
            line_item = item.get("line_item")
            if line_item is None:
                continue
            if line_item not in on_order:
                problems[index] = {"line_item": [NOT_ON_ORDER]}
            else:
                item["line_item"] = on_order[line_item]
        if problems:
            raise serializers.ValidationError({"preparations": problems})
        attrs["preparations"] = preparations
        return attrs


class DeliveryOptionSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    kind = serializers.CharField()
    location = serializers.CharField()
    cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    capacity = serializers.IntegerField(allow_null=True)


class PlanDepartureSerializer(serializers.Serializer):
    delivery_option = DeliveryOptionSummarySerializer()
    departs_at = serializers.DateTimeField()
    arrives_at = serializers.DateTimeField()
    sales = serializers.ListField(child=serializers.UUIDField())


class PlanSaleSerializer(serializers.Serializer):
    sale = serializers.UUIDField()
    order = serializers.UUIDField()
    order_name = serializers.CharField()
    channel = serializers.UUIDField()
    channel_name = serializers.CharField()
    committed_delivery_at = serializers.DateTimeField()


class PlanAssignmentSerializer(PlanSaleSerializer):
    delivery_option = serializers.UUIDField()
    departs_at = serializers.DateTimeField()
    arrives_at = serializers.DateTimeField()
    late = serializers.BooleanField()
    cost_share = serializers.DecimalField(max_digits=12, decimal_places=2)
    penalty = serializers.DecimalField(max_digits=12, decimal_places=2)


class DispatchPlanSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    currency = serializers.CharField()
    exhaustive = serializers.BooleanField()
    total_cost = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_penalty = serializers.DecimalField(max_digits=14, decimal_places=2)
    total = serializers.DecimalField(max_digits=14, decimal_places=2)
    departures = PlanDepartureSerializer(many=True)
    assignments = PlanAssignmentSerializer(many=True)
    unassigned = PlanSaleSerializer(many=True)


class DispatchRecalculationSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    currency = serializers.CharField()
    exhaustive = serializers.BooleanField()
    dispatch_orders = DispatchOrderSerializer(many=True)
    unassigned = PlanSaleSerializer(many=True)


CLOSE_IS_DELIVERY = "Only delivery has a close level."
TEAM_ASSIGNED = "This team already has an assignment for this order type."


class QueueAssignmentSerializer(_OrgScoped, serializers.ModelSerializer):
    scoped = {"team": Teams}
    team_name = serializers.CharField(source="team.name", read_only=True)

    class Meta:
        model = QueueAssignment
        fields = ("id", "team", "team_name", "order_type", "access", "created_at")
        read_only_fields = ("id", "created_at")
        # Uniqueness is checked below, with a sentence for the caller.
        validators = []

    def validate(self, attrs):
        team = attrs.get("team", getattr(self.instance, "team", None))
        order_type = attrs.get("order_type", getattr(self.instance, "order_type", None))
        access = attrs.get("access", getattr(self.instance, "access", "HANDLE"))
        if access == "CLOSE" and order_type != "DELIVERY":
            raise serializers.ValidationError({"access": [CLOSE_IS_DELIVERY]})
        taken = QueueAssignment.objects.filter(team=team, order_type=order_type)
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise serializers.ValidationError({"order_type": [TEAM_ASSIGNED]})
        return attrs


class QueueSummarySerializer(serializers.Serializer):
    order_type = serializers.ChoiceField(choices=ORDER_TYPES)
    access = serializers.CharField()
    pending = serializers.IntegerField()


class QueueItemSerializer(serializers.Serializer):
    """A preparation or packaging order waiting in its queue."""

    id = serializers.UUIDField()
    sale = serializers.UUIDField(source="sale_id")
    order = serializers.UUIDField(source="sale.order_id")
    order_name = serializers.CharField(source="sale.order.name")
    channel = serializers.UUIDField(source="sale.channel_id")
    channel_name = serializers.CharField(source="sale.channel.name")
    committed_delivery_at = serializers.DateTimeField(
        source="sale.committed_delivery_at"
    )
    description = serializers.SerializerMethodField()
    line_item = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_description(self, obj) -> str:
        return getattr(obj, "description", "")

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_line_item(self, obj):
        return getattr(obj, "line_item_id", None)


SALE_TWICE = "A sale is listed more than once."


class DispatchRecalculationRequestSerializer(serializers.Serializer):
    allow_non_working_days = serializers.BooleanField(default=False)


class ManualDispatchSerializer(_OrgScoped, serializers.Serializer):
    """A dispatch order from chosen sales (``MANUAL``), or from chosen sales
    with the run's remaining room filled by the plan (``HYBRID``)."""

    scoped = {"sales": Sale, "delivery_option": DeliveryOption}
    mode = serializers.ChoiceField(choices=[("MANUAL", "Manual"), ("HYBRID", "Hybrid")])
    sales = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Sale.objects.none(), allow_empty=False
    )
    delivery_option = serializers.PrimaryKeyRelatedField(
        queryset=DeliveryOption.objects.none(), required=False, allow_null=True
    )
    allow_non_working_days = serializers.BooleanField(default=False)

    def validate_sales(self, sales):
        if len({sale.id for sale in sales}) != len(sales):
            raise serializers.ValidationError(SALE_TWICE)
        return sales
