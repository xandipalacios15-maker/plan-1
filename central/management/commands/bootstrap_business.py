from getpass import getpass
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from central.models import Appearance, AuditEvent, Business, Human, Membership, ServiceIdentity


class Command(BaseCommand):
    help = "Crear un negocio y su primer administrador humano, sin contraseñas predeterminadas."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--business", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        try:
            validate_email(email)
            if Human.objects.filter(email__iexact=email).exists():
                raise CommandError("La cuenta ya existe; no se modifica.")
            password = getpass("Contraseña nueva: ")
            if password != getpass("Repetir contraseña: "):
                raise CommandError("Las contraseñas no coinciden.")
            human = Human(username=email, email=email, first_name=options["name"])
            validate_password(password, human)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        human.set_password(password)
        human.save()
        business = Business.objects.create(name=options["business"])
        Membership.objects.create(business=business, human=human, role="admin", admin_seat=1)
        Appearance.objects.create(business=business)
        ServiceIdentity.objects.create(business=business)
        AuditEvent.objects.create(business=business, actor=human, action="business.created")
        self.stdout.write(
            self.style.SUCCESS(
                "Negocio y administrador creados. Inicia sesión e invita al segundo administrador."
            )
        )
