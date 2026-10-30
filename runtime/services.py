import uuid
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Exists
from django.utils import timezone
from central.services import audit as human_audit
from operations.models import Order
from . import handlers
from .models import (
    SandboxDatabase,
    RuntimeSpace,
    Event,
    Outbox,
    Job,
    Attempt,
    SyntheticEffect,
    Schedule,
    Heartbeat,
    Alert,
    RuntimeAudit,
    KINDS,
    HANDLERS,
)

LEASE_SECONDS = 60
HEALTH_SECONDS = 120
BACKLOG_LIMIT = 100


def sandbox():
    if not settings.SYNTHETIC_RUNTIME or not SandboxDatabase.objects.filter(pk=1).exists():
        raise PermissionDenied(
            "Solo disponible en una base de laboratorio sintética habilitada explícitamente."
        )
    if Order.objects.exists():
        raise PermissionDenied(
            "Runtime bloqueado: esta base contiene pedidos de Fase 2. Usa una base nueva y separada."
        )


def lock_space(business_id):
    if not settings.SYNTHETIC_RUNTIME:
        raise PermissionDenied("Runtime sintético deshabilitado.")
    # First database operation inside the transaction is a guarded WRITE. Reading
    # first would permit SQLite snapshot-upgrade conflicts between CLI processes.
    # Safety predicates are evaluated in the same statement; unsafe DBs mutate no row.
    spaces = RuntimeSpace.objects.filter(business_id=business_id).filter(
        Exists(SandboxDatabase.objects.filter(pk=1)), ~Exists(Order.objects.all())
    )
    if not spaces.update(revision=F("revision") + 1):
        raise PermissionDenied("No hay laboratorio habilitado sin pedidos para este negocio.")
    return RuntimeSpace.objects.get(business_id=business_id)


def admin_member(member):
    if member.role != "admin" or not member.human.is_active:
        raise PermissionDenied


def log(business_id, actor, action, resource=None, correlation=None, human=None):
    RuntimeAudit.objects.create(
        business_id=business_id,
        actor_kind=actor,
        human=human,
        action=action,
        resource_id=resource,
        correlation_id=correlation,
    )


def human_log(member, action, resource=None, correlation=None):
    log(member.business_id, "human", action, resource, correlation, member.human)
    human_audit(member, f"runtime.{action}", resource or member.business_id)


def pending_count(business_id):
    return (
        Outbox.objects.filter(event__business_id=business_id, dispatched_at__isnull=True).count()
        + Job.objects.filter(business_id=business_id)
        .exclude(state__in=["succeeded", "failed"])
        .count()
    )


def _emit(business_id, kind, handler, subject_id, key, correlation_id, actor="human", human=None):
    if kind not in dict(KINDS) or handler not in dict(HANDLERS):
        raise ValidationError("Solo se admiten tipos y handlers sintéticos del registro cerrado.")
    try:
        subject_id, key, correlation_id = [
            uuid.UUID(str(value)) for value in [subject_id, key, correlation_id]
        ]
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Los identificadores deben ser UUID válidos.")
    event = Event.objects.filter(business_id=business_id, idempotency_key=key).first()
    if event:
        if (event.kind, event.handler, event.subject_id, event.correlation_id) != (
            kind,
            handler,
            subject_id,
            correlation_id,
        ):
            raise ValidationError("La clave ya se utilizó con otro evento.")
        return event, False
    if pending_count(business_id) >= BACKLOG_LIMIT:
        raise ValidationError("Límite de 100 trabajos/eventos pendientes alcanzado.")
    event = Event.objects.create(
        business_id=business_id,
        kind=kind,
        handler=handler,
        subject_id=subject_id,
        idempotency_key=key,
        correlation_id=correlation_id,
    )
    Outbox.objects.create(event=event)
    log(business_id, actor, "event.created", event.pk, event.correlation_id, human)
    return event, True


@transaction.atomic
def emit(member, kind, handler, subject_id, key, correlation_id):
    admin_member(member)
    lock_space(member.business_id)
    return _emit(
        member.business_id, kind, handler, subject_id, key, correlation_id, human=member.human
    )


def beat(business_id, component, now=None):
    Heartbeat.objects.update_or_create(
        business_id=business_id, component=component, defaults={"seen_at": now or timezone.now()}
    )
    Alert.objects.filter(
        business_id=business_id, dedupe_key=f"health:{component}", resolved_at__isnull=True
    ).update(resolved_at=now or timezone.now())


