"""Servicio que combina lectura QR, redirecciones y el analizador existente."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from url_analysis.domains import domain_parts, registrable_domain, strip_www
from url_analysis.layered import analyze
from url_analysis.preprocessing import normalize_url
from url_analysis.safe_browsing import SafeBrowsingClient

from .redirects import RedirectResolution, resolve_redirects


RISK_RANK = {"low": 0, "review": 1, "high": 2}


@lru_cache(maxsize=4)
def _trusted_hosts(model_dir_text: str) -> frozenset[str]:
    reference_path = Path(model_dir_text) / "trusted_reference.json"
    if not reference_path.is_file():
        return frozenset()
    with reference_path.open(encoding="utf-8") as source:
        payload = json.load(source)
    return frozenset(strip_www(str(host)) for host in payload.get("hosts", []))


def _edit_distance(first: str, second: str) -> int:
    previous = list(range(len(second) + 1))
    for row, first_character in enumerate(first, start=1):
        current = [row]
        for column, second_character in enumerate(second, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (first_character != second_character),
                )
            )
        previous = current
    return previous[-1]


def _authenticity(
    hostname: str,
    model_dir: Path,
    destination_analysis: dict[str, object],
) -> dict[str, object]:
    """Busca coincidencias exactas y suplantaciones tipograficas de sitios oficiales."""

    if destination_analysis.get("superfinanciera_host_match"):
        return {
            "status": "official_registry_match",
            "official_host_match": True,
            "suspected_official_host": None,
        }

    candidate_host = strip_www(hostname)
    candidate_subdomain, candidate_brand, _ = domain_parts(candidate_host)
    candidate_labels = set(candidate_subdomain.split(".")) if candidate_subdomain else set()
    best: tuple[float, str, str] | None = None

    for trusted_host in _trusted_hosts(str(model_dir.resolve())):
        _, trusted_brand, _ = domain_parts(trusted_host)
        if len(trusted_brand) < 6 or not candidate_brand:
            continue

        score = 0.0
        reason = ""
        if candidate_brand == trusted_brand:
            score, reason = 1.0, "mismo nombre con una terminacion de dominio diferente"
        elif trusted_brand in candidate_labels:
            score, reason = 0.99, "el nombre oficial aparece como subdominio enganoso"
        elif trusted_brand in candidate_brand and candidate_brand != trusted_brand:
            score, reason = 0.97, "el dominio agrega texto al nombre oficial"
        else:
            distance = _edit_distance(candidate_brand, trusted_brand)
            if distance == 1 or (len(trusted_brand) >= 8 and distance == 2):
                score = 0.96 if distance == 1 else 0.91
                reason = "el nombre difiere por muy pocos caracteres del oficial"

        if score and (best is None or score > best[0]):
            best = (score, trusted_host, reason)

    if best is not None:
        return {
            "status": "suspected_impersonation",
            "official_host_match": False,
            "suspected_official_host": best[1],
            "reason": best[2],
        }
    return {
        "status": "not_in_financial_registry",
        "official_host_match": False,
        "suspected_official_host": None,
    }


def _public_analysis(result: dict[str, object]) -> dict[str, object]:
    """Conserva los campos utiles para la interfaz y evita filtrar internals."""

    allowed = {
        "normalized_url",
        "classification",
        "risk_level",
        "is_suspicious",
        "phishing_probability",
        "autoencoder_anomaly_score",
        "superfinanciera_host_match",
        "superfinanciera_base_url_match",
        "detection_source",
        "warning",
        "safe_browsing",
        "model_classification",
        "model_risk_level",
    }
    return {key: value for key, value in result.items() if key in allowed}


def _domain_details(url: str) -> dict[str, str]:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    return {
        "url": url,
        "scheme": parsed.scheme,
        "hostname": hostname,
        "registrable_domain": registrable_domain(hostname),
    }


def _summary(
    initial: dict[str, object],
    destination: dict[str, object],
    redirect: RedirectResolution,
    authenticity: dict[str, object],
) -> dict[str, object]:
    initial_risk = str(initial.get("risk_level", "review"))
    final_risk = str(destination.get("risk_level", "review"))
    rank = max(RISK_RANK.get(initial_risk, 1), RISK_RANK.get(final_risk, 1))
    reasons: list[str] = []

    if rank == 2:
        reasons.append("La URL escaneada o su destino presenta senales de phishing.")
    if redirect.domain_changed:
        rank = max(rank, 1)
        reasons.append("La redireccion termina en un dominio diferente al del codigo QR.")
    if redirect.status != "resolved":
        rank = max(rank, 1)
        reasons.append("No fue posible confirmar completamente el destino final.")
    if authenticity["status"] == "suspected_impersonation":
        rank = 2
        reasons.append(
            "El dominio parece imitar a "
            f"{authenticity['suspected_official_host']}: {authenticity['reason']}."
        )
    elif destination.get("superfinanciera_host_match"):
        reasons.append(
            "El host de destino coincide con el registro de la Superfinanciera."
        )
    else:
        reasons.append(
            "El host de destino no figura en el registro financiero oficial disponible."
        )

    risk = ("low", "review", "high")[rank]
    if risk == "high":
        title = "Riesgo alto: no abras el enlace"
    elif risk == "review":
        title = "Revisa el destino antes de continuar"
    else:
        title = "No se detectaron senales de alto riesgo"
    return {"risk_level": risk, "title": title, "reasons": reasons}


def analyze_qr_url(
    raw_url: str,
    model_dir: Path,
    *,
    safe_browsing_client: SafeBrowsingClient | None = None,
    check_redirects: bool = True,
    analyzer: Callable[..., dict[str, object]] = analyze,
    redirect_resolver: Callable[..., RedirectResolution] = resolve_redirects,
) -> dict[str, object]:
    """Analiza el valor del QR sin abrirlo en el navegador del usuario."""

    normalized = normalize_url(raw_url)
    if normalized is None:
        raise ValueError("El codigo QR no contiene una URL HTTP(S) valida")

    initial = analyzer(
        normalized,
        model_dir,
        safe_browsing_client=safe_browsing_client,
    )
    if check_redirects:
        redirect = redirect_resolver(normalized)
    else:
        redirect = RedirectResolution(
            status="skipped",
            final_url=normalized,
            chain=(),
            domain_changed=False,
        )

    final_url = redirect.final_url
    if final_url == normalized:
        destination = initial
    else:
        destination = analyzer(
            final_url,
            model_dir,
            safe_browsing_client=safe_browsing_client,
        )

    final_details = _domain_details(final_url)
    authenticity = _authenticity(
        final_details["hostname"], model_dir, destination
    )

    return {
        "scanned": _domain_details(normalized),
        "destination": final_details,
        "redirect": redirect.as_dict(),
        "initial_analysis": _public_analysis(initial),
        "destination_analysis": _public_analysis(destination),
        "authenticity": authenticity,
        "summary": _summary(initial, destination, redirect, authenticity),
    }

