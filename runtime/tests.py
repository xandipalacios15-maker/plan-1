import io
import os
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from central.models import Business, Human, Membership, Appearance, ServiceIdentity, AuditEvent
from operations.models import Supplier, Product, Order
from . import services
from .models import (
    SandboxDatabase,
    RuntimeSpace,
    Event,
    Outbox,
    Job,
    Attempt,
    SyntheticEffect,
    Schedule,
    Alert,
    Heartbeat,
    RuntimeAudit,
    KINDS,
)

PASSWORD = "Synthetic-runtime-test-passphrase!642"


@override_settings(SYNTHETIC_RUNTIME=True)
class RuntimeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        SandboxDatabase.objects.create(pk=1)
        cls.business = Business.objects.create(name="Laboratorio sintético A")
        cls.foreign = Business.objects.create(name="Laboratorio sintético B")
        cls.admin = Human.objects.create_user(
            username="runtime-owner@example.test",
            email="runtime-owner@example.test",
            password=PASSWORD,
            first_name="Administrador sintético",
        )
        cls.viewer = Human.objects.create_user(
            username="runtime-reader@example.test",
            email="runtime-reader@example.test",
            password=PASSWORD,
        )
        cls.outside = Human.objects.create_user(
            username="runtime-outside@example.test",
            email="runtime-outside@example.test",
            password=PASSWORD,
        )
        cls.member = Membership.objects.create(
            business=cls.business, human=cls.admin, role="admin", admin_seat=1
        )
        cls.reader = Membership.objects.create(
            business=cls.business, human=cls.viewer, role="viewer"
        )
        cls.other = Membership.objects.create(
            business=cls.foreign, human=cls.outside, role="admin", admin_seat=1
        )
        for business in [cls.business, cls.foreign]:
            RuntimeSpace.objects.create(business=business, paused=False)
            Appearance.objects.create(business=business)
            ServiceIdentity.objects.create(business=business)

    def event(self, handler="record", member=None, **changes):
        return services.emit(
            member or self.member,
            **{
                "kind": "synthetic.order.received",
                "handler": handler,
                "subject_id": uuid.uuid4(),
                "key": uuid.uuid4(),
                "correlation_id": uuid.uuid4(),
                **changes,
            },
        )[0]

    def job(self, handler="record"):
        event = self.event(handler)
        services.dispatch(self.business.pk)
        return Job.objects.get(event=event)

    def url(self, name, business=None, **kwargs):
        return reverse(name, kwargs={"business_id": (business or self.business).pk, **kwargs})

    def test_event_and_outbox_are_atomic_durable_and_idempotent(self):
        data = dict(
            kind="synthetic.tick",
            handler="record",
            subject_id=uuid.uuid4(),
            key=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
        )
        first, created = services.emit(self.member, **data)
        self.assertTrue(created)
        second, created = services.emit(self.member, **data)
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Outbox.objects.count(), 1)
        self.assertEqual(RuntimeAudit.objects.filter(action="event.created").count(), 1)
        with self.assertRaises(ValidationError):
            services.emit(self.member, **{**data, "handler": "flaky"})
        with patch(
            "runtime.services.Outbox.objects.create",
            side_effect=RuntimeError("synthetic storage failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.event()
        self.assertEqual(Event.objects.count(), 1)

    def test_closed_schema_rejects_real_kinds_handlers_and_non_uuid_payloads(self):
        for change in [
            {"kind": "order.received"},
            {"handler": "send_whatsapp"},
            {"subject_id": "synthetic@example.test"},
            {"key": "raw-token"},
            {"correlation_id": "address"},
        ]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.event(**change)
        self.assertEqual(Event.objects.count(), 0)
        self.assertNotIn("payload", [field.name for field in Event._meta.fields])

    def test_all_documented_events_are_synthetic_and_do_not_touch_orders(self):
        for kind, _ in KINDS:
            self.event(kind=kind)
        self.assertEqual(Event.objects.count(), len(KINDS))
        self.assertEqual(Order.objects.count(), 0)

    def test_disabled_or_unmarked_runtime_is_rejected(self):
        with self.settings(SYNTHETIC_RUNTIME=False), self.assertRaises(PermissionDenied):
            self.event()
        SandboxDatabase.objects.all().delete()
        with self.assertRaises(PermissionDenied):
            self.event()

    def test_runtime_rejects_database_containing_phase2_orders(self):
        supplier = Supplier.objects.create(business=self.business, name="Proveedor sintético")
        product = Product.objects.create(
            business=self.business,
            supplier=supplier,
            name="Producto sintético",
            sku="RUNTIME-GUARD",
            price="1.00",
        )
        Order.objects.create(
            business=self.business,
            product=product,
            product_label="Producto sintético",
            quantity=1,
            unit_price="1.00",
            currency="COP",
            source="manual",
            idempotency_key=uuid.uuid4(),
            fingerprint="0" * 64,
            request_hash="0" * 64,
            created_by=self.admin,
        )
        for operation in [
            lambda: self.event(),
            lambda: services.dispatch(self.business.pk),
            lambda: services.work_once(self.business.pk),
            lambda: services.tick(self.business.pk),
        ]:
            with self.assertRaises(PermissionDenied):
                operation()
        self.assertEqual(Event.objects.count(), 0)

    def test_backlog_is_bounded(self):
        with (
            patch("runtime.services.pending_count", return_value=100),
            self.assertRaises(ValidationError),
        ):
            self.event()
        self.assertEqual(Event.objects.count(), 0)

    def test_dispatch_is_idempotent_and_commits_with_outbox(self):
        event = self.event()
        self.assertEqual(services.dispatch(self.business.pk), 1)
        self.assertEqual(services.dispatch(self.business.pk), 0)
        Outbox.objects.filter(event=event).update(dispatched_at=None)
        self.assertEqual(services.dispatch(self.business.pk), 1)
        self.assertEqual(Job.objects.filter(event=event).count(), 1)
        self.assertEqual(RuntimeAudit.objects.filter(action="outbox.dispatched").count(), 1)

    def test_dispatch_failure_rolls_back_job_and_outbox(self):
        event = self.event()
        with patch("runtime.services.log", side_effect=RuntimeError("synthetic audit failure")):
            with self.assertRaises(RuntimeError):
                services.dispatch(self.business.pk)
        self.assertEqual(Job.objects.count(), 0)
        self.assertIsNone(Outbox.objects.get(event=event).dispatched_at)
        self.assertEqual(services.dispatch(self.business.pk), 1)

    def test_worker_success_records_one_effect_and_correlated_audit(self):
        job = self.job()
        self.assertTrue(services.work_once(self.business.pk))
        job.refresh_from_db()
        self.assertEqual(job.state, "succeeded")
        self.assertEqual(job.attempts, 1)
        self.assertEqual(SyntheticEffect.objects.filter(event=job.event).count(), 1)
        self.assertEqual(Attempt.objects.get(job=job).code, "synthetic_recorded")
        logs = RuntimeAudit.objects.filter(resource_id=job.pk)
        self.assertTrue(
            logs.filter(actor_kind="worker", correlation_id=job.event.correlation_id).exists()
        )
        self.assertFalse(services.work_once(self.business.pk))

    def test_duplicate_completion_and_wrong_token_cannot_duplicate_effect(self):
        self.job()
        job = services.claim(self.business.pk)
        self.assertFalse(services.complete(self.business.pk, job.pk, uuid.uuid4()))
        self.assertTrue(services.complete(self.business.pk, job.pk, job.lease_token))
        self.assertFalse(services.complete(self.business.pk, job.pk, job.lease_token))
        self.assertEqual(SyntheticEffect.objects.count(), 1)

    def test_active_lease_excludes_second_claim(self):
        self.job()
        self.job()
        self.assertIsNotNone(services.claim(self.business.pk))
        self.assertIsNone(services.claim(self.business.pk))
        self.assertEqual(Attempt.objects.count(), 1)

    def test_expired_lease_recovers_and_old_worker_is_fenced(self):
        self.job()
        now = timezone.now() + timedelta(seconds=1)
        old = services.claim(self.business.pk, now)
        self.assertFalse(
            services.complete(
                self.business.pk, old.pk, old.lease_token, now=now + timedelta(seconds=61)
            )
        )
        self.assertIsNone(services.claim(self.business.pk, now + timedelta(seconds=61)))
        new = services.claim(self.business.pk, now + timedelta(seconds=64))
        self.assertIsNotNone(new)
        self.assertNotEqual(old.lease_token, new.lease_token)
        self.assertFalse(
            services.complete(
                self.business.pk, old.pk, old.lease_token, now=now + timedelta(seconds=65)
            )
        )
        self.assertTrue(
            services.complete(
                self.business.pk, new.pk, new.lease_token, now=now + timedelta(seconds=65)
            )
        )
        self.assertEqual(SyntheticEffect.objects.count(), 1)
        self.assertEqual(Attempt.objects.get(job=old, number=1).code, "lease_expired")
        self.assertTrue(Alert.objects.filter(code="lease_expired").exists())

    def test_transient_failure_backs_off_then_succeeds(self):
        job = self.job("flaky")
        now = timezone.now() + timedelta(seconds=1)
        with patch("runtime.services.timezone.now", return_value=now):
            self.assertTrue(services.work_once(self.business.pk))
        job.refresh_from_db()
        self.assertEqual(job.state, "retry_wait")
        self.assertEqual(job.available_at, now + timedelta(seconds=2))
        self.assertIsNone(services.claim(self.business.pk, now + timedelta(seconds=1)))
        with patch("runtime.services.timezone.now", return_value=now + timedelta(seconds=3)):
            self.assertTrue(services.work_once(self.business.pk))
        job.refresh_from_db()
        self.assertEqual(job.state, "succeeded")
        self.assertEqual(job.attempts, 2)

    def test_finite_attempts_create_one_terminal_alert(self):
        job = self.job("always_fail")
        now = timezone.now() + timedelta(seconds=1)
        for seconds in [0, 3, 8]:
            with patch(
                "runtime.services.timezone.now", return_value=now + timedelta(seconds=seconds)
            ):
                self.assertTrue(services.work_once(self.business.pk))
        job.refresh_from_db()
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.attempts, 3)
        self.assertEqual(Attempt.objects.filter(job=job).count(), 3)
        self.assertEqual(Alert.objects.filter(code="retries_exhausted", job=job).count(), 1)
        self.assertFalse(services.work_once(self.business.pk))
        self.assertFalse(SyntheticEffect.objects.exists())

    def test_permanent_failure_does_not_retry(self):
        job = self.job("reject")
        self.assertTrue(services.work_once(self.business.pk))
        job.refresh_from_db()
        self.assertEqual((job.state, job.attempts), ("failed", 1))
        self.assertTrue(Alert.objects.filter(code="permanent_failure").exists())

    def test_exception_messages_are_not_persisted_or_logged(self):
        job = self.job()
        with patch(
            "runtime.handlers.execute", side_effect=RuntimeError("NEVER_LOG_SYNTHETIC_SECRET")
        ):
            services.work_once(self.business.pk)
        job.refresh_from_db()
        self.assertEqual(job.last_code, "handler_failed")
        self.assertNotIn(
            "NEVER_LOG", str(list(RuntimeAudit.objects.values_list("action", flat=True)))
        )
        self.assertNotIn("NEVER_LOG", str(list(Alert.objects.values_list("code", flat=True))))

    def test_completion_transaction_rolls_back_effect_if_audit_fails(self):
        self.job()
        job = services.claim(self.business.pk)
        with patch(
            "runtime.services.log", side_effect=RuntimeError("synthetic audit storage failure")
        ):
            with self.assertRaises(RuntimeError):
                services.complete(self.business.pk, job.pk, job.lease_token)
        self.assertFalse(SyntheticEffect.objects.exists())
        job.refresh_from_db()
        self.assertEqual(job.state, "running")
        self.assertTrue(services.complete(self.business.pk, job.pk, job.lease_token))

    def test_pause_executor_blocks_scheduler_dispatch_and_claim_but_not_inflight_completion(self):
        event = self.event()
        services.control_space(self.member, True)
        self.assertEqual(services.dispatch(self.business.pk), 0)
        self.assertIsNone(services.claim(self.business.pk))
        self.assertEqual(services.tick(self.business.pk), 0)
        services.control_space(self.member, False)
        services.dispatch(self.business.pk)
        job = services.claim(self.business.pk)
        services.control_space(self.member, True)
        self.assertTrue(services.complete(self.business.pk, job.pk, job.lease_token))
        self.assertEqual(Job.objects.get(event=event).state, "succeeded")

    def test_pause_resume_job_preserves_queue_and_retry_state(self):
        job = self.job()
        services.control_job(self.member, job.pk, True)
        self.assertIsNone(services.claim(self.business.pk))
        services.control_job(self.member, job.pk, False)
        job.refresh_from_db()
        self.assertEqual(job.state, "queued")
        job = services.claim(self.business.pk)
        with self.assertRaises(ValidationError):
            services.control_job(self.member, job.pk, True)
        services.complete(self.business.pk, job.pk, job.lease_token, "transient")
        services.control_job(self.member, job.pk, True)
        services.control_job(self.member, job.pk, False)
        job.refresh_from_db()
        self.assertEqual(job.state, "retry_wait")

    def test_rate_limit_counts_failures_and_successes(self):
        RuntimeSpace.objects.filter(business=self.business).update(claims_per_minute=1)
        self.job()
        self.job()
        now = timezone.now() + timedelta(seconds=1)
        first = services.claim(self.business.pk, now)
        services.complete(self.business.pk, first.pk, first.lease_token, now=now)
        self.assertIsNone(services.claim(self.business.pk, now + timedelta(seconds=30)))
        self.assertIsNotNone(services.claim(self.business.pk, now + timedelta(seconds=61)))

    def test_scheduler_is_disabled_by_default_finite_and_no_catchup_flood(self):
        schedule = services.create_schedule(self.member, 10, 2)
        now = timezone.now() + timedelta(seconds=1)
        self.assertEqual(services.tick(self.business.pk, now), 0)
        services.control_schedule(self.member, schedule.pk, True)
        self.assertEqual(services.tick(self.business.pk, now), 1)
        self.assertEqual(services.tick(self.business.pk, now), 0)
        self.assertEqual(services.tick(self.business.pk, now + timedelta(days=1)), 1)
        schedule.refresh_from_db()
        self.assertFalse(schedule.enabled)
        self.assertEqual(schedule.runs, 2)
        self.assertEqual(Event.objects.filter(correlation_id=schedule.correlation_id).count(), 2)
        with self.assertRaises(ValidationError):
            services.control_schedule(self.member, schedule.pk, True)

    def test_scheduler_rolls_back_event_and_run_counter_together(self):
        schedule = services.create_schedule(self.member, 10, 2)
        services.control_schedule(self.member, schedule.pk, True)
        original = Schedule.save

        def failing_save(instance, *args, **kwargs):
            if instance.pk == schedule.pk and instance.runs:
                raise RuntimeError("synthetic schedule failure")
            return original(instance, *args, **kwargs)

        with patch.object(Schedule, "save", failing_save), self.assertRaises(RuntimeError):
            services.tick(self.business.pk)
        self.assertFalse(Event.objects.exists())
        schedule.refresh_from_db()
        self.assertEqual(schedule.runs, 0)
        self.assertEqual(services.tick(self.business.pk), 1)

    def test_scheduler_bounds_pause_and_backpressure(self):
        for interval, runs in [(9, 1), (86401, 1), (10, 0), (10, 11)]:
            with self.assertRaises(ValidationError):
                services.create_schedule(self.member, interval, runs)
        schedule = services.create_schedule(self.member, 10, 1)
        services.control_schedule(self.member, schedule.pk, True)
        with patch("runtime.services.pending_count", return_value=100):
            self.assertEqual(services.tick(self.business.pk), 0)
        self.assertTrue(Alert.objects.filter(code="backlog_limit").exists())
        services.control_schedule(self.member, schedule.pk, False)
        self.assertEqual(services.tick(self.business.pk), 0)

    def test_health_no_observation_is_not_success_and_alerts_deduplicate(self):
        now = timezone.now()
        RuntimeSpace.objects.filter(business=self.business).update(
            created_at=now - timedelta(seconds=121)
        )
        self.assertEqual(services.health_check(self.business.pk, now), 3)
        self.assertEqual(services.health_check(self.business.pk, now), 3)
        self.assertEqual(Alert.objects.count(), 3)
        services.beat(self.business.pk, "worker", now)
        self.assertEqual(services.health_check(self.business.pk, now), 2)
        self.assertIsNotNone(Alert.objects.get(code="worker_stale").resolved_at)

    def test_alert_acknowledgement_is_not_resolution_and_is_audited(self):
        self.job("reject")
        services.work_once(self.business.pk)
        value = Alert.objects.get()
        services.acknowledge(self.member, value.pk)
        services.acknowledge(self.member, value.pk)
        value.refresh_from_db()
        self.assertIsNotNone(value.acknowledged_at)
        self.assertIsNone(value.resolved_at)
        self.assertEqual(value.acknowledged_by_id, self.admin.pk)
        self.assertEqual(RuntimeAudit.objects.filter(action="alert.acknowledged").count(), 1)
        self.assertTrue(AuditEvent.objects.filter(action="runtime.alert.acknowledged").exists())

    def test_business_isolation_and_role_authorization(self):
        job = self.job()
        self.client.force_login(self.outside)
        for name, kw in [
            ("runtime_job", {"job_id": job.pk}),
            ("runtime_event", {}),
            ("runtime_schedule", {}),
        ]:
            self.assertEqual(self.client.get(self.url(name, **kw)).status_code, 404)
        self.assertEqual(
            self.client.post(
                self.url("runtime_job_control", self.foreign, job_id=job.pk), {"action": "pause"}
            ).status_code,
            404,
        )
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.url("tasks")).status_code, 200)
        for name, kw in [
            ("runtime_event", {}),
            ("runtime_schedule", {}),
            ("runtime_control", {}),
            ("runtime_job_control", {"job_id": job.pk}),
        ]:
            self.assertEqual(
                self.client.post(self.url(name, **kw), {"action": "pause"}).status_code, 403
            )
        with self.assertRaises(PermissionDenied):
            self.event(member=self.reader)
        with self.assertRaises(PermissionDenied):
            services.control_space(self.reader, True)
        self.assertIsNone(services.claim(self.foreign.pk))

    def test_csrf_and_post_only_controls(self):
        job = self.job()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        for name, kw in [
            ("runtime_control", {}),
            ("runtime_job_control", {"job_id": job.pk}),
            ("runtime_event", {}),
            ("runtime_schedule", {}),
        ]:
            self.assertEqual(client.post(self.url(name, **kw), {}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url("runtime_control")).status_code, 405)

    def test_dashboard_and_forms_report_honest_synthetic_status(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url("tasks"))
        self.assertContains(response, "Sin observación", count=3)
        self.assertContains(response, "Ejecutor sintético")
        self.assertEqual(response["Cache-Control"], "no-store")
        response = self.client.post(
            self.url("runtime_event"),
            {
                "kind": "synthetic.tick",
                "handler": "record",
                "subject_id": uuid.uuid4(),
                "correlation_id": uuid.uuid4(),
                "idempotency_key": uuid.uuid4(),
            },
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            self.url("runtime_control"), {"action": "resume", "claims_per_minute": 61}
        )
        self.assertEqual(response.status_code, 400)
        with self.settings(SYNTHETIC_RUNTIME=False):
            self.assertContains(self.client.get(self.url("tasks")), "Runtime deshabilitado")

    def test_database_constraints_reject_unbounded_attempts_states_and_real_events(self):
        job = self.job()
        for fields in [{"max_attempts": 6}, {"state": "unknown"}, {"state": "running"}]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                Job.objects.filter(pk=job.pk).update(**fields)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Event.objects.filter(pk=job.event_id).update(kind="order.received")

    def test_background_commands_work_without_browser_and_keep_operator_identity(self):
        environment = {"APP_DATABASE": "/tmp/synthetic-runtime-command-test.sqlite3"}
        with patch.dict(os.environ, environment):
            output = io.StringIO()
            call_command("runtime_emit", stdout=output, business=str(self.business.pk))
            self.assertTrue(
                RuntimeAudit.objects.filter(actor_kind="operator", human__isnull=True).exists()
            )
            call_command(
                "runtime_dispatch", once=True, business=str(self.business.pk), stdout=output
            )
            call_command("runtime_worker", once=True, business=str(self.business.pk), stdout=output)
            call_command(
                "runtime_scheduler", once=True, business=str(self.business.pk), stdout=output
            )
            self.assertEqual(SyntheticEffect.objects.count(), 1)
            self.assertIn("reservas_procesadas: 1", output.getvalue())
            self.assertEqual(Heartbeat.objects.filter(business=self.business).count(), 3)

    def test_commands_reject_disabled_runtime_or_missing_database_selection(self):
        with self.settings(SYNTHETIC_RUNTIME=False), self.assertRaises(CommandError):
            call_command("runtime_worker", once=True)
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(CommandError):
            call_command("runtime_init")
        with (
            patch.dict(os.environ, {"APP_DATABASE": "/tmp/synthetic-runtime-command-test.sqlite3"}),
            self.assertRaises(CommandError),
        ):
            call_command("runtime_init")

    def test_cli_key_alone_is_idempotent(self):
        with patch.dict(
            os.environ, {"APP_DATABASE": "/tmp/synthetic-runtime-command-test.sqlite3"}
        ):
            key = uuid.uuid4()
            for _ in range(2):
                call_command(
                    "runtime_emit", business=str(self.business.pk), key=key, stdout=io.StringIO()
                )
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(Outbox.objects.count(), 1)

    def test_scheduler_active_limit_is_enforced(self):
        schedules = [services.create_schedule(self.member, 10, 1) for _ in range(21)]
        for schedule in schedules[:20]:
            services.control_schedule(self.member, schedule.pk, True)
        with self.assertRaises(ValidationError):
            services.control_schedule(self.member, schedules[20].pk, True)
        self.assertEqual(Schedule.objects.filter(enabled=True).count(), 20)

    def test_queue_transaction_starts_with_guarded_write_not_snapshot_read(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            services.claim(self.business.pk)
        statements = [
            item["sql"]
            for item in queries
            if item["sql"].lstrip().upper().startswith(("SELECT", "UPDATE", "INSERT", "DELETE"))
        ]
        self.assertTrue(statements[0].startswith('UPDATE "runtime_runtimespace"'), statements[0])
        self.assertIn("EXISTS", statements[0])


@override_settings(SYNTHETIC_RUNTIME=True)
class RuntimeInitializationTests(TestCase):
    def test_initializer_only_creates_synthetic_account_in_empty_selected_database(self):
        with patch.dict(os.environ, {"APP_DATABASE": "/tmp/phase3-empty-selected-test.sqlite3"}):
            with patch("runtime.management.commands.runtime_init.getpass", return_value=PASSWORD):
                call_command("runtime_init", stdout=io.StringIO())
            self.assertEqual(Human.objects.count(), 1)
            human = Human.objects.get(email="runtime-admin@example.test")
            self.assertTrue(human.check_password(PASSWORD))
            self.assertTrue(RuntimeSpace.objects.get().paused)
            self.assertEqual(SandboxDatabase.objects.count(), 1)
            with patch("runtime.management.commands.runtime_init.getpass") as prompt:
                with self.assertRaises(CommandError):
                    call_command("runtime_init", stdout=io.StringIO())
                prompt.assert_not_called()
            self.assertEqual(Human.objects.count(), 1)
            self.assertEqual(Order.objects.count(), 0)

    def test_initializer_refuses_existing_phase1_records_without_modifying_them(self):
        business = Business.objects.create(name="Registro previo sintético")
        with patch.dict(os.environ, {"APP_DATABASE": "/tmp/phase3-existing-selected-test.sqlite3"}):
            with self.assertRaises(CommandError):
                call_command("runtime_init", stdout=io.StringIO())
        self.assertEqual(Business.objects.get().pk, business.pk)
        self.assertFalse(SandboxDatabase.objects.exists())
        self.assertFalse(RuntimeSpace.objects.exists())

    def test_commands_refuse_the_usual_project_database_even_with_flags(self):
        from types import SimpleNamespace
        from django.conf import settings

        selected = SimpleNamespace(settings_dict={"NAME": str(settings.BASE_DIR / "db.sqlite3")})
        with patch.dict(os.environ, {"APP_DATABASE": str(settings.BASE_DIR / "db.sqlite3")}):
            with patch("runtime.management.base.connection", selected):
                with self.assertRaises(CommandError):
                    call_command("runtime_init", stdout=io.StringIO())
        self.assertFalse(SandboxDatabase.objects.exists())
