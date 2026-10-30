from django.urls import path
from . import views

urlpatterns = [
    path("events/new/", views.new_event, name="runtime_event"),
    path("executor/", views.executor_control, name="runtime_control"),
    path("jobs/<uuid:job_id>/", views.job_detail, name="runtime_job"),
    path("jobs/<uuid:job_id>/control/", views.job_control, name="runtime_job_control"),
    path("schedules/new/", views.new_schedule, name="runtime_schedule"),
    path(
        "schedules/<uuid:schedule_id>/control/",
        views.schedule_control,
        name="runtime_schedule_control",
    ),
    path("alerts/<uuid:alert_id>/ack/", views.alert_ack, name="runtime_alert_ack"),
]
