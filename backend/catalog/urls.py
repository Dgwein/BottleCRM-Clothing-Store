from django.urls import path

from catalog import views

app_name = "api_catalog"

urlpatterns = [
    path("channels/", views.SalesChannelListView.as_view(), name="channels"),
    path("channels/<uid:pk>/", views.SalesChannelDetailView.as_view(), name="channel"),
    path("attributes/", views.AttributeListView.as_view(), name="attributes"),
    path("attributes/<uid:pk>/", views.AttributeDetailView.as_view(), name="attribute"),
    path(
        "attributes/<uid:pk>/options/",
        views.AttributeOptionListView.as_view(),
        name="attribute_options",
    ),
    path(
        "attribute-options/<uid:pk>/",
        views.AttributeOptionDetailView.as_view(),
        name="attribute_option",
    ),
    path("categories/", views.CategoryListView.as_view(), name="categories"),
    path("categories/<uid:pk>/", views.CategoryDetailView.as_view(), name="category"),
    path(
        "categories/<uid:pk>/attributes/",
        views.CategoryAttributeListView.as_view(),
        name="category_attributes",
    ),
    path(
        "category-attributes/<uid:pk>/",
        views.CategoryAttributeDetailView.as_view(),
        name="category_attribute",
    ),
    path("products/", views.ProductListView.as_view(), name="products"),
    path("products/<uid:pk>/", views.ProductDetailView.as_view(), name="product"),
    path(
        "products/<uid:pk>/variants/",
        views.VariantCreateView.as_view(),
        name="product_variants",
    ),
    path("variants/", views.VariantListView.as_view(), name="variants"),
    path("variants/<uid:pk>/", views.VariantDetailView.as_view(), name="variant"),
    path("sizes/", views.SizeListView.as_view(), name="sizes"),
    path("listings/", views.ListingListView.as_view(), name="listings"),
    path("listings/<uid:pk>/", views.ListingDetailView.as_view(), name="listing"),
    path(
        "listings/<uid:pk>/variants/",
        views.ListingVariantCreateView.as_view(),
        name="listing_variants",
    ),
    path(
        "listing-variants/<uid:pk>/",
        views.ListingVariantDetailView.as_view(),
        name="listing_variant",
    ),
    path(
        "import/sources/", views.ImportSourceListView.as_view(), name="import_sources"
    ),
    path("import/runs/", views.ImportRunListView.as_view(), name="import_runs"),
    path(
        "import/runs/<uid:pk>/",
        views.ImportRunDetailView.as_view(),
        name="import_run",
    ),
    path(
        "import/runs/<uid:pk>/import/",
        views.ImportRunCommitView.as_view(),
        name="import_run_commit",
    ),
]
