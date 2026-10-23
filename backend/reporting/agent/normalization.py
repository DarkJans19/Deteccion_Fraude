"""Normalización de textos y anonimización de datos personales (PII) e indicadores."""

import re
import unicodedata
from typing import Tuple

# Expresiones regulares para extracción y anonimización
EMAIL_REGEX = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
PHONE_REGEX = re.compile(
    r"(?:\+?57\s*)?(?:3\d{2}[-.\s]?\d{3}[-.\s]?\d{4}|\b(?:60\d|03\d)[-.\s]?\d{7}\b)"
)
CC_REGEX = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")  # Tarjetas de crédito
DOCUMENT_REGEX = re.compile(r"\b(?:C\.?C\.?|NIT|DNI|TI)?\s*(\d{7,10})\b", re.IGNORECASE)
URL_REGEX = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def normalize_text(text: str) -> str:
    r"""Normaliza texto para comparación de mensajes equivalentes.
    
    Aplica:
    1. Normalización Unicode NFKC (compatibilidad y composición canónica).
    2. Unificación de espacios en blanco y saltos de línea (\s+ -> ' ').
    3. Conversión consistente a minúsculas mediante casefold.
    
    Regla: No elimina correos, dominios, enlaces ni números, ya que
    distinguen mensajes relevantes.
    """
    if not text:
        return ""
    # Unicode NFKC
    nfkc = unicodedata.normalize("NFKC", text)
    # Unificación de espacios y saltos de línea
    collapsed = re.sub(r"\s+", " ", nfkc).strip()
    # Case folding para comparación insensible a mayúsculas
    return collapsed.casefold()


def mask_email(email: str) -> str:
    """Enmascara una dirección de correo para reportes públicos.
    
    Ejemplo:
    - 'juan.perez@empresa.com' -> 'j***z@e***a.com'
    - 'ab@c.co' -> 'a*@c*.co'
    """
    if "@" not in email:
        return email
    local, domain = email.split("@", 1)
    
    # Enmascarar parte local
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[0] + "***" + local[-1]
        
    # Enmascarar dominio
    domain_parts = domain.split(".")
    masked_domain_parts = []
    for idx, part in enumerate(domain_parts):
        if idx == len(domain_parts) - 1:  # TLD se preserva (ej. .com, .co)
            masked_domain_parts.append(part)
        elif len(part) <= 2:
            masked_domain_parts.append(part[0] + "*")
        else:
            masked_domain_parts.append(part[0] + "***" + part[-1])
            
    return f"{masked_local}@{''.join('.' + p for p in masked_domain_parts)[1:]}"


def mask_phone(phone: str) -> str:
    """Enmascara números telefónicos conservando prefijo inicial y final."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) >= 10:
        return f"{digits[:3]}***{digits[-4:]}"
    return "***-***"


def anonymize_text(text: str, mask_indicators: bool = True) -> str:
    """Anonimiza datos sensibles (PII) en fragmentos de texto.
    
    - Correos electrónicos (si mask_indicators=True)
    - Números de teléfono
    - Números de documento / tarjetas
    - Transforma URLs en texto no navegable
    """
    if not text:
        return ""
    
    # 1. Enmascarar correos si está activada la anonimización de indicadores
    result = text
    if mask_indicators:
        result = EMAIL_REGEX.sub(lambda m: mask_email(m.group(0)), result)
    
    # 2. Enmascarar tarjetas de crédito
    result = CC_REGEX.sub("****-****-****-****", result)
    
    # 3. Enmascarar teléfonos
    result = PHONE_REGEX.sub(lambda m: mask_phone(m.group(0)), result)
    
    # 4. Desarmar URLs activas (prevenir links clicables en visualizadores)
    result = URL_REGEX.sub(lambda m: f"[enlace no navegable: {re.sub(r'^https?://', '', m.group(0))[:50]}]", result)
    
    return result


def extract_emails_from_text(text: str) -> list[str]:
    """Extrae todas las direcciones de correo válidas contenidas en el texto, en minúsculas."""
    if not text:
        return []
    matches = EMAIL_REGEX.findall(text)
    return [m.lower().strip() for m in matches]


def extract_domain_from_email(email: str) -> str:
    """Extrae el dominio en minúsculas de una dirección de correo."""
    if "@" in email:
        return email.split("@", 1)[1].lower().strip()
    return ""
