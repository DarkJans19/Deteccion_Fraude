"""Adaptador HTTP opcional: conecta una vista y un detector externo con Ledger."""

import argparse
import json
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from .ledger import IntegrityError, Ledger
else:
    from ledger import IntegrityError, Ledger


STATIC = Path(__file__).parent / "static"


class ReportServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, ledger, detector=None):
        super().__init__(address, Handler)
        self.ledger = ledger
        self.detector = detector
        self.csrf = secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format, *args):
        pass

    def respond(self, status, body, content_type="application/json; charset=utf-8", download=None):
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode() if isinstance(body, (dict, list)) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(data)

    def allowed_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, write):
        if not self.allowed_host():
            return self.respond(403, {"error": "Host no permitido"})
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self.respond(403, {"error": "Acceso externo no permitido"})
        try:
            if write:
                origin = self.headers.get("Origin")
                if origin and origin != "http://" + self.headers["Host"]:
                    return self.respond(403, {"error": "Origen no permitido"})
                if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), self.server.csrf):
                    return self.respond(403, {"error": "Token local inválido; recarga el dashboard"})
                if self.headers.get_content_type() != "application/json":
                    return self.respond(415, {"error": "Se requiere application/json"})
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 300000:
                    return self.respond(413, {"error": "Tamaño del envío inválido"})
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Se requiere un objeto JSON")
                return self.post(urlsplit(self.path).path, data)
            return self.get(urlsplit(self.path).path)
        except IntegrityError as error:
            self.respond(409, {"error": str(error), "integrity": False})
        except KeyError:
            self.respond(404, {"error": "Reporte no encontrado"})
        except (ValueError, TypeError, UnicodeError) as error:
            self.respond(400, {"error": str(error)})
        except sqlite3.Error:
            self.respond(503, {"error": "No se pudo acceder a los reportes locales"})

    def get(self, path):
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if path in assets:
            filename, mime = assets[path]
            return self.respond(200, (STATIC / filename).read_bytes(), mime)
        if path == "/api/session":
            return self.respond(200, {"csrf": self.server.csrf, "model_available": self.server.detector is not None})
        if path == "/api/reports":
            return self.respond(200, self.server.ledger.snapshot())
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "reports"]:
            return self.respond(200, self.server.ledger.get_report(parts[2]))
        if len(parts) == 4 and parts[:2] == ["api", "reports"] and parts[3] == "export":
            result = self.server.ledger.export(parts[2])
            return self.respond(200, result, download=f"reporte-{result['report']['id']}.json")
        return self.respond(404, {"error": "Ruta no encontrada"})

    def post(self, path, data):
        ledger = self.server.ledger
        if path == "/api/reports":
            if set(data) - {"title", "text", "source", "source_ref", "reported_by"}:
                raise ValueError("Campos desconocidos")
            content = ledger.validate_content(data.get("title"), data.get("text"), data.get("source"),
                                              data.get("source_ref", ""), data.get("reported_by"))
            analysis = None
            if self.server.detector is not None:
                try:
                    analysis = self.server.detector.analyze(content["text"])
                except Exception:
                    return self.respond(503, {"error": "El modelo no pudo completar el análisis. No se guardó el reporte"})
                if analysis is None:
                    return self.respond(503, {"error": "El modelo no devolvió un resultado. No se guardó el reporte"})
                try:
                    analysis = ledger.validate_analysis(analysis)
                except (ValueError, TypeError):
                    return self.respond(502, {"error": "El modelo devolvió un resultado inválido. No se guardó el reporte"})
            report = ledger.create_report(**content, analysis=analysis)
            return self.respond(201, {"id": report["id"], "hash": report["hash"]})
        return self.respond(404, {"error": "Ruta no encontrada"})


def main():
    parser = argparse.ArgumentParser(description="Dashboard local de reportes con un único registro SHA-256")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "runtime" / "reports.sqlite3")
    args = parser.parse_args()
    try:
        ledger = Ledger(args.db)
    except (ValueError, sqlite3.Error, OSError) as error:
        parser.exit(1, f"Error: {error}\n")
    server = ReportServer(("127.0.0.1", args.port), ledger)
    print(f"Dashboard: http://127.0.0.1:{server.server_port}", flush=True)
    print("LLM sin conectar: los reportes se guardan sin análisis. Ctrl+C para detener.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
