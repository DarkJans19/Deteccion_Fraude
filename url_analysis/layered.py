"""Orquestacion de reputacion remota y deteccion local de URLs."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .hybrid import predict
from .preprocessing import normalize_url
from .safe_browsing import SafeBrowsingClient, SafeBrowsingError


def analyze(
    url: str,
    model_dir: Path,
    safe_browsing_client: SafeBrowsingClient | None = None,
    require_safe_browsing: bool = False,
    safe_browsing_enabled: bool = True,
    predictor: Callable[[str, Path], dict[str, object]] = predict,
) -> dict[str, object]:
    """Ejecuta Safe Browsing primero y combina su resultado con el modelo local."""

    normalized = normalize_url(url)
    if normalized is None:
        raise ValueError("La URL no es HTTP(S) valida o supera 2048 caracteres")

    if not safe_browsing_enabled:
        reputation: dict[str, object] = {
            "provider": "google_safe_browsing",
            "status": "skipped",
            "verdict": "unknown",
            "is_threat": None,
            "threats": [],
        }
    elif safe_browsing_client is None:
        if require_safe_browsing:
            raise RuntimeError(
                "Falta GOOGLE_SAFE_BROWSING_API_KEY y la consulta remota es obligatoria"
            )
        reputation = {
            "provider": "google_safe_browsing",
            "status": "not_configured",
            "verdict": "unknown",
            "is_threat": None,
            "threats": [],
        }
    else:
        try:
            reputation = safe_browsing_client.check(normalized).as_dict()
        except SafeBrowsingError as error:
            if require_safe_browsing:
                raise RuntimeError(str(error)) from error
            reputation = {
                "provider": "google_safe_browsing",
                "status": "error",
                "verdict": "unknown",
                "is_threat": None,
                "threats": [],
                "error": str(error),
            }

    local = dict(predictor(normalized, model_dir))
    local["url"] = url
    local["safe_browsing"] = reputation
    local["model_classification"] = local.get("classification")
    local["model_risk_level"] = local.get("risk_level")

    if reputation["is_threat"] is True:
        local.update(
            {
                "classification": "suspicious",
                "risk_level": "high",
                "is_suspicious": True,
                "detection_source": "google_safe_browsing",
                "warning": (
                    "Google Safe Browsing reporto una amenaza conocida. "
                    "La fiabilidad y precision del servicio no estan garantizadas."
                ),
            }
        )
    elif reputation.get("verdict") == "no_match" and local.get(
        "superfinanciera_base_url_match"
    ):
        local.update(
            {
                "classification": "official_base_url",
                "risk_level": "low",
                "is_suspicious": False,
                "detection_source": "superfinanciera_registry_and_safe_browsing",
                "warning": (
                    "La URL base coincide con la reportada a la Superfinanciera y "
                    "Google Safe Browsing no devolvio amenazas conocidas. Esto no "
                    "garantiza que el sitio o su contenido sean seguros."
                ),
            }
        )
    elif reputation.get("verdict") == "no_match" and local.get(
        "superfinanciera_host_match"
    ) and local.get("risk_level") == "high":
        local.update(
            {
                "classification": "uncertain",
                "risk_level": "review",
                "is_suspicious": False,
                "detection_source": "layered_review",
                "warning": (
                    "El host coincide con una entidad reportada a la Superfinanciera y "
                    "Google no devolvio amenazas conocidas, pero la ruta sigue siendo "
                    "anomala para el modelo local y requiere revision."
                ),
            }
        )
    else:
        local["detection_source"] = "local_model"
        if reputation.get("verdict") == "no_match":
            local["warning"] = (
                "No encontrar una coincidencia conocida no demuestra que la URL sea "
                "segura; el resultado local tambien es una estimacion estadistica."
            )
        else:
            local["warning"] = (
                "Google Safe Browsing no entrego un veredicto; el resultado procede "
                "solo del modelo local y es una estimacion estadistica."
            )
    return local
