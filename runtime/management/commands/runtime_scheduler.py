from runtime.management.base import RunnerCommand
from runtime.services import tick


class Command(RunnerCommand):
    help = "Emite pulsos sintéticos acotados; no depende de un navegador abierto."
    operation = staticmethod(tick)
    label = "emisiones_programadas"
