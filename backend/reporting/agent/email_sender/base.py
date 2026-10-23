"""Interfaz abstracta y resultados para adaptadores de correo electrónico."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class DeliveryResult:
    status: str                         # ACCEPTED, SIMULATED, FAILED, UNCERTAIN
    is_simulation: bool
    provider_name: str
    provider_message_id: Optional[str] = None
    response_details: Optional[str] = None
    error: Optional[str] = None


class EmailAdapter(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @property
    @abstractmethod
    def is_simulation(self) -> bool:
        pass

    @abstractmethod
    def send_email(
        self,
        recipient_email: str,
        subject: str,
        body: str,
        pdf_path: Path,
        pdf_filename: str,
    ) -> DeliveryResult:
        """Despacha el mensaje con el archivo adjunto.
        
        Reglas:
        - Nunca asume que el correo fue 'entregado'; solo 'aceptado por proveedor' (ACCEPTED)
          o 'simulado' (SIMULATED).
        - Si el proveedor no devuelve un ID de mensaje, provider_message_id queda como None.
        - Ante timeouts de socket tras enviar la carga, debe devolver estado UNCERTAIN.
        """
        pass
