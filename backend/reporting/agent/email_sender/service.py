"""Servicio de despacho, controles de autorización, anti-inyección e idempotencia."""

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from backend.reporting.agent.audit import (
    AuditStore,
    STATUS_ACCEPTED,
    STATUS_FAILED,
    STATUS_SIMULATED,
    STATUS_UNCERTAIN,
)
from backend.reporting.agent.config import AgentConfig
from backend.reporting.agent.email_sender.base import DeliveryResult, EmailAdapter
from backend.reporting.agent.email_sender.gmail_adapter import GmailOAuthAdapter
from backend.reporting.agent.email_sender.mock_adapter import MockEmailAdapter
from backend.reporting.agent.email_sender.ms_graph_adapter import MSGraphOAuthAdapter

# RFC 5322 simplificado estricto
STRICT_EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
)


class EmailValidationError(ValueError):
    pass


class AuthorizationError(PermissionError):
    pass


@dataclass
class OperatorPermissions:
    """Permisos del operador en la sesión CLI o contexto de ejecución."""
    can_send: bool = True
    can_authorize: bool = False  # Permiso exclusivo para conceder o ampliar autorizaciones


def validate_email_address(email: str) -> str:
    """Valida formato y previene inyección de cabeceras CRLF en la dirección."""
    if not isinstance(email, str):
        raise EmailValidationError("La dirección destinataria debe ser una cadena de texto.")
    email = email.strip()
    if "\r" in email or "\n" in email or "%0a" in email.lower() or "%0d" in email.lower():
        raise EmailValidationError("Intento de inyección de cabecera CRLF detectado en la dirección de correo.")
    if not STRICT_EMAIL_REGEX.match(email):
        raise EmailValidationError(f"La dirección '{email}' no es un correo electrónico válido.")
    return email.lower()


def sanitize_header(value: str) -> str:
    """Elimina saltos de línea para prevenir inyección en asuntos y cabeceras."""
    if not value:
        return ""
    clean = value.replace("\r", " ").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", clean)


def build_adapter(config: AgentConfig) -> EmailAdapter:
    """Instancia el adaptador configurado según la cuenta remitente autorizada."""
    provider = config.email_provider.lower()
    if provider == "gmail":
        return GmailOAuthAdapter(
            client_id=config.gmail_client_id,
            client_secret=config.gmail_client_secret,
            refresh_token=config.gmail_refresh_token,
            sender_email=config.gmail_sender_email,
        )
    elif provider == "ms_graph":
        return MSGraphOAuthAdapter(
            tenant_id=config.ms_graph_tenant_id,
            client_id=config.ms_graph_client_id,
            client_secret=config.ms_graph_client_secret,
            sender_email=config.ms_graph_sender_email,
        )
    return MockEmailAdapter()


