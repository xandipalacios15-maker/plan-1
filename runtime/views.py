from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods
from central.views import member_required, context
from .forms import EventForm, SpaceForm, ScheduleForm
from .models import (
    RuntimeSpace,
    Event,
    Job,
    Outbox,
    Schedule,
    Heartbeat,
    Alert,
    RuntimeAudit,
    Attempt,
)
from . import services


@member_required()
def dashboard(request, business_id):
    enabled = settings.SYNTHETIC_RUNTIME
    space = None
    if enabled:
        try:
            services.sandbox()
            space = RuntimeSpace.objects.filter(business_id=business_id).first()
        except PermissionDenied:
            enabled = False
    extra = {"enabled": enabled, "space": space}
    if enabled and space:
        beats = {
            value.component: value.seen_at
            for value in Heartbeat.objects.filter(business_id=business_id)
        }
        extra.update(
            events=Event.objects.filter(business_id=business_id).order_by("-created_at")[:30],
            jobs=Job.objects.filter(business_id=business_id)
            .select_related("event")
            .order_by("-created_at")[:30],
            schedules=Schedule.objects.filter(business_id=business_id).order_by("-created_at")[:20],
            alerts=Alert.objects.filter(business_id=business_id).order_by("-created_at")[:30],
            logs=RuntimeAudit.objects.filter(business_id=business_id)
            .select_related("human")
            .order_by("-created_at")[:40],
            pending=Outbox.objects.filter(
                event__business_id=business_id, dispatched_at__isnull=True
            ).count(),
            health=[
                (
                    component,
                    beats.get(component),
                    bool(
                        beats.get(component)
                        and (timezone.now() - beats[component]).total_seconds()
                        < services.HEALTH_SECONDS
                    ),
                )
                for component in ["dispatcher", "worker", "scheduler"]
            ],
            rate=space.claims_per_minute,
        )
    return render(request, "runtime/dashboard.html", context(request, **extra))


def result_or_error(request, action):
    try:
        action()
    except ValidationError as exc:
        return render(
            request, "runtime/error.html", context(request, errors=exc.messages), status=400
        )
    return redirect("tasks", business_id=request.member.business_id)


@require_http_methods(["GET", "POST"])
@member_required(admin=True)
def new_event(request, business_id):
    services.sandbox()
    get_object_or_404(RuntimeSpace, business_id=business_id)
    form = EventForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.emit(
                request.member,
                **{
                    "kind": form.cleaned_data["kind"],
                    "handler": form.cleaned_data["handler"],
                    "subject_id": form.cleaned_data["subject_id"],
                    "key": form.cleaned_data["idempotency_key"],
                    "correlation_id": form.cleaned_data["correlation_id"],
                },
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect("tasks", business_id=business_id)
    return render(
        request,
        "runtime/form.html",
        context(
            request, form=form, title="Emitir evento sintético", submit="Guardar evento y outbox"
        ),
    )


@require_POST
@member_required(admin=True)
def executor_control(request, business_id):
    form = SpaceForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("Control o límite inválido.")
    return result_or_error(
        request,
        lambda: services.control_space(
            request.member,
            form.cleaned_data["action"] == "pause",
            form.cleaned_data["claims_per_minute"],
        ),
    )


@require_POST
@member_required(admin=True)
def job_control(request, business_id, job_id):
    get_object_or_404(Job, pk=job_id, business_id=business_id)
    if request.POST.get("action") not in {"pause", "resume"}:
        return HttpResponseBadRequest("Acción inválida.")
    return result_or_error(
        request,
        lambda: services.control_job(request.member, job_id, request.POST["action"] == "pause"),
    )


@require_http_methods(["GET", "POST"])
@member_required(admin=True)
def new_schedule(request, business_id):
    services.sandbox()
    get_object_or_404(RuntimeSpace, business_id=business_id)
    form = ScheduleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.create_schedule(request.member, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect("tasks", business_id=business_id)
    return render(
        request,
        "runtime/form.html",
        context(
            request,
            form=form,
            title="Programación sintética limitada",
            submit="Crear programación deshabilitada",
        ),
    )


@require_POST
@member_required(admin=True)
def schedule_control(request, business_id, schedule_id):
    get_object_or_404(Schedule, pk=schedule_id, business_id=business_id)
    if request.POST.get("action") not in {"pause", "resume"}:
        return HttpResponseBadRequest("Acción inválida.")
    return result_or_error(
        request,
        lambda: services.control_schedule(
            request.member, schedule_id, request.POST["action"] == "resume"
        ),
    )


@require_POST
@member_required(admin=True)
def alert_ack(request, business_id, alert_id):
    get_object_or_404(Alert, pk=alert_id, business_id=business_id)
    return result_or_error(request, lambda: services.acknowledge(request.member, alert_id))


@member_required()
def job_detail(request, business_id, job_id):
    services.sandbox()
    job = get_object_or_404(Job.objects.select_related("event"), pk=job_id, business_id=business_id)
    return render(
        request,
        "runtime/job.html",
        context(request, job=job, attempts=Attempt.objects.filter(job=job).order_by("number")),
    )
