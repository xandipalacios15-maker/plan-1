from getpass import getpass
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from central.models import Human, Business, Membership, Appearance, ServiceIdentity
from operations.models import Order
from runtime.models import SandboxDatabase, RuntimeSpace
from runtime.management.base import dedicated_database


class Command(BaseCommand):
    help = "Inicializa únicamente una base vacía de laboratorio con cuenta sintética; nunca una base existente."

    @transaction.atomic
    def handle(self, *args, **options):
        dedicated_database()
        if (
            SandboxDatabase.objects.exists()
            or Human.objects.exists()
            or Business.objects.exists()
            or Order.objects.exists()
        ):
            raise CommandError(
                "La base no está vacía o ya fue inicializada. No se modificó; crea otra base separada."
            )
        password = getpass("Contraseña nueva para runtime-admin@example.test: ")
        if password != getpass("Repetir contraseña: "):
            raise CommandError("Las contraseñas no coinciden.")
        human = Human(
            username="runtime-admin@example.test",
            email="runtime-admin@example.test",
            first_name="Administrador sintético",
        )
        try:
            validate_password(password, human)
        except ValidationError as exc:
            raise CommandError("Contraseña no válida para el laboratorio.") from exc
        human.set_password(password)
        human.save()
        business = Business.objects.create(name="Laboratorio sintético Fase 3")
        Membership.objects.create(business=business, human=human, role="admin", admin_seat=1)
        Appearance.objects.create(business=business)
        ServiceIdentity.objects.create(business=business)
        SandboxDatabase.objects.create(pk=1)
        RuntimeSpace.objects.create(business=business)
        self.stdout.write(
            f"Laboratorio creado y pausado. Negocio: {business.pk}. Acceso: runtime-admin@example.test. No se enviaron mensajes."
        )
