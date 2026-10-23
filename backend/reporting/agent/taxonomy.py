"""Taxonomía analítica versionada y motor de clasificación heurística de fraudes."""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

TAXONOMY_VERSION = "1.0.0"

# Catálogo explícito de categorías
CATEGORIES = [
    "Suplantación de entidad",
    "Solicitud de credenciales",
    "Solicitud de datos personales o financieros",
    "Premios o beneficios engañosos",
    "Solicitudes de pago",
    "Otros",
    "No clasificable",
]

# Patrones y familias léxicas asociadas a cada categoría
CATEGORY_RULES: Dict[str, List[re.Pattern]] = {
    "Suplantación de entidad": [
        re.compile(
            r"\b(banco|bancolombia|davivienda|bbva|banco de bogot[aá]|banco agrario|nequi|daviplata|"
            r"dian|polic[ií]a|fiscal[ií]a|claro|tigo|movistar|superintendencia|notar[ií]a)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(entidad bancaria|departamento de seguridad|portal oficial)\b", re.IGNORECASE),
    ],
    "Solicitud de credenciales": [
        re.compile(
            r"\b(clave|contrase[ñn]a|token|pin|c[oó]digo de seguridad|c[oó]digo de verificaci[oó]n|"
            r"ingrese sus credenciales|valide sus datos de ingreso|actualice su clave)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(su cuenta est[aá] bloqueada|cuenta suspendida|acceso restringido)\b", re.IGNORECASE),
    ],
    "Solicitud de datos personales o financieros": [
        re.compile(
            r"\b(n[uú]mero de tarjeta|cvv|fecha de vencimiento|c[eé]dula|identificaci[oó]n|datos personales|"
            r"informaci[oó]n financiera|extracto|n[uú]mero de cuenta)\b",
            re.IGNORECASE,
        ),
    ],
    "Premios o beneficios engañosos": [
        re.compile(
            r"\b(ha ganado|felicitaciones|premio|sorteo|loter[ií]a|subsidio|bono solidario|"
            r"cr[eé]dito preaprobado|desembolso inmediato|ganador seleccionado)\b",
            re.IGNORECASE,
        ),
    ],
    "Solicitudes de pago": [
        re.compile(
            r"\b(pago inmediato|transferencia|env[ií]e dinero|multa pendiente|embargo judicial|"
            r"paquete retenido|costo de aduana|pague hoy|deuda pendiente)\b",
            re.IGNORECASE,
        ),
    ],
}


@dataclass
class CategoryMatch:
    category: str
    evidence: List[str] = field(default_factory=list)


def classify_text(text: str) -> List[CategoryMatch]:
    """Clasifica un texto determinísticamente mediante reglas y patrones léxicos.
    
    Permite múltiples categorías si se detectan evidencias concurrentes.
    Si ninguna regla de categorías 1-5 se activa:
    - Si el texto tiene longitud mínima pero indicios genéricos (ej. enlaces sospechosos o palabras de alerta), se clasifica como "Otros".
    - Si el texto es muy breve o carece de indicios, se clasifica como "No clasificable".
    """
    if not text or len(text.strip()) < 10:
        return [CategoryMatch(category="No clasificable", evidence=["Texto vacío o inferior a 10 caracteres"])]

    matches: List[CategoryMatch] = []

    for cat_name, patterns in CATEGORY_RULES.items():
        matched_terms: Set[str] = set()
        for pat in patterns:
            for find in pat.finditer(text):
                matched_terms.add(find.group(0).lower().strip())
        if matched_terms:
            matches.append(CategoryMatch(category=cat_name, evidence=sorted(matched_terms)))

    if not matches:
        # Evaluar si contiene enlaces o palabras de alerta para catalogar como "Otros"
        generic_alert = re.search(r"(https?://|urgente|alerta|atenci[oó]n|importante|bloqueo)", text, re.IGNORECASE)
        if generic_alert:
            matches.append(
                CategoryMatch(category="Otros", evidence=[f"Indicio genérico: '{generic_alert.group(0)}'"])
            )
        else:
            matches.append(
                CategoryMatch(category="No clasificable", evidence=["Sin coincidencias léxicas en la taxonomía"])
            )

    return matches
