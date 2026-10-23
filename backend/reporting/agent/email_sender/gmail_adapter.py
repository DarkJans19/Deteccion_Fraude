"""Adaptador de correo para Gmail API mediante OAuth 2.0 (REST)."""

import base64
import email.message
from email.utils import formatdate
from pathlib import Path
from typing import Optional
import urllib.error
import urllib.request
import json

from backend.reporting.agent.audit import STATUS_ACCEPTED, STATUS_FAILED, STATUS_UNCERTAIN
from backend.reporting.agent.email_sender.base import DeliveryResult, EmailAdapter


class GmailOAuthAdapter(EmailAdapter):
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

    def __init__(
        self,
        client_id: Optional[str],
        client_secret: Optional[str],
        refresh_token: Optional[str],
        sender_email: Optional[str],
        timeout_seconds: int = 20,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.sender_email = sender_email or "me"
        self.timeout = timeout_seconds

    @property
    def provider_name(self) -> str:
        return "gmail"

    @property
    def is_simulation(self) -> bool:
        return False

    def _get_access_token(self) -> str:
        """Obtiene un token de acceso temporal mediante el refresh_token de OAuth 2.0."""
        if not self.client_id or not self.client_secret or not self.refresh_token:
            raise ValueError("Faltan credenciales requeridas de Gmail OAuth 2.0 (client_id, client_secret, refresh_token).")

        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(self.TOKEN_URL, data=data, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_json = json.loads(resp.read().decode("utf-8"))
                token = resp_json.get("access_token")
                if not token:
                    raise ValueError("La respuesta de OAuth de Google no contuvo 'access_token'.")
                return token
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")[:200]
            raise ValueError(f"Fallo de autenticación con Gmail OAuth (HTTP {err.code}): {err_body}") from None
        except (urllib.error.URLError, TimeoutError) as err:
            raise ValueError(f"No se pudo contactar el servidor de tokens de Google: {err}") from None

    def send_email(
        self,
        recipient_email: str,
        subject: str,
        body: str,
        pdf_path: Path,
        pdf_filename: str,
    ) -> DeliveryResult:
        try:
            token = self._get_access_token()
        except Exception as auth_err:
            return DeliveryResult(
                status=STATUS_FAILED,
                is_simulation=False,
                provider_name=self.provider_name,
                error=str(auth_err),
            )

        # Construir mensaje MIME
        msg = email.message.EmailMessage()
        msg["To"] = recipient_email
        msg["From"] = self.sender_email
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=False, usegmt=True)
        msg.set_content(body)

        if pdf_path.exists():
            msg.add_attachment(
                pdf_path.read_bytes(),
                maintype="application",
                subtype="pdf",
                filename=pdf_filename,
            )

        raw_base64 = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        send_payload = json.dumps({"raw": raw_base64}).encode("utf-8")

        req = urllib.request.Request(
            self.SEND_URL,
            data=send_payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                msg_id = resp_data.get("id")
                return DeliveryResult(
                    status=STATUS_ACCEPTED,
                    is_simulation=False,
                    provider_name=self.provider_name,
                    provider_message_id=msg_id,
                    response_details=f"Aceptado por Gmail API (ID: {msg_id})",
                )
        except urllib.error.HTTPError as err:
            # 504 Gateway Timeout tras enviar datos -> estado incierto
            if err.code == 504:
                return DeliveryResult(
                    status=STATUS_UNCERTAIN,
                    is_simulation=False,
                    provider_name=self.provider_name,
                    error="HTTP 504 Gateway Timeout: no se pudo verificar si Gmail procesó el despacho.",
                )
            err_body = err.read().decode("utf-8", errors="replace")[:200]
            return DeliveryResult(
                status=STATUS_FAILED,
                is_simulation=False,
                provider_name=self.provider_name,
                error=f"Error devuelto por Gmail API (HTTP {err.code}): {err_body}",
            )
        except (TimeoutError, urllib.error.URLError) as err:
            # Error de timeout en socket durante el envío -> incierto
            return DeliveryResult(
                status=STATUS_UNCERTAIN,
                is_simulation=False,
                provider_name=self.provider_name,
                error=f"Timeout o error de red durante la transmisión a Gmail: {err}",
            )


# Importar urllib.parse explícitamente para evitar NameError en _get_access_token
import urllib.parse
