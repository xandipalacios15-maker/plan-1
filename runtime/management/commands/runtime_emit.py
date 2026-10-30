import uuid
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from runtime.models import RuntimeSpace, KINDS, HANDLERS
from runtime import services
from runtime.management.base import dedicated_database


class Command(BaseCommand):
    help = "Emite un evento sintético como operador CLI; no suplanta a un humano autenticado."

    def add_arguments(self, parser):
        parser.add_argument("--business")
        parser.add_argument(
            "--kind", choices=[value for value, _ in KINDS], default="synthetic.tick"
        )
        parser.add_argument("--handler", choices=[value for value, _ in HANDLERS], default="record")
        parser.add_argument("--key", type=uuid.UUID)
        parser.add_argument("--subject", type=uuid.UUID)
        parser.add_argument("--correlation", type=uuid.UUID)

    def handle(self, *args, **options):
        dedicated_database()
        try:
            services.sandbox()
            spaces = RuntimeSpace.objects.all()
            if options["business"]:
                spaces = spaces.filter(business_id=options["business"])
            if spaces.count() != 1:
                raise CommandError("Selecciona un único negocio de laboratorio.")
            business_id = spaces.get().business_id
            with transaction.atomic():
                services.lock_space(business_id)
                key = options["key"] or uuid.uuid4()
                event, created = services._emit(
                    business_id,
                    options["kind"],
                    options["handler"],
                    options["subject"] or uuid.uuid5(key, "subject"),
                    key,
                    options["correlation"] or uuid.uuid5(key, "correlation"),
                    actor="operator",
                )
        except PermissionDenied as exc:
            raise CommandError("Solo disponible en laboratorio separado sin pedidos.") from exc
        except ValidationError as exc:
            raise CommandError(
                "Evento inválido, clave conflictiva o límite de pendientes alcanzado."
            ) from exc
        self.stdout.write(
            f"Evento {'creado' if created else 'existente'}: {event.pk}; correlación: {event.correlation_id}"
        )
