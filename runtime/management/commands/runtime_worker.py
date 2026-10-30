from runtime.management.base import RunnerCommand
from runtime.services import work_once


class Command(RunnerCommand):
    help = "Ejecuta exclusivamente handlers sintéticos de la cola local."
    operation = staticmethod(work_once)
    label = "reservas_procesadas"
