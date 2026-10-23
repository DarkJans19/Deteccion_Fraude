"""Cliente pequeno para la consulta en tiempo real de Google Safe Browsing v4."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_ENDPOINT = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
API_KEY_ENV = "GOOGLE_SAFE_BROWSING_API_KEY"
CLIENT_ID = "deteccion-fraude-url-analysis"
CLIENT_VERSION = "1.0"
THREAT_TYPES = (
    "MALWARE",
    "SOCIAL_ENGINEERING",
    "UNWANTED_SOFTWARE",
    "POTENTIALLY_HARMFUL_APPLICATION",
)
PLATFORM_TYPES = ("ANY_PLATFORM",)
THREAT_ENTRY_TYPES = ("URL",)


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
    """Consulta URLs completas mediante ``threatMatches.find`` de Safe Browsing v4."""

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

        body = json.dumps(
            {
                "client": {
                    "clientId": CLIENT_ID,
                    "clientVersion": CLIENT_VERSION,
                },
                "threatInfo": {
                    "threatTypes": list(THREAT_TYPES),
                    "platformTypes": list(PLATFORM_TYPES),
                    "threatEntryTypes": list(THREAT_ENTRY_TYPES),
                    "threatEntries": [{"url": url}],
                },
            }
        ).encode("utf-8")
        request = Request(
            self._endpoint,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-goog-api-key": self._api_key,
            },
            method="POST",
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

        raw_matches = payload.get("matches", [])
        if not isinstance(raw_matches, list):
            raise SafeBrowsingError("Respuesta inesperada de Google Safe Browsing")

        threats_by_url: dict[str, list[str]] = {}
        durations: list[float] = []
        for item in raw_matches:
            if not isinstance(item, dict):
                continue
            threat = item.get("threat")
            threat_url = str(threat.get("url", "")) if isinstance(threat, dict) else ""
            threat_types = threats_by_url.setdefault(threat_url, [])
            threat_type = item.get("threatType")
            if isinstance(threat_type, str) and threat_type not in threat_types:
                threat_types.append(threat_type)
            duration = _duration_seconds(item.get("cacheDuration"))
            if duration > 0:
                durations.append(duration)

        threats = tuple(
            {"url": threat_url, "threat_types": threat_types}
            for threat_url, threat_types in threats_by_url.items()
        )
        raw_cache_duration = payload.get("cacheDuration")
        if raw_cache_duration is None and durations:
            duration = min(durations)
            raw_cache_duration = f"{duration:g}s"
        cache_duration = (
            str(raw_cache_duration) if raw_cache_duration is not None else None
        )
        result = SafeBrowsingResult(
            is_threat=bool(threats),
            threats=threats,
            cache_duration=cache_duration,
        )

        duration = _duration_seconds(cache_duration)
        if duration > 0:
            maximum = 1_800.0 if result.is_threat else 86_400.0
            self._cache[url] = (now + min(duration, maximum), result)

        return result


def _duration_seconds(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(str(value).removesuffix("s"))
    except (TypeError, ValueError):
        return 0.0
