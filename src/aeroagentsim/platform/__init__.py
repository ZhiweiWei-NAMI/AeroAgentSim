"""Import-light platform core, isolated from the legacy core package."""

from .ingress import IngressReceipt
from .simulation import RunSession, Simulation

__all__ = ["IngressReceipt", "RunSession", "Simulation"]
