from runtime.management.base import RunnerCommand
from runtime.services import dispatch


class Command(RunnerCommand):
    help = "Despacha outbox sintética durable a la cola local; no conecta un broker externo."
    operation = staticmethod(dispatch)
    label = "outbox_despachados"
