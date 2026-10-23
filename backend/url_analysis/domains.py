"""Utilidades de dominio compartidas por preparacion y rasgos."""

from __future__ import annotations

from functools import lru_cache


try:
    import tldextract

    # Usa la Public Suffix List incluida en el paquete y nunca consulta la red.
    _EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())
except ImportError:
    _EXTRACTOR = None


_COMMON_COMPOUND_SUFFIXES = {
    "ac.uk", "co.jp", "co.uk", "com.ar", "com.au", "com.br", "com.co",
    "com.mx", "com.tr", "edu.co", "gov.uk", "net.au", "org.uk",
}


def strip_www(hostname: str) -> str:
    hostname = hostname.casefold().rstrip(".")
    return hostname[4:] if hostname.startswith("www.") else hostname


@lru_cache(maxsize=200_000)
def domain_parts(hostname: str) -> tuple[str, str, str]:
    """Devuelve (subdominio, dominio, sufijo publico), ignorando `www`."""

    hostname = strip_www(hostname)
    if not hostname or ":" in hostname or hostname.replace(".", "").isdigit():
        return "", hostname, ""
    if _EXTRACTOR is not None:
        result = _EXTRACTOR(hostname)
        return result.subdomain, result.domain, result.suffix

    labels = hostname.split(".")
    if len(labels) == 1:
        return "", labels[0], ""
    suffix_size = 2 if ".".join(labels[-2:]) in _COMMON_COMPOUND_SUFFIXES else 1
    domain_index = len(labels) - suffix_size - 1
    if domain_index < 0:
        return "", labels[0], ".".join(labels[1:])
    return (
        ".".join(labels[:domain_index]),
        labels[domain_index],
        ".".join(labels[domain_index + 1 :]),
    )


def registrable_domain(hostname: str) -> str:
    _, domain, suffix = domain_parts(hostname)
    return f"{domain}.{suffix}" if suffix else domain
