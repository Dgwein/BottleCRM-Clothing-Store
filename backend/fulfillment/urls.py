from django.urls import path

from fulfillment import views

app_name = "api_fulfillment"

urlpatterns = [
    path("channels/", views.ChannelListView.as_view(), name="channels"),
    path("channels/<uid:pk>/", views.ChannelDetailView.as_view(), name="channel"),
    path(
        "delivery-options/",
        views.DeliveryOptionListView.as_view(),
        name="delivery_options",
    ),
    path(
        "delivery-options/<uid:pk>/",
        views.DeliveryOptionDetailView.as_view(),
        name="delivery_option",
    ),
    path(
        "delivery-options/<uid:pk>/days/",
        views.DeliveryOptionDayListView.as_view(),
        name="delivery_option_days",
    ),
    path(
        "delivery-options/<uid:pk>/days/<uid:day_pk>/",
        views.DeliveryOptionDayDetailView.as_view(),
        name="delivery_option_day",
    ),
    path("sales/", views.SaleListView.as_view(), name="sales"),
    path("sales/<uid:pk>/", views.SaleDetailView.as_view(), name="sale"),
    path(
        "preparation-orders/<uid:pk>/complete/",
        views.PreparationCompleteView.as_view(),
        name="preparation_complete",
    ),
    path(
        "packaging-orders/<uid:pk>/complete/",
        views.PackagingCompleteView.as_view(),
        name="packaging_complete",
    ),
    path(
        "dispatch-orders/",
        views.DispatchOrderListView.as_view(),
        name="dispatch_orders",
    ),
    # Before the detail route, whose converter would also match "manual".
    path(
        "dispatch-orders/manual/",
        views.DispatchOrderManualView.as_view(),
        name="dispatch_orders_manual",
    ),
    path(
        "dispatch-orders/<uid:pk>/",
        views.DispatchOrderDetailView.as_view(),
        name="dispatch_order",
    ),
    path(
        "dispatch-orders/<uid:pk>/close/",
        views.DispatchOrderCloseView.as_view(),
        name="dispatch_order_close",
    ),
    path("dispatch-plan/", views.DispatchPlanView.as_view(), name="dispatch_plan"),
    path(
        "queue-assignments/",
        views.QueueAssignmentListView.as_view(),
        name="queue_assignments",
    ),
    path(
        "queue-assignments/<uid:pk>/",
        views.QueueAssignmentDetailView.as_view(),
        name="queue_assignment",
    ),
    path("queues/", views.QueueListView.as_view(), name="queues"),
    path(
        "queues/preparation/",
        views.WorkQueueView.as_view(order_type="PREPARATION"),
        name="queue_preparation",
    ),
    path(
        "queues/packaging/",
        views.WorkQueueView.as_view(order_type="PACKAGING"),
        name="queue_packaging",
    ),
    path("queues/delivery/", views.DeliveryQueueView.as_view(), name="queue_delivery"),
]
