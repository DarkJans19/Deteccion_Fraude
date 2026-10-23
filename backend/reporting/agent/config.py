"""Configuración del agente de análisis de fraude y despacho de informes."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = ROOT_DIR / "backend" / "runtime" / "reports"


def get_bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on", "si", "sí")


def get_list_env(name: str, default: str = "") -> List[str]:
    raw = os.getenv(name, default).strip()
    if not raw:
        return []
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


@dataclass
class AgentConfig:
    timezone_name: str = "America/Bogota"
    auto_send_enabled: bool = False
    email_provider: str = "mock"
    allowed_recipient_domains: List[str] = field(default_factory=list)
    allowed_recipient_emails: List[str] = field(default_factory=list)
    report_retention_days: int = 90
    allow_unmasked_indicators: bool = False
    max_reports_limit: int = 50000
    reports_dir: Path = DEFAULT_REPORTS_DIR

    # Credenciales OAuth 2.0 Gmail
    gmail_client_id: Optional[str] = None
    gmail_client_secret: Optional[str] = None
    gmail_refresh_token: Optional[str] = None
    gmail_sender_email: Optional[str] = None

    # Credenciales OAuth 2.0 Microsoft Graph
    ms_graph_tenant_id: Optional[str] = None
    ms_graph_client_id: Optional[str] = None
    ms_graph_client_secret: Optional[str] = None
    ms_graph_sender_email: Optional[str] = None

    @classmethod
    def from_env(cls) -> "AgentConfig":
        tz = os.getenv("REPORT_TIMEZONE", "").strip() or "America/Bogota"
        provider = os.getenv("EMAIL_PROVIDER", "mock").strip().lower()
        if provider not in ("mock", "gmail", "ms_graph"):
            provider = "mock"

        return cls(
            timezone_name=tz,
            auto_send_enabled=get_bool_env("AUTO_SEND_ENABLED", False),
            email_provider=provider,
            allowed_recipient_domains=get_list_env("ALLOWED_RECIPIENT_DOMAINS"),
            allowed_recipient_emails=get_list_env("ALLOWED_RECIPIENT_EMAILS"),
            report_retention_days=int(os.getenv("REPORT_RETENTION_DAYS", "90")),
            allow_unmasked_indicators=get_bool_env("ALLOW_UNMASKED_INDICATORS", False),
            max_reports_limit=int(os.getenv("MAX_REPORTS_LIMIT", "50000")),
            reports_dir=Path(os.getenv("REPORTS_DIR", str(DEFAULT_REPORTS_DIR))).resolve(),
            gmail_client_id=os.getenv("GMAIL_CLIENT_ID"),
            gmail_client_secret=os.getenv("GMAIL_CLIENT_SECRET"),
            gmail_refresh_token=os.getenv("GMAIL_REFRESH_TOKEN"),
            gmail_sender_email=os.getenv("GMAIL_SENDER_EMAIL"),
            ms_graph_tenant_id=os.getenv("MS_GRAPH_TENANT_ID"),
            ms_graph_client_id=os.getenv("MS_GRAPH_CLIENT_ID"),
            ms_graph_client_secret=os.getenv("MS_GRAPH_CLIENT_SECRET"),
            ms_graph_sender_email=os.getenv("MS_GRAPH_SENDER_EMAIL"),
        )
