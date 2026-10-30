from django.urls import include, path
from central import views
from runtime import views as runtime_views

urlpatterns = [
    path("business/<uuid:business_id>/runtime/", include("runtime.urls")),
    path("business/<uuid:business_id>/operations/", include("operations.urls")),
    path("login/", views.sign_in, name="login"),
    path("logout/", views.sign_out, name="logout"),
    path("", views.home, name="home"),
    path("password/", views.password_change, name="password_change"),
    path("profile/", views.profile, name="profile"),
    path("accept/<str:token>/", views.accept_invitation, name="accept"),
    path("business/<uuid:business_id>/", views.dashboard, name="dashboard"),
    path("business/<uuid:business_id>/tasks/", runtime_views.dashboard, name="tasks"),
    path("business/<uuid:business_id>/integrations/", views.integrations, name="integrations"),
    path("business/<uuid:business_id>/invitations/", views.invitations, name="invitations"),
    path("business/<uuid:business_id>/audit/", views.audit_log, name="audit"),
    path("business/<uuid:business_id>/appearance/", views.appearance, name="appearance"),
    path("business/<uuid:business_id>/assets/<str:kind>/", views.asset, name="asset"),
]
