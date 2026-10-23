"""Orquestador central del agente de análisis, generación de informes y despacho."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from pymongo.database import Database

from backend.reporting.agent.audit import AuditStore
from backend.reporting.agent.config import AgentConfig
from backend.reporting.agent.data_access import ReadOnlyReportsStore
from backend.reporting.agent.date_rules import QueryWindow, build_query_window
from backend.reporting.agent.email_sender.base import DeliveryResult
from backend.reporting.agent.email_sender.service import DeliveryService, OperatorPermissions
from backend.reporting.agent.formatters.json_report import export_json_report
from backend.reporting.agent.formatters.pdf_report import build_pdf_report
from backend.reporting.agent.metrics import MetricsReport, compute_metrics
from backend.reporting.agent.narrative import GeneratedNarrative, generate_narrative
from backend.reporting.hasher import digest


@dataclass
class AnalysisResult:
    report_id: str
    window: QueryWindow
    metrics: MetricsReport
    narrative: GeneratedNarrative
    pdf_path: Path
    pdf_sha256: str
    json_path: Path
    json_sha256: str


class FraudReportingAgent:
    def __init__(self, database: Database, config: Optional[AgentConfig] = None):
        self.database = database
        self.config = config or AgentConfig.from_env()
        self.store = ReadOnlyReportsStore(database)
        self.audit = AuditStore(database)
        self.audit.initialize_indexes()
        self.delivery_service = DeliveryService(self.audit, self.config)

    def analyze_period(
        self,
        start_date_str: str,
        end_date_str: str,
        timezone_name: Optional[str] = None,
        recipient_name: Optional[str] = None,
        recipient_entity: Optional[str] = None,
    ) -> AnalysisResult:
        """Ejecuta el pipeline completo de análisis, cálculo de métricas y generación de archivos."""
        tz = timezone_name or self.config.timezone_name
        window = build_query_window(start_date_str, end_date_str, tz)

        # 1. Consulta en modo de solo lectura y verificación de integridad
        valid_reports, invalid_reports, events = self.store.fetch_period_data(
            window=window, limit=self.config.max_reports_limit
        )

        # 2. Cálculo verificable de métricas
        metrics = compute_metrics(
            valid_reports=valid_reports,
            invalid_reports=invalid_reports,
            all_events=events,
            window=window,
        )

        # 3. Generación de narrativa (LLM o plantilla determinista)
        narrative = generate_narrative(metrics, recipient_name, recipient_entity)

        # 4. Identificador único determinista del informe generado
        now_utc = datetime.now(timezone.utc)
        params_hash = digest({
            "window": window.to_dict(),
            "total": metrics.total_reports_in_period,
            "valid": len(valid_reports),
        })[:8].upper()
        report_id = f"INF-{now_utc.strftime('%Y%m%d')}-{params_hash}"

        # 5. Guardado estructurado de archivos (PDF y JSON)
        period_folder = now_utc.strftime("%Y/%m")
        target_dir = self.config.reports_dir / period_folder
        target_dir.mkdir(parents=True, exist_ok=True)

        json_path = target_dir / f"informe_{report_id}.json"
        pdf_path = target_dir / f"informe_{report_id}.pdf"

        json_sha = export_json_report(
            report_id=report_id,
            metrics=metrics,
            narrative=narrative,
            output_path=json_path,
            allow_unmasked_indicators=self.config.allow_unmasked_indicators,
        )

        pdf_sha = build_pdf_report(
            report_id=report_id,
            metrics=metrics,
            narrative=narrative,
            output_path=pdf_path,
            allow_unmasked_indicators=self.config.allow_unmasked_indicators,
        )

        # 6. Registrar generación en la colección de auditoría
        self.audit.record_generation(
            report_id=report_id,
            period=window.to_dict(),
            metrics_summary={
                "total": metrics.total_reports_in_period,
                "usable": metrics.usable_message_reports,
                "legacy": metrics.empty_or_legacy_message_reports,
                "invalid": metrics.invalid_data_reports,
            },
            pdf_sha256=pdf_sha,
            json_sha256=json_sha,
            parameters_digest=params_hash,
        )

        return AnalysisResult(
            report_id=report_id,
            window=window,
            metrics=metrics,
            narrative=narrative,
            pdf_path=pdf_path,
            pdf_sha256=pdf_sha,
            json_path=json_path,
            json_sha256=json_sha,
        )

    def dispatch_report(
        self,
        analysis_result: AnalysisResult,
        recipient_email: str,
        operator_permissions: OperatorPermissions,
        interactive_confirmed: bool = False,
    ) -> Tuple[bool, str, Optional[DeliveryResult]]:
        """Despacha el informe ya generado respetando controles de autorización e idempotencia."""
        return self.delivery_service.dispatch_report(
            report_id=analysis_result.report_id,
            recipient_email=recipient_email,
            subject=analysis_result.narrative.email_subject,
            body=analysis_result.narrative.email_body,
            pdf_path=analysis_result.pdf_path,
            pdf_sha256=analysis_result.pdf_sha256,
            operator_permissions=operator_permissions,
            interactive_confirmed=interactive_confirmed,
        )
