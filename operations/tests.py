import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from central.models import Appearance, AuditEvent, Business, Human, Membership, ServiceIdentity
from .forms import CaptureForm, CONSENT_FIELDS, ProductForm
from .models import (
    Supplier,
    Product,
    Variant,
    Campaign,
    Order,
    OrderPII,
    OrderConsent,
    OrderAccess,
    OrderTransition,
    OperationalRole,
)
from .services import capture, assign_access, set_role, transition, pii_allowed

PASSWORD = "Only-synthetic-test-passphrase!826"


class OperationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.business = Business.objects.create(name="Negocio sintético A")
        cls.foreign = Business.objects.create(name="Negocio sintético B")
        cls.admin = Human.objects.create_user(
            username="owner-ops@example.test",
            email="owner-ops@example.test",
            password=PASSWORD,
            first_name="Administrador sintético",
        )
        cls.reader = Human.objects.create_user(
            username="reader-ops@example.test",
            email="reader-ops@example.test",
            password=PASSWORD,
            first_name="Operador sintético",
        )
        cls.outsider = Human.objects.create_user(
            username="outside-ops@example.test", email="outside-ops@example.test", password=PASSWORD
        )
        cls.member = Membership.objects.create(
            business=cls.business, human=cls.admin, role="admin", admin_seat=1
        )
        cls.operator = Membership.objects.create(
            business=cls.business, human=cls.reader, role="viewer"
        )
        cls.other_member = Membership.objects.create(
            business=cls.foreign, human=cls.outsider, role="admin", admin_seat=1
        )
        for business in [cls.business, cls.foreign]:
            Appearance.objects.create(business=business)
            ServiceIdentity.objects.create(business=business)
        cls.supplier = Supplier.objects.create(business=cls.business, name="Proveedor sintético")
        cls.product = Product.objects.create(
            business=cls.business,
            name="Producto sintético",
            sku="SYNTH-1",
            supplier=cls.supplier,
            price="12000.50",
        )
        cls.variant = Variant.objects.create(
            business=cls.business, product=cls.product, name="Azul sintético"
        )
        cls.campaign = Campaign.objects.create(
            business=cls.business, name="Campaña sintética", channel="manual"
        )
        cls.other_supplier = Supplier.objects.create(
            business=cls.foreign, name="Proveedor privado B"
        )
        cls.other_product = Product.objects.create(
            business=cls.foreign,
            name="Producto privado B",
            sku="SYNTH-1",
            supplier=cls.other_supplier,
            price="100.00",
        )
        cls.other_variant = Variant.objects.create(
            business=cls.foreign, product=cls.other_product, name="Privada B"
        )
        cls.other_campaign = Campaign.objects.create(
            business=cls.foreign, name="Campaña privada B", channel="manual"
        )

    def payload(self, **changes):
        return {
            "customer_name": "Cliente Sintético Verificación",
            "address": "Dirección sintética número 123",
            "postal_code": "",
            "whatsapp": "300 000 0000",
            "email": "",
            "email_purpose": "",
            "product": str(self.product.pk),
            "variant": str(self.variant.pk),
            "quantity": "2",
            "unit_price": "12000.50",
            "source": "manual",
            "campaign": str(self.campaign.pk),
            "operational_use": True,
            "idempotency_key": str(uuid.uuid4()),
            **changes,
        }

    def url(self, name, business=None, **kwargs):
        return reverse(name, kwargs={"business_id": (business or self.business).pk, **kwargs})

    def login(self, user=None):
        self.client.force_login(user or self.admin)

    def make_order(self, **changes):
        return capture(self.member, self.payload(**changes))[0]

    def grant(self, order, member=None, purpose="support"):
        member = member or self.operator
        set_role(self.member, member, purpose)
        assign_access(self.member, order, member, purpose)

    def test_capture_creates_atomic_private_data_consent_and_history(self):
        order = self.make_order()
        self.assertEqual(order.status, "received")
        self.assertEqual(order.total, Decimal("24001.00"))
        self.assertEqual(order.currency, "COP")
        self.assertEqual(OrderPII.objects.get(order=order).whatsapp, "+573000000000")
        consent = OrderConsent.objects.get(order=order)
        self.assertTrue(consent.operational_use)
        self.assertEqual(consent.policy_version, "phase2-v1")
        for field in CONSENT_FIELDS[1:]:
            self.assertFalse(getattr(consent, field))
        self.assertEqual(OrderTransition.objects.get(order=order).current, "received")
        self.assertTrue(
            AuditEvent.objects.filter(action="order.created", resource=str(order.pk)).exists()
        )

    def test_contact_and_sharing_consents_are_not_inferred_or_prechecked(self):
        form = CaptureForm(business=self.business)
        for field in CONSENT_FIELDS:
            self.assertFalse(form.fields[field].initial)
        order = self.make_order(email="synthetic@example.test", email_purpose="support")
        consent = OrderConsent.objects.get(order=order)
        self.assertFalse(consent.email_service)
        self.assertFalse(consent.email_marketing)
        self.assertFalse(consent.whatsapp_service)
        self.assertFalse(consent.whatsapp_marketing)

    def test_consents_are_separate_by_channel_and_purpose(self):
        order = self.make_order(whatsapp_service=True, share_supplier=True)
        consent = OrderConsent.objects.get(order=order)
        self.assertTrue(consent.whatsapp_service)
        self.assertTrue(consent.share_supplier)
        for field in ["whatsapp_marketing", "email_service", "email_marketing", "share_carrier"]:
            self.assertFalse(getattr(consent, field))

    def test_validation_rejects_bad_inputs_without_partial_records(self):
        invalid = [
            {"operational_use": False},
            {"quantity": "0"},
            {"quantity": "1000"},
            {"unit_price": "-1"},
            {"unit_price": "1.234"},
            {"customer_name": ""},
            {"address": ""},
            {"whatsapp": "not-phone"},
            {"postal_code": "123"},
            {"email": "bad", "email_purpose": "support"},
            {"email": "synth@example.test"},
            {"email_purpose": "support"},
            {"email_service": True},
            {"email_marketing": True},
            {"idempotency_key": "invalid"},
            {"source": "unknown"},
            {"variant": ""},
        ]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                capture(self.member, self.payload(**change))
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderPII.objects.count(), 0)
        self.assertEqual(OrderConsent.objects.count(), 0)

    def test_colombian_postal_optional_and_international_requires_review(self):
        self.assertTrue(
            CaptureForm(self.payload(postal_code="110111"), business=self.business).is_valid()
        )
        self.business.country = "US"
        self.assertFalse(CaptureForm(self.payload(), business=self.business).is_valid())
        self.assertTrue(
            CaptureForm(
                self.payload(postal_code="12345", whatsapp="+12025550123"), business=self.business
            ).is_valid()
        )

    def test_foreign_references_and_mismatched_variant_are_rejected(self):
        for change in [
            {"product": str(self.other_product.pk)},
            {"variant": str(self.other_variant.pk)},
            {"campaign": str(self.other_campaign.pk)},
        ]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                capture(self.member, self.payload(**change))
        another = Product.objects.create(
            business=self.business,
            name="Otro producto",
            sku="OTHER",
            supplier=self.supplier,
            price="10",
        )
        with self.assertRaises(ValidationError):
            capture(self.member, self.payload(product=str(another.pk)))

    def test_inactive_catalog_and_campaign_source_mismatch_are_rejected(self):
        self.campaign.channel = "meta"
        self.campaign.save()
        with self.assertRaises(ValidationError):
            self.make_order()
        self.product.active = False
        self.product.save()
        with self.assertRaises(ValidationError):
            self.make_order(campaign="")

    def test_idempotent_replay_is_exact_and_does_not_duplicate_audit(self):
        data = self.payload()
        first, created = capture(self.member, data)
        self.assertTrue(created)
        second, created = capture(self.member, data)
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="order.created").count(), 1)
        with self.assertRaises(ValidationError):
            capture(self.member, {**data, "quantity": "3"})
        with self.assertRaises(ValidationError):
            capture(self.member, {**data, "whatsapp_service": True})

    def test_duplicate_new_key_blocked_for_24_hours(self):
        order = self.make_order()
        with self.assertRaises(ValidationError):
            self.make_order(customer_name="  cliente sintético verificación  ")
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=25))
        second = self.make_order()
        self.assertNotEqual(second.pk, order.pk)

    def test_dedup_is_scoped_to_business(self):
        self.make_order()
        foreign_data = self.payload(
            product=str(self.other_product.pk),
            variant=str(self.other_variant.pk),
            campaign=str(self.other_campaign.pk),
        )
        order, _ = capture(self.other_member, foreign_data)
        self.assertEqual(order.business_id, self.foreign.pk)
        self.assertEqual(Order.objects.count(), 2)

    def test_snapshots_survive_catalog_edits(self):
        order = self.make_order()
        Product.objects.filter(pk=self.product.pk).update(name="Nombre nuevo", price="99")
        Variant.objects.filter(pk=self.variant.pk).update(name="Nueva etiqueta")
        order.refresh_from_db()
        self.assertEqual(order.product_label, "Producto sintético")
        self.assertEqual(order.variant_label, "Azul sintético")
        self.assertEqual(order.unit_price, Decimal("12000.50"))

    def test_reader_cannot_capture_or_edit_catalog(self):
        with self.assertRaises(PermissionDenied):
            capture(self.operator, self.payload())
        self.login(self.reader)
        self.assertEqual(
            self.client.post(self.url("order_create"), self.payload()).status_code, 403
        )
        for kind in ["suppliers", "products", "variants", "campaigns"]:
            self.assertEqual(
                self.client.get(self.url("catalog_create", kind=kind)).status_code, 403
            )

    def test_anonymous_redirects_and_cross_business_routes_return_404(self):
        order = self.make_order()
        self.assertEqual(self.client.get(self.url("orders")).status_code, 302)
        self.login(self.outsider)
        for name in ["orders", "order_create", "operational_roles"]:
            self.assertEqual(self.client.get(self.url(name)).status_code, 404)
        for name in ["order_detail", "order_private", "order_access"]:
            self.assertEqual(self.client.get(self.url(name, order_id=order.pk)).status_code, 404)
        self.assertEqual(
            self.client.post(
                self.url("order_status", order_id=order.pk),
                {"status": "validated", "expected_status": "received"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                self.url("catalog_edit", kind="products", record_id=self.product.pk)
            ).status_code,
            404,
        )

    def test_general_views_never_query_or_render_private_data(self):
        order = self.make_order(email="pii-synthetic@example.test", email_purpose="support")
        self.login()
        for name, kwargs in [("orders", {}), ("order_detail", {"order_id": order.pk})]:
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.url(name, **kwargs))
            self.assertEqual(response.status_code, 200)
            for forbidden in [
                "Cliente Sintético Verificación",
                "Dirección sintética número 123",
                "+573000000000",
                "pii-synthetic@example.test",
            ]:
                self.assertNotContains(response, forbidden)
            self.assertFalse(any('"operations_orderpii"' in item["sql"] for item in queries))

    def test_admin_does_not_get_private_access_automatically(self):
        order = self.make_order()
        self.login()
        self.assertEqual(
            self.client.get(self.url("order_private", order_id=order.pk)).status_code, 403
        )
        self.assertTrue(AuditEvent.objects.filter(action="order.pii_denied").exists())

    def test_support_requires_both_role_and_live_order_task(self):
        order = self.make_order()
        self.login(self.reader)
        set_role(self.member, self.operator, "support")
        url = self.url("order_private", order_id=order.pk)
        self.assertEqual(self.client.get(url).status_code, 403)
        assign_access(self.member, order, self.operator, "support")
        response = self.client.get(url)
        self.assertContains(response, "Cliente Sintético Verificación")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertTrue(
            AuditEvent.objects.filter(action="order.pii_viewed", actor=self.reader).exists()
        )
        OrderAccess.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_logistics_task_does_not_grant_access_to_another_order(self):
        first = self.make_order()
        second = self.make_order(quantity="3")
        self.grant(first, purpose="logistics")
        self.assertTrue(pii_allowed(self.operator, first))
        self.assertFalse(pii_allowed(self.operator, second))

    def test_marketing_creative_analytics_never_get_pii_even_with_forged_task(self):
        order = self.make_order()
        self.login(self.reader)
        for role in ["marketing", "creative", "analytics"]:
            set_role(self.member, self.operator, role)
            OrderAccess.objects.create(
                order=order,
                membership=self.operator,
                purpose="support",
                expires_at=timezone.now() + timedelta(hours=1),
                granted_by=self.admin,
            )
            self.assertFalse(pii_allowed(self.operator, order))
            self.assertEqual(
                self.client.get(self.url("order_private", order_id=order.pk)).status_code, 403
            )
            with self.assertRaises(ValidationError):
                assign_access(self.member, order, self.operator, "support")

    def test_grant_cannot_cross_business_or_bypass_admin(self):
        order = self.make_order()
        with self.assertRaises(PermissionDenied):
            assign_access(self.member, order, self.other_member, "support")
        with self.assertRaises(PermissionDenied):
            assign_access(self.operator, order, self.operator, "support")
        with self.assertRaises(PermissionDenied):
            set_role(self.operator, self.operator, "support")

    def test_revoke_task_and_role_change_remove_access(self):
        order = self.make_order()
        self.grant(order)
        self.login()
        grant = OrderAccess.objects.get(order=order)
        response = self.client.post(self.url("revoke_access", order_id=order.pk, grant_id=grant.pk))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(pii_allowed(self.operator, order))
        assign_access(self.member, order, self.operator, "support")
        set_role(self.member, self.operator, "marketing")
        self.assertFalse(OrderAccess.objects.filter(order=order).exists())

    def test_state_transitions_are_local_terminal_and_audited(self):
        order = self.make_order()
        changed = transition(self.member, order.pk, "validated", "received")
        self.assertEqual(changed.status, "validated")
        self.assertEqual(OrderTransition.objects.filter(order=order).count(), 2)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="order.status.validated", resource=str(order.pk)
            ).exists()
        )
        for target in ["received", "cancelled", "shipped", "validated"]:
            with self.assertRaises(ValidationError):
                transition(self.member, order.pk, target, "received")

    def test_cancel_transition_and_stale_or_unauthorized_writes(self):
        order = self.make_order()
        with self.assertRaises(PermissionDenied):
            transition(self.operator, order.pk, "cancelled", "received")
        with self.assertRaises(ValidationError):
            transition(self.member, order.pk, "cancelled", "validated")
        self.grant(order)
        changed = transition(self.operator, order.pk, "cancelled", "received")
        self.assertEqual(changed.status, "cancelled")

    def test_status_http_endpoint_rejects_get_and_bad_states(self):
        order = self.make_order()
        self.login()
        url = self.url("order_status", order_id=order.pk)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(
            self.client.post(url, {"status": "shipped", "expected_status": "received"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                url, {"status": "validated", "expected_status": "received"}
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                url, {"status": "cancelled", "expected_status": "received"}
            ).status_code,
            400,
        )

    def test_database_constraints_enforce_price_quantity_and_idempotency(self):
        order = self.make_order()
        for field, value in [
            ("quantity", 0),
            ("quantity", 1000),
            ("unit_price", "-1"),
            ("status", "shipped"),
        ]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                Order.objects.filter(pk=order.pk).update(**{field: value})

    def test_audit_never_contains_customer_pii_or_request_bodies(self):
        order = self.make_order(email="pii-synthetic@example.test", email_purpose="support")
        self.grant(order)
        self.login(self.reader)
        self.client.get(self.url("order_private", order_id=order.pk))
        text = str(list(AuditEvent.objects.values_list("action", "resource")))
        for private in [
            "Cliente Sintético",
            "Dirección sintética",
            "+573000000000",
            "pii-synthetic@example.test",
            PASSWORD,
        ]:
            self.assertNotIn(private, text)

    def test_catalog_create_edit_and_business_scoped_choices(self):
        self.login()
        response = self.client.post(
            self.url("catalog_create", kind="suppliers"),
            {"name": "Proveedor local nuevo", "active": "on"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            self.url("catalog_edit", kind="products", record_id=self.product.pk),
            {
                "name": "Editado",
                "sku": "SYNTH-1",
                "supplier": str(self.supplier.pk),
                "price": "9000.00",
                "active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Editado")
        form = ProductForm(
            {
                "name": "Intruso",
                "sku": "OTHER",
                "supplier": self.other_supplier.pk,
                "price": "9",
                "active": True,
            },
            business=self.business,
        )
        self.assertFalse(form.is_valid())
        for kind in ["suppliers", "products", "variants", "campaigns"]:
            response = self.client.get(self.url("catalog", kind=kind))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "privado B")

    def test_capture_view_redirects_and_does_not_echo_pii(self):
        self.login()
        response = self.client.post(self.url("order_create"), self.payload(), follow=True)
        self.assertEqual(Order.objects.count(), 1)
        self.assertContains(response, "Pedido local")
        self.assertNotContains(response, "Cliente Sintético Verificación")

    def test_admin_role_and_assignment_forms_validate_same_business(self):
        self.login()
        response = self.client.post(
            self.url("operational_roles"), {"membership": self.operator.pk, "role": "support"}
        )
        self.assertEqual(response.status_code, 302)
        order = self.make_order()
        response = self.client.post(
            self.url("order_access", order_id=order.pk),
            {"membership": self.operator.pk, "purpose": "support"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            self.url("operational_roles"), {"membership": self.other_member.pk, "role": "support"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OperationalRole.objects.filter(membership=self.other_member).exists())
        self.assertTrue(pii_allowed(self.operator, order))

    def test_csrf_protects_operations_and_private_headers(self):
        order = self.make_order()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        urls = [
            self.url("order_create"),
            self.url("operational_roles"),
            self.url("order_access", order_id=order.pk),
            self.url("order_status", order_id=order.pk),
            self.url("catalog_create", kind="products"),
        ]
        for url in urls:
            self.assertEqual(client.post(url, self.payload()).status_code, 403)

    def test_numeric_price_representation_cannot_bypass_dedup(self):
        self.make_order(unit_price="12000.50")
        with self.assertRaises(ValidationError):
            self.make_order(unit_price="12000.5")

    def test_idempotency_unique_constraint_and_duplicate_catalog_sku(self):
        first = self.make_order()
        second = self.make_order(quantity="3")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.filter(pk=second.pk).update(idempotency_key=first.idempotency_key)
        self.login()
        response = self.client.post(
            self.url("catalog_create", kind="products"),
            {
                "name": "Duplicado",
                "sku": self.product.sku,
                "supplier": str(self.supplier.pk),
                "price": "9.00",
                "active": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya existe una referencia equivalente")
        self.assertEqual(
            Product.objects.filter(business=self.business, sku=self.product.sku).count(), 1
        )

    def test_operational_reader_cannot_assign_roles_or_grants(self):
        order = self.make_order()
        self.grant(order)
        self.login(self.reader)
        self.assertEqual(self.client.get(self.url("operational_roles")).status_code, 403)
        self.assertEqual(
            self.client.post(
                self.url("operational_roles"), {"membership": self.operator.pk, "role": "logistics"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(self.url("order_access", order_id=order.pk)).status_code, 403
        )
        grant = OrderAccess.objects.get(order=order)
        self.assertEqual(
            self.client.post(
                self.url("revoke_access", order_id=order.pk, grant_id=grant.pk)
            ).status_code,
            403,
        )

    def test_capture_rolls_back_all_records_when_private_storage_fails(self):
        with patch(
            "operations.services.OrderConsent.objects.create",
            side_effect=RuntimeError("synthetic storage failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.make_order()
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderPII.objects.count(), 0)
        self.assertEqual(OrderTransition.objects.count(), 0)
        self.assertFalse(AuditEvent.objects.filter(action="order.created").exists())

    def test_changed_source_or_campaign_does_not_bypass_duplicate_check(self):
        self.make_order()
        campaign = Campaign.objects.create(
            business=self.business, name="Otra campaña", channel="organic"
        )
        with self.assertRaises(ValidationError):
            self.make_order(source="organic", campaign=str(campaign.pk))

    def test_private_customer_text_is_html_escaped(self):
        order = self.make_order(customer_name="<script>alert(1)</script>")
        self.grant(order)
        self.login(self.reader)
        response = self.client.get(self.url("order_private", order_id=order.pk))
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>")
