import uuid
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from central.models import Business, Human, Membership
from operations.models import Supplier, Product, Order, OrderPII, OrderConsent
from operations.services import capture


class Phase2PreservationTests(TransactionTestCase):
    def test_runtime_migration_preserves_phase2_order_pii_and_consents_without_emitting_events(
        self,
    ):
        MigrationExecutor(connection).migrate([("runtime", None)])
        try:
            business = Business.objects.create(name="Negocio previo sintético")
            human = Human.objects.create_user(
                username="phase2-before-runtime@example.test",
                email="phase2-before-runtime@example.test",
                password="Unusable-synthetic-passphrase!273",
            )
            member = Membership.objects.create(
                business=business, human=human, role="admin", admin_seat=1
            )
            supplier = Supplier.objects.create(business=business, name="Proveedor previo sintético")
            product = Product.objects.create(
                business=business,
                supplier=supplier,
                name="Producto previo sintético",
                sku="BEFORE-RUNTIME",
                price="10.00",
            )
            order, _ = capture(
                member,
                {
                    "customer_name": "Cliente previo sintético",
                    "address": "Dirección sintética de prueba",
                    "postal_code": "",
                    "whatsapp": "3000000000",
                    "email": "",
                    "email_purpose": "",
                    "product": str(product.pk),
                    "variant": "",
                    "quantity": 1,
                    "unit_price": "10.00",
                    "source": "manual",
                    "campaign": "",
                    "operational_use": True,
                    "idempotency_key": str(uuid.uuid4()),
                },
            )
            MigrationExecutor(connection).migrate([("runtime", "0001_initial")])
            from runtime.models import Event, Job, SandboxDatabase

            self.assertEqual(Order.objects.get(pk=order.pk).status, "received")
            self.assertEqual(
                OrderPII.objects.get(order=order).customer_name, "Cliente previo sintético"
            )
            self.assertFalse(OrderConsent.objects.get(order=order).whatsapp_service)
            self.assertEqual(Event.objects.count(), 0)
            self.assertEqual(Job.objects.count(), 0)
            self.assertEqual(SandboxDatabase.objects.count(), 0)
            self.assertEqual(Membership.objects.get(human=human).business_id, business.pk)
        finally:
            MigrationExecutor(connection).migrate([("runtime", "0001_initial")])
