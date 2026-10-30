import uuid
from django.db import models
from django.db.models import Q, F
from django.core.validators import MinValueValidator, MaxValueValidator
from central.models import Business, Human

KINDS = [
    ("synthetic.order.received", "Pedido recibido · sintético"),
    ("synthetic.order.confirmed", "Confirmación · sintética"),
    ("synthetic.supplier.created", "Pedido en proveedor · sintético"),
    ("synthetic.label.created", "Guía · sintética"),
    ("synthetic.tracking.changed", "Cambio de estado logístico · sintético"),
    ("synthetic.delivered", "Entrega · sintética"),
    ("synthetic.incident", "Incidencia · sintética"),
    ("synthetic.collected", "Recaudo · sintético"),
    ("synthetic.tick", "Pulso programado · sintético"),
]
HANDLERS = [
    ("record", "Registrar resultado sintético"),
    ("flaky", "Fallar una vez y reintentar"),
    ("always_fail", "Agotar reintentos sintéticos"),
    ("reject", "Fallo permanente sintético"),
]
STATES = [
    ("queued", "En cola"),
    ("running", "En ejecución"),
    ("retry_wait", "Esperando reintento"),
    ("paused", "Pausada"),
    ("succeeded", "Completada · sintética"),
    ("failed", "Fallida"),
]
ACTORS = [
    ("human", "Humano"),
    ("dispatcher", "Despachador"),
    ("worker", "Worker sintético"),
    ("scheduler", "Scheduler"),
    ("health", "Monitor de salud"),
    ("operator", "Operador de laboratorio CLI"),
]


class SandboxDatabase(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(id=1), name="runtime_one_sandbox_marker")]


class RuntimeSpace(models.Model):
    business = models.OneToOneField(Business, primary_key=True, on_delete=models.PROTECT)
    paused = models.BooleanField(default=True)
    claims_per_minute = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(60)]
    )
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(claims_per_minute__gte=1, claims_per_minute__lte=60),
                name="runtime_bounded_rate",
            )
        ]


class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT)
    kind = models.CharField(max_length=40, choices=KINDS)
    handler = models.CharField(max_length=16, choices=HANDLERS, default="record")
    subject_id = models.UUIDField()
    correlation_id = models.UUIDField(default=uuid.uuid4)
    idempotency_key = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "idempotency_key"], name="runtime_event_idempotency"
            ),
            models.CheckConstraint(
                condition=Q(kind__in=[v for v, _ in KINDS]), name="runtime_synthetic_event_only"
            ),
            models.CheckConstraint(
                condition=Q(handler__in=[v for v, _ in HANDLERS]), name="runtime_known_handler_only"
            ),
        ]


class Outbox(models.Model):
    event = models.OneToOneField(Event, primary_key=True, on_delete=models.PROTECT)
    dispatched_at = models.DateTimeField(null=True)


class Job(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.OneToOneField(Event, on_delete=models.PROTECT)
    business = models.ForeignKey(Business, on_delete=models.PROTECT)
    state = models.CharField(max_length=16, choices=STATES, default="queued")
    resume_state = models.CharField(max_length=16, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    available_at = models.DateTimeField()
    lease_token = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    last_code = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(state__in=[v for v, _ in STATES]), name="runtime_job_valid_state"
            ),
            models.CheckConstraint(
                condition=Q(
                    max_attempts__gte=1, max_attempts__lte=5, attempts__lte=F("max_attempts")
                ),
                name="runtime_job_bounded_attempts",
            ),
            models.CheckConstraint(
                condition=Q(state="running", lease_token__isnull=False, lease_until__isnull=False)
                | (~Q(state="running") & Q(lease_token__isnull=True, lease_until__isnull=True)),
                name="runtime_job_valid_lease",
            ),
        ]
        indexes = [models.Index(fields=["business", "state", "available_at"])]


class Attempt(models.Model):
    job = models.ForeignKey(Job, on_delete=models.PROTECT)
    number = models.PositiveSmallIntegerField()
    token = models.UUIDField(unique=True)
    code = models.CharField(max_length=32, default="running")
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["job", "number"], name="runtime_attempt_number")
        ]


class SyntheticEffect(models.Model):
    event = models.OneToOneField(Event, primary_key=True, on_delete=models.PROTECT)
    job = models.OneToOneField(Job, on_delete=models.PROTECT)
    code = models.CharField(max_length=32, default="synthetic_recorded")
    created_at = models.DateTimeField(auto_now_add=True)


class Schedule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT)
    subject_id = models.UUIDField(default=uuid.uuid4)
    correlation_id = models.UUIDField(default=uuid.uuid4)
    interval_seconds = models.PositiveIntegerField(
        default=60, validators=[MinValueValidator(10), MaxValueValidator(86400)]
    )
    max_runs = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(10)]
    )
    runs = models.PositiveSmallIntegerField(default=0)
    enabled = models.BooleanField(default=False)
    next_run_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    interval_seconds__gte=10,
                    interval_seconds__lte=86400,
                    max_runs__gte=1,
                    max_runs__lte=10,
                    runs__lte=F("max_runs"),
                ),
                name="runtime_schedule_bounded",
            )
        ]


class Heartbeat(models.Model):
    business = models.ForeignKey(Business, on_delete=models.PROTECT)
    component = models.CharField(max_length=16, choices=ACTORS[1:4])
    seen_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "component"], name="runtime_component_heartbeat"
            )
        ]


class Alert(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT)
    dedupe_key = models.CharField(max_length=100)
    code = models.CharField(max_length=32)
    job = models.ForeignKey(Job, null=True, on_delete=models.PROTECT)
    correlation_id = models.UUIDField(null=True)
    acknowledged_at = models.DateTimeField(null=True)
    acknowledged_by = models.ForeignKey(Human, null=True, on_delete=models.PROTECT)
    resolved_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "dedupe_key"], name="runtime_alert_deduplication"
            )
        ]


class RuntimeAudit(models.Model):
    business = models.ForeignKey(Business, on_delete=models.PROTECT)
    human = models.ForeignKey(Human, null=True, on_delete=models.PROTECT)
    actor_kind = models.CharField(max_length=16, choices=ACTORS)
    action = models.CharField(max_length=40)
    resource_id = models.UUIDField(null=True)
    correlation_id = models.UUIDField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
