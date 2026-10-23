"""Reportes anónimos, hash SHA-256, persistencia y API HTTP local.

La ubicación declarada por el navegador forma parte del mismo registro inmutable.
No se almacenan identidades, direcciones IP ni agentes de usuario.
"""

import argparse
import hashlib
import json
import math
import mimetypes
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4


class IntegrityError(Exception):
    pass


class StorageError(Exception):
    """Error de persistencia cuyo mensaje no contiene credenciales."""


class ConflictError(Exception):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def required_text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name}: se requiere texto de 1 a {limit} caracteres")
    return value


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "frontend" / "dashboard" / "dist"
SCHEMA_VERSION = 3


class Hasher:
    """Almacena reportes independientes; no genera eventos ni permite modificarlos."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
                raise ValueError("Esta base usa el formato anterior de eventos. Consérvala y elige otra ruta con --db.")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reports'").fetchone():
                for (raw,) in db.execute("SELECT report_json FROM reports"):
                    try:
                        report = json.loads(raw)
                        compatible = (isinstance(report, dict)
                                      and report.get("schema_version") == SCHEMA_VERSION
                                      and "reported_by" not in report)
                    except (ValueError, TypeError):
                        compatible = False
                    if not compatible:
                        raise ValueError("La base contiene reportes del formato anterior o incompatibles. "
                                         "Se conserva intacta; usa otra ruta con --db para los reportes anónimos.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY NOT NULL,
                    report_json TEXT NOT NULL,
                    hash TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS no_report_update BEFORE UPDATE ON reports
                BEGIN SELECT RAISE(ABORT, 'El reporte no admite modificaciones'); END;
                CREATE TRIGGER IF NOT EXISTS no_report_delete BEFORE DELETE ON reports
                BEGIN SELECT RAISE(ABORT, 'El reporte no admite eliminaciones'); END;
                CREATE TRIGGER IF NOT EXISTS no_report_replace BEFORE INSERT ON reports
                WHEN EXISTS (SELECT 1 FROM reports WHERE id = NEW.id)
                BEGIN SELECT RAISE(ABORT, 'El reporte ya está registrado'); END;
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def validate_content(title, text, source, source_ref, geolocation):
        content = {
            "title": required_text(title, "título", 160),
            "text": required_text(text, "contenido", 50000),
            "source": source, "source_ref": source_ref,
            "geolocation": Hasher.validate_geolocation(geolocation),
        }
        if source not in ("sms", "correo", "otro"):
            raise ValueError("Origen inválido")
        if not isinstance(source_ref, str) or len(source_ref) > 500:
            raise ValueError("La referencia admite hasta 500 caracteres")
        return content

    @staticmethod
    def validate_geolocation(geolocation):
        required = {"latitude", "longitude", "accuracy_m"}
        if not isinstance(geolocation, dict) or set(geolocation) != required:
            raise ValueError("Se requiere geolocation con latitude, longitude y accuracy_m")
        for name, lower, upper in (("latitude", -90, 90), ("longitude", -180, 180),
                                   ("accuracy_m", 0, 40075000)):
            value = geolocation[name]
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"{name}: se requiere un número finito entre {lower} y {upper}")
        # Reducir precisión antes de calcular el hash y antes de persistir.
        return {"latitude": round(geolocation["latitude"], 3),
                "longitude": round(geolocation["longitude"], 3),
                "accuracy_m": math.ceil(geolocation["accuracy_m"])}

    @staticmethod
    def validate_analysis(analysis):
        if analysis is None:
            return None
        required = {"label", "score", "explanation", "model_name", "model_version",
                    "model_digest", "prompt_version"}
        if not isinstance(analysis, dict) or set(analysis) != required:
            raise ValueError(f"Campos requeridos del análisis: {sorted(required)}")
        if analysis["label"] not in ("fraude", "no_fraude", "incierto"):
            raise ValueError("Clasificación inválida")
        score = analysis["score"]
        if score is not None and (type(score) not in (int, float) or not math.isfinite(score)
                                  or not 0 <= score <= 1):
            raise ValueError("score debe ser null o un número entre 0 y 1")
        for name in ("explanation", "model_name", "model_version", "prompt_version"):
            required_text(analysis[name], name, 10000 if name == "explanation" else 200)
        value = analysis["model_digest"]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("model_digest debe ser SHA-256 hexadecimal en minúsculas")
        return dict(analysis)

    def create_report(self, title, text, source, source_ref="", *, geolocation, analysis=None):
        content = self.validate_content(title, text, source, source_ref, geolocation)
        analysis = self.validate_analysis(analysis)
        report = {
            "schema_version": SCHEMA_VERSION, "id": str(uuid4()), **content,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "analysis": analysis,
            "fraud_positive": {"fraude": True, "no_fraude": False, "incierto": None}[analysis["label"]] if analysis else None,
        }
        report_hash = digest(report)
        with self.connection() as db:
            db.execute("INSERT INTO reports (id, report_json, hash) VALUES (?, ?, ?)",
                       (report["id"], canonical(report), report_hash))
        return {**report, "hash": report_hash}

    @staticmethod
    def _verified(row):
        report_id, raw, saved_hash = row
        try:
            report = json.loads(raw)
            fields = {"schema_version", "id", "title", "text", "source", "source_ref",
                      "geolocation", "created_at", "analysis", "fraud_positive"}
            if (not isinstance(report, dict) or set(report) != fields
                    or report["id"] != report_id or report["schema_version"] != SCHEMA_VERSION
                    or digest(report) != saved_hash):
                raise IntegrityError(f"El reporte {report_id} fue alterado")
            Hasher.validate_content(report["title"], report["text"], report["source"],
                                    report["source_ref"], report["geolocation"])
            analysis = Hasher.validate_analysis(report["analysis"])
            expected = {"fraude": True, "no_fraude": False, "incierto": None}[analysis["label"]] if analysis else None
            if report["fraud_positive"] is not expected:
                raise IntegrityError(f"El reporte {report_id} tiene un resultado inconsistente")
        except (ValueError, TypeError, KeyError) as error:
            raise IntegrityError(f"El reporte {report_id} está dañado") from error
        return {**report, "hash": saved_hash}

    def snapshot(self):
        with self.connection() as db:
            rows = db.execute("SELECT id, report_json, hash FROM reports ORDER BY rowid DESC").fetchall()
        return {"reports": [self._verified(row) for row in rows]}

    def get_report(self, report_id):
        with self.connection() as db:
            row = db.execute("SELECT id, report_json, hash FROM reports WHERE id=?", (report_id,)).fetchone()
        if row is None:
            raise KeyError(report_id)
        return self._verified(row)

    def export(self, report_id):
        return {"format": "fraud-report-v3", "report": self.get_report(report_id)}


class ReportServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, hasher, detector=None, *, static_dir=STATIC,
                 frontend_origins=("http://127.0.0.1:5173", "http://localhost:5173"),
                 allowed_hosts=()):
        super().__init__(address, Handler)
        self.hasher = hasher
        self.detector = detector
        self.static_dir = Path(static_dir).resolve()
        self.frontend_origins = frozenset(frontend_origins)
        self.allowed_hosts = frozenset(allowed_hosts)
        self.csrf = secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format, *args):
        # BaseHTTPRequestHandler registra la IP por defecto: mantenerlo desactivado.
        pass

    def respond(self, status, body, content_type="application/json; charset=utf-8", download=None):
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8") if isinstance(body, (dict, list)) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "geolocation=(self)")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; "
                         "base-uri 'none'; form-action 'self'")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(data)

    def allowed_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}",
                                            *self.server.allowed_hosts}

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, write):
        raw_body = None
        if write:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if not 0 < length <= 300000:
                return self.respond(413, {"error": "Tamaño del envío inválido"})
            # Consumir una sola vez el cuerpo acotado evita que Windows reinicie
            # la conexión cuando la solicitud se rechaza por host, origen o CSRF.
            # El JSON solo se interpreta después de superar esos controles.
            raw_body = self.rfile.read(length)
        if not self.allowed_host():
            return self.respond(403, {"error": "Host no permitido"})
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self.respond(403, {"error": "Acceso externo no permitido"})
        try:
            if write:
                origin = self.headers.get("Origin")
                allowed = {"http://" + self.headers["Host"], *self.server.frontend_origins}
                if origin and origin not in allowed:
                    return self.respond(403, {"error": "Origen no permitido"})
                if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), self.server.csrf):
                    return self.respond(403, {"error": "Token local inválido; recarga el dashboard"})
                if self.headers.get_content_type() != "application/json":
                    return self.respond(415, {"error": "Se requiere application/json"})
                data = json.loads(raw_body)
                if not isinstance(data, dict):
                    raise ValueError("Se requiere un objeto JSON")
                return self.post(urlsplit(self.path).path, data)
            return self.get(urlsplit(self.path).path)
        except IntegrityError as error:
            self.respond(409, {"error": str(error), "integrity": False})
        except ConflictError as error:
            self.respond(409, {"error": str(error)})
        except StorageError:
            self.respond(503, {"error": "No se pudo acceder a MongoDB; vuelve a intentar"})
        except KeyError:
            self.respond(404, {"error": "Reporte no encontrado"})
        except (ValueError, TypeError, UnicodeError) as error:
            self.respond(400, {"error": str(error)})
        except sqlite3.Error:
            self.respond(503, {"error": "No se pudo acceder a los reportes locales"})
        except OSError:
            self.respond(503, {"error": "No se pudo leer el recurso local"})

    def get(self, path):
        if path == "/api/health":
            if hasattr(self.server.hasher, "health"):
                self.server.hasher.health()
            return self.respond(200, {"status": "ok"})
        if path == "/api/session":
            return self.respond(200, {"csrf": self.server.csrf, "model_available": self.server.detector is not None})
        if path == "/api/reports":
            return self.respond(200, self.server.hasher.snapshot())
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "reports"]:
            return self.respond(200, self.server.hasher.get_report(parts[2]))
        if len(parts) == 4 and parts[:2] == ["api", "reports"] and parts[3] == "export":
            result = self.server.hasher.export(parts[2])
            return self.respond(200, result, download=f"reporte-{parts[2]}.json")
        if (len(parts) == 4 and parts[:2] == ["api", "reports"] and parts[3] == "tracking"
                and hasattr(self.server.hasher, "get_tracking")):
            return self.respond(200, self.server.hasher.get_tracking(parts[2]))
        if path in ("/", "/dashboard", "/dashboard/", "/dashboard/trazabilidad"):
            index = self.server.static_dir / "index.html"
            if not index.is_file():
                return self.respond(503, {"error": "Compila el dashboard: cd frontend/dashboard && npm install && npm run build. "
                                                  "Para desarrollo, usa npm run dev en http://127.0.0.1:5173."})
            return self.respond(200, index.read_bytes(), "text/html; charset=utf-8")
        if path.startswith(("/assets/", "/geo/")) or path in ("/favicon.svg", "/antifraudi-inador-logo.png"):
            asset = (self.server.static_dir / unquote(path).lstrip("/")).resolve()
            if asset.is_relative_to(self.server.static_dir) and asset.is_file():
                mime = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
                        ".geojson": "application/geo+json", ".json": "application/json"}.get(asset.suffix)
                return self.respond(200, asset.read_bytes(), mime or mimetypes.guess_type(asset.name)[0] or "application/octet-stream")
        return self.respond(404, {"error": "Ruta no encontrada"})

    def post(self, path, data):
        hasher = self.server.hasher
        parts = path.strip("/").split("/")
        if (len(parts) == 4 and parts[:2] == ["api", "reports"] and parts[3] == "tracking"
                and hasattr(hasher, "add_tracking")):
            if set(data) != {"status", "note"}:
                raise ValueError("El seguimiento requiere únicamente status y note")
            return self.respond(201, hasher.add_tracking(parts[2], data["status"], data["note"]))
        if path == "/api/reports":
            if hasattr(hasher, "create_document"):
                return self.respond(201, hasher.create_document(data))
            if set(data) - {"title", "text", "source", "source_ref", "geolocation"}:
                raise ValueError("Campos desconocidos: el reporte es anónimo y no admite datos de usuario")
            content = hasher.validate_content(data.get("title"), data.get("text"), data.get("source"),
                                              data.get("source_ref", ""), data.get("geolocation"))
            analysis = None
            if self.server.detector is not None:
                try:
                    analysis = self.server.detector.analyze(content["text"])
                except Exception:
                    return self.respond(503, {"error": "El modelo no pudo completar el análisis. No se guardó el reporte"})
                if analysis is None:
                    return self.respond(503, {"error": "El modelo no devolvió un resultado. No se guardó el reporte"})
                try:
                    analysis = hasher.validate_analysis(analysis)
                except (ValueError, TypeError):
                    return self.respond(502, {"error": "El modelo devolvió un resultado inválido. No se guardó el reporte"})
            report = hasher.create_report(**content, analysis=analysis)
            return self.respond(201, {"id": report["id"], "hash": report["hash"]})
        return self.respond(404, {"error": "Ruta no encontrada"})


def main():
    parser = argparse.ArgumentParser(description="Trazabilidad anónima con geolocalización y SHA-256")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=ROOT / "backend" / "runtime" / "anonymous-reports.sqlite3")
    args = parser.parse_args()
    try:
        hasher = Hasher(args.db)
        server = ReportServer(("127.0.0.1", args.port), hasher)
    except (ValueError, sqlite3.Error, OSError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"Dashboard / API: http://127.0.0.1:{server.server_port}", flush=True)
    print("LLM sin conectar: los reportes se guardan sin análisis. Ctrl+C para detener.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
