"""Adaptador de correo simulado para entornos de desarrollo y pruebas."""

import email.message
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.reporting.agent.audit import STATUS_SIMULATED
from backend.reporting.agent.email_sender.base import DeliveryResult, EmailAdapter


class MockEmailAdapter(EmailAdapter):
    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or (Path(__file__).resolve().parents[3] / "runtime" / "mock_emails")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.last_sent: Optional[dict] = None

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def is_simulation(self) -> bool:
        return True

    def send_email(
        self,
        recipient_email: str,
        subject: str,
        body: str,
        pdf_path: Path,
        pdf_filename: str,
    ) -> DeliveryResult:
        now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        eml_file = self.output_dir / f"mock_email_{now_str}.txt"

        msg = email.message.EmailMessage()
        msg["To"] = recipient_email
        msg["From"] = "antifraude-simulado@seguridad.local"
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=False, usegmt=True)
        msg.set_content(body)

        pdf_bytes = pdf_path.read_bytes() if pdf_path.exists() else b"%PDF-mock"
        msg.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename=pdf_filename,
        )

        eml_file.write_bytes(msg.as_bytes())

        self.last_sent = {
            "recipient": recipient_email,
            "subject": subject,
            "pdf_size": len(pdf_bytes),
            "eml_path": str(eml_file),
        }

        mock_id = f"mock-msg-{now_str}"
        return DeliveryResult(
            status=STATUS_SIMULATED,
            is_simulation=True,
            provider_name=self.provider_name,
            provider_message_id=mock_id,
            response_details=f"Despacho simulado guardado en {eml_file.name}",
        )
