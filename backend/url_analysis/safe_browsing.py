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
    """Consulta URLs completas mediante ``urls.search`` de Safe Browsing v5."""

    def __init__(
        self,
        api_key: str,
        timeout: float = 5.0,
        endpoint: str = DEFAULT_ENDPOINT,
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

        query = urlencode({"urls": url})
        request = Request(
            f"{self._endpoint}?{query}",
            headers={
                "Accept": "application/json",
                "X-goog-api-key": self._api_key,
            },
            method="GET",
        )

        try:
            response = self._opener(request, timeout=self._timeout)
            with response:  # type: ignore[attr-defined]
                raw_data = response.read().decode("utf-8", errors="replace")
                payload = json.loads(raw_data)
        except HTTPError as error:
            raise SafeBrowsingError(
                f"Google Safe Browsing respondio HTTP {error.code}"
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

        raw_threats = payload.get("threats", [])
        if not isinstance(raw_threats, list):
            raise SafeBrowsingError("Respuesta inesperada de Google Safe Browsing")

        threats: list[dict[str, object]] = []
        for item in raw_threats:
            if not isinstance(item, dict):
                continue
            threat_types = item.get("threatTypes", [])
            threats.append(
                {
                    "url": str(item.get("url", "")),
                    "threat_types": [
                        str(threat_type)
                        for threat_type in threat_types
                        if isinstance(threat_type, str)
                    ]
                    if isinstance(threat_types, list)
                    else [],
                }
            )

        cache_duration = payload.get("cacheDuration")
        result = SafeBrowsingResult(
            is_threat=bool(threats),
            threats=tuple(threats),
            cache_duration=str(cache_duration) if cache_duration is not None else None,
        )

        try:
            duration = float(str(cache_duration).removesuffix("s"))
        except (TypeError, ValueError):
            duration = 0.0

        if duration > 0:
            maximum = 1_800.0 if result.is_threat else 86_400.0
            self._cache[url] = (now + min(duration, maximum), result)

        return result
