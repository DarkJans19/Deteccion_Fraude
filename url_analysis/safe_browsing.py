"""Cliente pequeno para la consulta en tiempo real de Google Safe Browsing v5."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_ENDPOINT = "https://safebrowsing.googleapis.com/v5/urls:search"
API_KEY_ENV = "GOOGLE_SAFE_BROWSING_API_KEY"


class SafeBrowsingError(RuntimeError):
    """Error controlado al consultar o interpretar la respuesta del servicio."""


@dataclass(frozen=True)
class SafeBrowsingResult:
    is_threat: bool
    threats: tuple[dict[str, object], ...]
    cache_duration: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": "google_safe_browsing",
            "status": "checked",
            "verdict": "known_threat" if self.is_threat else "no_match",
            "is_threat": self.is_threat,
            "threats": list(self.threats),
            "cache_duration": self.cache_duration,
        }


class SafeBrowsingClient:
    """Consulta URLs completas mediante threatMatches de Safe Browsing v4 (Soporte nativo JSON)."""

    def __init__(
        self,
        api_key: str,
        timeout: float = 5.0,
        # Cambiamos al endpoint oficial de la v4
        endpoint: str = "https://safebrowsing.googleapis.com/v4/threatMatches:find",
        opener: Callable[..., object] = urlopen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key.strip():
            raise ValueError("La clave de Google Safe Browsing no puede estar vacia")
        if timeout <= 0:
            raise ValueError("El timeout de Google Safe Browsing debe ser positivo")
        self._api_key = api_key.strip()
        self._timeout = timeout
        self._endpoint = endpoint
        self._opener = opener
        self._clock = clock
        self._cache: dict[str, tuple[float, SafeBrowsingResult]] = {}

    @classmethod
    def from_environment(cls, timeout: float = 5.0) -> SafeBrowsingClient | None:
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        return cls(api_key, timeout=timeout) if api_key else None

    def check(self, url: str) -> SafeBrowsingResult:
        cached = self._cache.get(url)
        now = self._clock()
        if cached is not None and cached[0] > now:
            return cached[1]

        # La v4 requiere un POST con la siguiente estructura
        payload_data = {
            "client": {
                "clientId": "cliente-python-ligero",
                "clientVersion": "1.0.0"
            },
            "threatInfo": {
                "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"],
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": [{"url": url}]
            }
        }
        body_bytes = json.dumps(payload_data).encode("utf-8")

        # La llave de API se envia por parametro de URL en la v4
        request = Request(
            f"{self._endpoint}?key={self._api_key}",
            data=body_bytes,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        
        try:
            response = self._opener(request, timeout=self._timeout)
            with response:  # type: ignore[attr-defined]
                raw_data = response.read().decode("utf-8", errors="replace")
                payload = json.loads(raw_data)  
        except HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            raise SafeBrowsingError(
                f"Google Safe Browsing respondio HTTP {error.code}. Detalles: {error_body}"
            ) from error
        except URLError as error:
            raise SafeBrowsingError(
                f"No fue posible conectar con Google Safe Browsing: {error.reason}"
            ) from error
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SafeBrowsingError(
                "Google Safe Browsing devolvio una respuesta que no pudo procesarse"
            ) from error

        if not isinstance(payload, dict):
            raise SafeBrowsingError("Respuesta inesperada de Google Safe Browsing")

        threats: list[dict[str, object]] = []
        matches = payload.get("matches", [])
        
        for item in matches:
            if not isinstance(item, dict):
                continue
            threat_type = item.get("threatType", "")
            threat_url = item.get("threat", {}).get("url", "")
            threats.append({
                "url": str(threat_url),
                "threat_types": [str(threat_type)] if threat_type else []
            })
            
        cache_duration = matches[0].get("cacheDuration") if matches else None
        
        result = SafeBrowsingResult(
            is_threat=bool(threats),
            threats=tuple(threats),
            cache_duration=str(cache_duration) if cache_duration is not None else None,
        )
        
        try:
            duration = float(str(cache_duration).removesuffix("s"))
        except (TypeError, ValueError):
            # Para URLs seguras, la API no envia duracion de cache. 
            # Aplicamos un tiempo local razonable (ej. 5 minutos) para evitar saturar la cuota
            duration = 300.0 if not result.is_threat else 0.0
            
        if duration > 0:
            maximum = 1_800.0 if result.is_threat else 86_400.0
            self._cache[url] = (now + min(duration, maximum), result)
            
        return result