class DeliveryService:
    def __init__(self, audit_store: AuditStore, config: AgentConfig, adapter: Optional[EmailAdapter] = None):
        self.audit_store = audit_store
        self.config = config
        self.adapter = adapter or build_adapter(config)

    def dispatch_report(
        self,
        report_id: str,
        recipient_email: str,
        subject: str,
        body: str,
        pdf_path: Path,
        pdf_sha256: str,
        operator_permissions: OperatorPermissions,
        interactive_confirmed: bool = False,
    ) -> Tuple[bool, str, Optional[DeliveryResult]]:
        """Ejecuta el flujo seguro de despacho del informe.
        
        Reglas:
        1. Validación de dirección y anti-inyección CRLF.
        2. Verificación de autorización con estricta precedencia.
        3. Comprobación de permisos del operador: una confirmación interactiva no permite
           a un operador sin 'can_authorize' enviar a destinatarios no autorizados.
        4. Verificación de idempotencia (bloquea duplicados y reusa archivos existentes).
        5. Reintentos limitados ante fallos transitorios; detención ante resultados inciertos.
        """
        # 1. Validaciones
        clean_recipient = validate_email_address(recipient_email)
        clean_subject = sanitize_header(subject)

        if not operator_permissions.can_send:
            raise AuthorizationError("El operador no cuenta con permiso para enviar informes ('can_send=False').")

        # 2. Control de Autorización
        is_allowed, can_auto, auth_reason = self.audit_store.check_authorization(
            recipient_email=clean_recipient,
            auto_send_enabled_env=self.config.auto_send_enabled,
            allowed_domains_env=self.config.allowed_recipient_domains,
            allowed_emails_env=self.config.allowed_recipient_emails,
        )

        # Si el envío es mock/simulado, se permite en desarrollo para pruebas
        is_sim = self.adapter.is_simulation

        if not is_sim:
            # Si la autorización fue revocada explícitamente, denegar rotundamente el envío
            if "revocada explícitamente" in auth_reason:
                raise AuthorizationError(f"Envío rechazado: {auth_reason}")

            if not is_allowed:
                # El destinatario no está autorizado previamente.
                # Solo un operador con 'can_authorize' puede validar y conceder autorización.
                if not operator_permissions.can_authorize:
                    raise AuthorizationError(
                        f"Envío rechazado: {auth_reason} Un operador sin permisos de autorización no puede "
                        f"despachar informes a destinatarios no autorizados, incluso mediante confirmación interactiva."
                    )
                if not interactive_confirmed:
                    return False, f"Requiere autorización explícita: {auth_reason}", None

                # Si el operador con permisos autorizó interactivamente, registrar la autorización
                self.audit_store.grant_authorization(
                    recipient=clean_recipient,
                    authorized_by="cli_operator_authorized",
                    days_valid=30,
                    is_domain=False,
                )
            elif not can_auto and not interactive_confirmed:
                return False, f"Envío pendiente de confirmación (AUTO_SEND_ENABLED=false o sin auto-envío). {auth_reason}", None

        # 3. Registro e Idempotencia
        attempt_doc, can_proceed, idemp_reason = self.audit_store.start_delivery_attempt(
            report_id=report_id,
            recipient_email=clean_recipient,
            pdf_sha256=pdf_sha256,
            provider=self.adapter.provider_name,
            is_simulation=is_sim,
        )

        if not can_proceed:
            return False, idemp_reason, None

        delivery_id = attempt_doc["deliveryId"]

        # 4. Despacho físico con reintentos limitados (máximo 3) ante errores recuperables
        max_retries = 3
        current_attempt = 0
        final_result: Optional[DeliveryResult] = None

        while current_attempt < max_retries:
            current_attempt += 1
            final_result = self.adapter.send_email(
                recipient_email=clean_recipient,
                subject=clean_subject,
                body=body,
                pdf_path=pdf_path,
                pdf_filename=f"informe_{report_id}.pdf",
            )

            # Si el resultado fue aceptado o simulado, finalizar exitosamente
            if final_result.status in (STATUS_ACCEPTED, STATUS_SIMULATED):
                self.audit_store.finish_delivery_attempt(
                    delivery_id=delivery_id,
                    status=final_result.status,
                    provider_message_id=final_result.provider_message_id,
                    error_details=None,
                )
                msg = (
                    f"Despacho simulado registrado ({final_result.response_details})"
                    if is_sim
                    else f"Solicitud aceptada por el proveedor {self.adapter.provider_name} (ID: {final_result.provider_message_id or 'N/A'})"
                )
                return True, msg, final_result

            # Si el resultado es INCIERTO (timeout de red tras enviar), DETENER inmediatamente los reintentos
            if final_result.status == STATUS_UNCERTAIN:
                self.audit_store.finish_delivery_attempt(
                    delivery_id=delivery_id,
                    status=STATUS_UNCERTAIN,
                    provider_message_id=None,
                    error_details=final_result.error,
                )
                return False, f"Resultado de despacho incierto: {final_result.error}. Reintento automático suspendido para evitar duplicados.", final_result

            # Si es un fallo (STATUS_FAILED), verificar si reintentar
            if current_attempt < max_retries:
                time.sleep(1.0 * current_attempt)

        # Si agotó los reintentos
        self.audit_store.finish_delivery_attempt(
            delivery_id=delivery_id,
            status=STATUS_FAILED,
            provider_message_id=None,
            error_details=final_result.error if final_result else "Error desconocido",
        )
        return False, f"Despacho fallido tras {max_retries} intentos: {final_result.error if final_result else 'Error'}", final_result
