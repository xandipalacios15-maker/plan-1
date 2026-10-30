import math
import os
from pathlib import Path
import time
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError
from runtime import services
from runtime.models import RuntimeSpace


def dedicated_database():
    if not settings.SYNTHETIC_RUNTIME or not os.environ.get("APP_DATABASE"):
        raise CommandError(
            "Define APP_SYNTHETIC_RUNTIME=1 y APP_DATABASE en una base nueva y separada."
        )
    name = str(connection.settings_dict["NAME"])
    if Path(name).expanduser().resolve() == (settings.BASE_DIR / "db.sqlite3").resolve():
        raise CommandError("La base habitual db.sqlite3 está prohibida para este runtime.")


class RunnerCommand(BaseCommand):
    operation = None
    label = "runtime"

    def add_arguments(self, parser):
        parser.add_argument("--business", type=str)
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll", type=float, default=1)

    def handle(self, *args, **options):
        dedicated_database()
        if not math.isfinite(options["poll"]) or not 0.25 <= options["poll"] <= 10:
            raise CommandError("--poll debe estar entre 0.25 y 10 segundos.")
        try:
            services.sandbox()
            spaces = RuntimeSpace.objects.all()
            if options["business"]:
                spaces = spaces.filter(business_id=options["business"])
            if spaces.count() != 1:
                raise CommandError(
                    "Selecciona un único negocio de laboratorio con --business UUID."
                )
            business_id = spaces.get().business_id
            while True:
                result = self.operation(business_id)
                services.health_check(business_id)
                if options["once"] or result:
                    self.stdout.write(f"{self.label}: {int(result)}")
                if options["once"]:
                    return
                time.sleep(options["poll"])
        except KeyboardInterrupt:
            self.stdout.write("Proceso de laboratorio detenido.")
        except PermissionDenied as exc:
            raise CommandError(
                "Runtime bloqueado: revisa la habilitación, marca de laboratorio y ausencia de pedidos."
            ) from exc
        except OperationalError as exc:
            raise CommandError(
                "Fallo de base del laboratorio. El proceso se detuvo; verifica migraciones/bloqueos antes de reiniciar."
            ) from exc
