"""Contrato futuro; ningún proveedor tiene implementación ni credenciales en Fase 1."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ConnectionStatus:
    provider: str
    state: str = "not_connected"
    detail: str = "Pendiente de una fase posterior y autorización humana."


class IntegrationAdapter(Protocol):
    def status(self) -> ConnectionStatus: ...


class DisconnectedAdapter:
    def __init__(self, provider: str):
        self.provider = provider

    def status(self) -> ConnectionStatus:
        return ConnectionStatus(provider=self.provider)
