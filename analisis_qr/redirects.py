"""Resolucion limitada y defensiva de redirecciones HTTP(S)."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, ProxyHandler, Request, build_opener

from url_analysis.domains import registrable_domain
from url_analysis.preprocessing import normalize_url


REDIRECT_CODES = {301, 302, 303, 307, 308}


class RedirectResolutionError(RuntimeError):
    """Indica que no fue posible comprobar el destino sin asumir que es seguro."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass(frozen=True)
class RedirectResolution:
    status: str
    final_url: str
    chain: tuple[dict[str, object], ...]
    domain_changed: bool
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "status": self.status,
            "final_url": self.final_url,
            "chain": list(self.chain),
            "redirect_count": max(0, len(self.chain) - 1),
            "domain_changed": self.domain_changed,
        }
        if self.error:
            result["error"] = self.error
        return result


def _public_destination(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.username is not None or parsed.password is not None:
        raise RedirectResolutionError("No se siguen URLs que contienen credenciales")
    if parsed.port not in {None, 80, 443}:
        raise RedirectResolutionError("Solo se permiten los puertos web 80 y 443")

    hostname = parsed.hostname or ""
    try:
        addresses = {ipaddress.ip_address(hostname)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(hostname, parsed.port or 443)
            }
        except (OSError, ValueError) as error:
            raise RedirectResolutionError(
                "No fue posible resolver el dominio del destino"
            ) from error
    if not addresses or any(not address.is_global for address in addresses):
        raise RedirectResolutionError(
            "Se bloqueo un destino local, privado o no enrutable"
        )


def _open_without_redirects() -> OpenerDirector:
    # No se usan proxies del entorno: una URL no debe poder alcanzar recursos
    # internos indirectamente a traves de la configuracion de la maquina.
    return build_opener(ProxyHandler({}), _NoRedirect())


def _request_once(opener: OpenerDirector, url: str, timeout: float):
    headers = {
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        "User-Agent": "QR-URL-Analyzer/1.0",
    }
    request = Request(url, headers=headers, method="HEAD")
    try:
        return opener.open(request, timeout=timeout)
    except HTTPError as error:
        if error.code in REDIRECT_CODES:
            return error
        if error.code not in {405, 501}:
            raise

    # Algunos sitios no implementan HEAD. Range evita descargar la pagina
    # completa cuando el servidor lo admite.
    headers["Range"] = "bytes=0-0"
    request = Request(url, headers=headers, method="GET")
    try:
        return opener.open(request, timeout=timeout)
    except HTTPError as error:
        if error.code in REDIRECT_CODES:
            return error
        raise


def resolve_redirects(
    raw_url: str,
    *,
    timeout: float = 4.0,
    max_redirects: int = 5,
    opener: OpenerDirector | None = None,
    destination_validator: Callable[[str], None] = _public_destination,
) -> RedirectResolution:
    """Sigue solo cabeceras Location y nunca interpreta ni ejecuta la pagina."""

    normalized = normalize_url(raw_url)
    if normalized is None:
        raise ValueError("La URL no es HTTP(S) valida")
    if timeout <= 0 or not 0 <= max_redirects <= 10:
        raise ValueError("Configuracion de redirecciones invalida")

    first_domain = registrable_domain(urlsplit(normalized).hostname or "")
    current = normalized
    chain: list[dict[str, object]] = []
    client = opener or _open_without_redirects()

    try:
        for attempt in range(max_redirects + 1):
            destination_validator(current)
            network_url = urlunsplit(urlsplit(current)._replace(fragment=""))
            response = _request_once(client, network_url, timeout)
            try:
                status_code = int(response.getcode())
                step: dict[str, object] = {"url": current, "status_code": status_code}
                chain.append(step)
                location = response.headers.get("Location")
            finally:
                response.close()

            if status_code not in REDIRECT_CODES or not location:
                final_domain = registrable_domain(urlsplit(current).hostname or "")
                return RedirectResolution(
                    status="resolved",
                    final_url=current,
                    chain=tuple(chain),
                    domain_changed=first_domain != final_domain,
                )
            if attempt == max_redirects:
                raise RedirectResolutionError("Se excedio el limite de redirecciones")

            candidate = normalize_url(urljoin(current, location))
            if candidate is None:
                raise RedirectResolutionError("La redireccion apunta a una URL no valida")
            step["location"] = candidate
            current = candidate
    except (HTTPError, URLError, TimeoutError, OSError, RedirectResolutionError) as error:
        final_domain = registrable_domain(urlsplit(current).hostname or "")
        return RedirectResolution(
            status="unavailable",
            final_url=current,
            chain=tuple(chain),
            domain_changed=first_domain != final_domain,
            error=str(error),
        )

    raise AssertionError("Bucle de redireccion inesperado")