@transaction.atomic
def dispatch(business_id, limit=20):
    space = lock_space(business_id)
    beat(business_id, "dispatcher")
    if space.paused:
        return 0
    rows = list(
        Outbox.objects.filter(event__business_id=business_id, dispatched_at__isnull=True)
        .select_related("event")
        .order_by("event__created_at")[: min(max(limit, 1), 20)]
    )
    for row in rows:
        job, created = Job.objects.get_or_create(
            event=row.event, defaults={"business_id": business_id, "available_at": timezone.now()}
        )
        row.dispatched_at = timezone.now()
        row.save(update_fields=["dispatched_at"])
        if created:
            log(business_id, "dispatcher", "outbox.dispatched", job.pk, row.event.correlation_id)
    return len(rows)


def alert(business_id, code, key, job=None, correlation=None):
    value, _ = Alert.objects.get_or_create(
        business_id=business_id,
        dedupe_key=key,
        defaults={"code": code, "job": job, "correlation_id": correlation},
    )
    if value.resolved_at:
        value.resolved_at = None
        value.acknowledged_at = None
        value.acknowledged_by = None
        value.save(update_fields=["resolved_at", "acknowledged_at", "acknowledged_by"])
    return value


def _failed_attempt(job, code, now, permanent=False):
    Attempt.objects.filter(job=job, token=job.lease_token, finished_at__isnull=True).update(
        code=code, finished_at=now
    )
    job.lease_token = None
    job.lease_until = None
    job.last_code = code
    if permanent or job.attempts >= job.max_attempts:
        job.state = "failed"
        job.finished_at = now
        alert(
            job.business_id,
            "permanent_failure" if permanent else "retries_exhausted",
            f"job:{job.pk}:failed",
            job,
            job.event.correlation_id,
        )
    else:
        job.state = "retry_wait"
        job.available_at = now + timedelta(seconds=min(2**job.attempts, 60))
    job.save()
    log(
        job.business_id,
        "worker",
        "job.failed" if job.state == "failed" else "job.retry_scheduled",
        job.pk,
        job.event.correlation_id,
    )


@transaction.atomic
def claim(business_id, now=None):
    space = lock_space(business_id)
    now = now or timezone.now()
    beat(business_id, "worker", now)
    # Recover abandoned reservations, even while the lane is paused; no execution.
    stale = Job.objects.filter(
        business_id=business_id, state="running", lease_until__lte=now
    ).select_related("event")
    for job in list(stale):
        _failed_attempt(job, "lease_expired", now)
        alert(business_id, "lease_expired", f"job:{job.pk}:lease", job, job.event.correlation_id)
    if space.paused or Job.objects.filter(business_id=business_id, state="running").exists():
        return None
    if (
        Attempt.objects.filter(
            job__business_id=business_id, started_at__gt=now - timedelta(minutes=1)
        ).count()
        >= space.claims_per_minute
    ):
        return None
    job = (
        Job.objects.filter(
            business_id=business_id, state__in=["queued", "retry_wait"], available_at__lte=now
        )
        .select_related("event")
        .order_by("available_at", "created_at")
        .first()
    )
    if not job:
        return None
    if job.attempts >= job.max_attempts:
        job.state = "failed"
        job.finished_at = now
        job.last_code = "attempt_limit"
        job.save()
        alert(
            business_id, "retries_exhausted", f"job:{job.pk}:failed", job, job.event.correlation_id
        )
        return None
    job.state = "running"
    job.attempts += 1
    job.lease_token = uuid.uuid4()
    job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
    job.save()
    Attempt.objects.create(job=job, number=job.attempts, token=job.lease_token, started_at=now)
    log(business_id, "worker", "job.claimed", job.pk, job.event.correlation_id)
    return job


@transaction.atomic
def complete(business_id, job_id, token, failure=None, now=None):
    lock_space(business_id)
    now = now or timezone.now()
    job = Job.objects.select_related("event").get(pk=job_id, business_id=business_id)
    if job.state != "running" or job.lease_token != token or job.lease_until <= now:
        return False
    if failure:
        _failed_attempt(
            job,
            "permanent_failure" if failure == "permanent" else "handler_failed",
            now,
            permanent=failure == "permanent",
        )
        return True
    SyntheticEffect.objects.get_or_create(event=job.event, defaults={"job": job})
    Attempt.objects.filter(job=job, token=token).update(code="synthetic_recorded", finished_at=now)
    job.state = "succeeded"
    job.finished_at = now
    job.lease_token = None
    job.lease_until = None
    job.last_code = "synthetic_recorded"
    job.save()
    log(business_id, "worker", "job.succeeded", job.pk, job.event.correlation_id)
    return True


def work_once(business_id):
    job = claim(business_id)
    if job is None:
        return False
    failure = None
    try:
        handlers.execute(job.event.handler, job.attempts)
    except handlers.PermanentFailure:
        failure = "permanent"
    except Exception:
        # Never persist/print exception messages, arguments, payloads or secrets.
        failure = "transient"
    complete(business_id, job.pk, job.lease_token, failure)
    return True


