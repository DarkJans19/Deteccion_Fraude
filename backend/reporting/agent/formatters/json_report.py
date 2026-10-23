"""Exportador de informe estructurado en formato JSON canonical."""

import json
from pathlib import Path
from typing import Any, Dict

from backend.reporting.agent.metrics import MetricsReport
from backend.reporting.agent.narrative import GeneratedNarrative
from backend.reporting.agent.taxonomy import TAXONOMY_VERSION
from backend.reporting.hasher import digest

REPORT_SPEC_VERSION = "1.0.0"


def build_json_report(
    report_id: str,
    metrics: MetricsReport,
    narrative: GeneratedNarrative,
    allow_unmasked_indicators: bool = False,
) -> Dict[str, Any]:
    """Construye la estructura completa del informe JSON."""
    metrics_dict = metrics.to_dict()

    # Si no se permiten indicadores sin enmascarar, ocultar correos sin máscara en la salida pública
    if not allow_unmasked_indicators:
        for em in metrics_dict["emails_ranking"]:
            em["email"] = em["email_masked"]

    reproducibility_params = {
        "window": metrics_dict["window"],
        "spec_version": REPORT_SPEC_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
        "narrative_method": narrative.method,
    }

    report_document = {
        "report_id": report_id,
        "spec_version": REPORT_SPEC_VERSION,
        "generated_at_utc": metrics.generated_at_utc,
        "methodology": {
            "taxonomy_version": TAXONOMY_VERSION,
            "normalization": "Unicode NFKC, espacios colapsados, minúsculas casefold",
            "narrative_method": narrative.method,
            "hash_algorithm": "SHA-256",
        },
        "reproducibility": {
            "parameters": reproducibility_params,
            "parameters_digest": digest(reproducibility_params),
        },
        "disclaimer": (
            "El presente informe describe exclusivamente patrones de comportamiento y frecuencia observados "
            "en denuncias ciudadanas. La clasificación categorial es heurística y automatizada; ni una denuncia, "
            "ni una mención de correo, ni una clasificación categorial constituyen prueba definitiva ni imputación "
            "formal de culpabilidad contra personas o entidades."
        ),
        "executive_summary": narrative.executive_summary,
        "metrics": metrics_dict,
    }

    return report_document


def export_json_report(
    report_id: str,
    metrics: MetricsReport,
    narrative: GeneratedNarrative,
    output_path: Path,
    allow_unmasked_indicators: bool = False,
) -> str:
    """Serializa y escribe el archivo JSON canonical en disco, devolviendo su hash SHA-256."""
    doc = build_json_report(report_id, metrics, narrative, allow_unmasked_indicators)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True)
    output_path.write_text(serialized, encoding="utf-8")
    return digest(doc)
