"""Adaptador de correo para Microsoft Graph API mediante OAuth 2.0 (REST)."""

import base64
import json
from pathlib import Path
from typing import Optional
import urllib.error
import urllib.parse
import urllib.request

from backend.reporting.agent.audit import STATUS_ACCEPTED, STATUS_FAILED, STATUS_UNCERTAIN
from backend.reporting.agent.email_sender.base import DeliveryResult, EmailAdapter


class MSGraphOAuthAdapter(EmailAdapter):
    def __init__(
        self,
        tenant_id: Optional[str],
        client_id: Optional[str],
        client_secret: Optional[str],
        sender_email: Optional[str],
        timeout_seconds: int = 20,
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.sender_email = sender_email
        self.timeout = timeout_seconds

    @property
    def provider_name(self) -> str:
        return "ms_graph"

    @property
    def is_simulation(self) -> bool:
        return False

    def _get_access_token(self) -> str:
        """Obtiene un token de acceso mediante el flujo OAuth 2.0 Client Credentials de Azure AD / Entra ID."""
        if not self.tenant_id or not self.client_id or not self.client_secret:
            raise ValueError("Faltan credenciales requeridas de Microsoft Graph (tenant_id, client_id, client_secret).")

        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(token_url, data=data, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_json = json.loads(resp.read().decode("utf-8"))
                token = resp_json.get("access_token")
                if not token:
                    raise ValueError("La respuesta de Microsoft Entra ID no contuvo 'access_token'.")
                return token
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")[:200]
            raise ValueError(f"Fallo de autenticación con Microsoft Graph (HTTP {err.code}): {err_body}") from None
        except (urllib.error.URLError, TimeoutError) as err:
            raise ValueError(f"No se pudo contactar el servidor de identidad de Microsoft: {err}") from None

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

        sender_endpoint = (
            f"https://graph.microsoft.com/v1.0/users/{self.sender_email}/sendMail"
            if self.sender_email
            else "https://graph.microsoft.com/v1.0/me/sendMail"
        )

        attachments = []
        if pdf_path.exists():
            pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode("utf-8")
            attachments.append({
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": pdf_filename,
                "contentType": "application/pdf",
                "contentBytes": pdf_b64,
            })

        graph_payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": recipient_email}}],
                "attachments": attachments,
            },
            "saveToSentItems": "true",
        }

        data_bytes = json.dumps(graph_payload).encode("utf-8")
        req = urllib.request.Request(
            sender_endpoint,
            data=data_bytes,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                # Microsoft Graph sendMail devuelve HTTP 202 Accepted con cuerpo vacío
                # providerMessageId permanece como None de manera conforme
                return DeliveryResult(
                    status=STATUS_ACCEPTED,
                    is_simulation=False,
                    provider_name=self.provider_name,
                    provider_message_id=None,
                    response_details=f"Solicitud aceptada por Microsoft Graph (HTTP {resp.status})",
                )
        except urllib.error.HTTPError as err:
            if err.code == 504:
                return DeliveryResult(
                    status=STATUS_UNCERTAIN,
                    is_simulation=False,
                    provider_name=self.provider_name,
                    error="HTTP 504 Gateway Timeout: no se pudo confirmar la recepción en Microsoft Graph.",
                )
            err_body = err.read().decode("utf-8", errors="replace")[:200]
            return DeliveryResult(
                status=STATUS_FAILED,
                is_simulation=False,
                provider_name=self.provider_name,
                error=f"Error devuelto por Microsoft Graph (HTTP {err.code}): {err_body}",
            )
        except (TimeoutError, urllib.error.URLError) as err:
            return DeliveryResult(
                status=STATUS_UNCERTAIN,
                is_simulation=False,
                provider_name=self.provider_name,
                error=f"Timeout o error de red durante la transmisión a Microsoft Graph: {err}",
            )