@transaction.atomic
def control_space(member, paused, rate=None):
    admin_member(member)
    space = lock_space(member.business_id)
    if rate is not None and (isinstance(rate, bool) or not 1 <= int(rate) <= 60):
        raise ValidationError("El límite debe estar entre 1 y 60 reclamaciones por minuto.")
    space.paused = bool(paused)
    if rate is not None:
        space.claims_per_minute = int(rate)
    space.save(update_fields=["paused", "claims_per_minute"])
    human_log(member, "executor.paused" if paused else "executor.resumed", member.business_id)


@transaction.atomic
def control_job(member, job_id, pause):
    admin_member(member)
    lock_space(member.business_id)
    job = Job.objects.get(pk=job_id, business_id=member.business_id)
    if pause and job.state in {"queued", "retry_wait"}:
        job.resume_state = job.state
        job.state = "paused"
    elif not pause and job.state == "paused" and job.resume_state in {"queued", "retry_wait"}:
        job.state = job.resume_state
        job.resume_state = ""
    else:
        raise ValidationError(
            "Solo se pausan trabajos en cola/espera. Un trabajo en ejecución termina su intento; no se interrumpe."
        )
    job.save(update_fields=["state", "resume_state"])
    human_log(member, "job.paused" if pause else "job.resumed", job.pk, job.event.correlation_id)


@transaction.atomic
def create_schedule(member, interval_seconds, max_runs):
    admin_member(member)
    lock_space(member.business_id)
    if Schedule.objects.filter(business_id=member.business_id, enabled=True).count() >= 20:
        raise ValidationError("Máximo de 20 programaciones activas.")
    schedule = Schedule(
        business_id=member.business_id,
        interval_seconds=interval_seconds,
        max_runs=max_runs,
        next_run_at=timezone.now(),
    )
    schedule.full_clean()
    schedule.save()
    human_log(member, "schedule.created", schedule.pk, schedule.correlation_id)
    return schedule


@transaction.atomic
def control_schedule(member, schedule_id, enabled):
    admin_member(member)
    lock_space(member.business_id)
    schedule = Schedule.objects.get(pk=schedule_id, business_id=member.business_id)
    if enabled and (
        schedule.runs >= schedule.max_runs
        or Schedule.objects.filter(business_id=member.business_id, enabled=True)
        .exclude(pk=schedule.pk)
        .count()
        >= 20
    ):
        raise ValidationError("Programación agotada o límite de 20 activas alcanzado.")
    schedule.enabled = enabled
    schedule.save(update_fields=["enabled"])
    human_log(
        member,
        "schedule.enabled" if enabled else "schedule.paused",
        schedule.pk,
        schedule.correlation_id,
    )


@transaction.atomic
def tick(business_id, now=None):
    space = lock_space(business_id)
    now = now or timezone.now()
    beat(business_id, "scheduler", now)
    if space.paused:
        return 0
    count = 0
    for schedule in Schedule.objects.filter(
        business_id=business_id, enabled=True, next_run_at__lte=now
    ).order_by("next_run_at")[:20]:
        try:
            _emit(
                business_id,
                "synthetic.tick",
                "record",
                schedule.subject_id,
                uuid.uuid5(schedule.pk, str(schedule.runs + 1)),
                schedule.correlation_id,
                actor="scheduler",
            )
        except ValidationError:
            alert(business_id, "backlog_limit", "backlog_limit")
            break
        schedule.runs += 1
        schedule.next_run_at = now + timedelta(seconds=schedule.interval_seconds)
        if schedule.runs >= schedule.max_runs:
            schedule.enabled = False
        schedule.save(update_fields=["runs", "next_run_at", "enabled"])
        count += 1
    return count


@transaction.atomic
def health_check(business_id, now=None):
    space = lock_space(business_id)
    now = now or timezone.now()
    if space.paused or space.created_at > now - timedelta(seconds=HEALTH_SECONDS):
        return 0
    count = 0
    for component in ["dispatcher", "worker", "scheduler"]:
        heartbeat = Heartbeat.objects.filter(business_id=business_id, component=component).first()
        if not heartbeat or heartbeat.seen_at <= now - timedelta(seconds=HEALTH_SECONDS):
            alert(business_id, f"{component}_stale", f"health:{component}")
            count += 1
    return count


@transaction.atomic
def acknowledge(member, alert_id):
    admin_member(member)
    lock_space(member.business_id)
    value = Alert.objects.get(pk=alert_id, business_id=member.business_id)
    if not value.acknowledged_at:
        value.acknowledged_at = timezone.now()
        value.acknowledged_by = member.human
        value.save(update_fields=["acknowledged_at", "acknowledged_by"])
        human_log(member, "alert.acknowledged", value.pk, value.correlation_id)
