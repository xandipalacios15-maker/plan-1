from django.urls import path
from . import views

urlpatterns = [
    path("catalog/<str:kind>/", views.catalog, name="catalog"),
    path("catalog/<str:kind>/new/", views.catalog_edit, name="catalog_create"),
    path("catalog/<str:kind>/<uuid:record_id>/edit/", views.catalog_edit, name="catalog_edit"),
    path("orders/", views.orders, name="orders"),
    path("orders/new/", views.order_create, name="order_create"),
    path("orders/<uuid:order_id>/", views.order_detail, name="order_detail"),
    path("orders/<uuid:order_id>/private/", views.order_private, name="order_private"),
    path("orders/<uuid:order_id>/status/", views.order_status, name="order_status"),
    path("orders/<uuid:order_id>/access/", views.order_access, name="order_access"),
    path(
        "orders/<uuid:order_id>/access/<int:grant_id>/revoke/",
        views.revoke_access,
        name="revoke_access",
    ),
    path("roles/", views.roles, name="operational_roles"),
]
