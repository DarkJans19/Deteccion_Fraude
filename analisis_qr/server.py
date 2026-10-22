"""Servidor web local del lector y analizador de codigos QR."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analisis_qr.service import analyze_qr_url
from url_analysis.safe_browsing import SafeBrowsingClient


STATIC_DIR = Path(__file__).with_name("static")
MAX_REQUEST_BYTES = 8_192
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/vendor/jsQR.js": ("vendor/jsQR.js", "text/javascript; charset=utf-8"),
}


class QRAnalysisServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        model_dir: Path,
        safe_browsing_client: SafeBrowsingClient | None,
        check_redirects: bool,
    ) -> None:
        super().__init__(server_address, QRRequestHandler)
        self.model_dir = model_dir
        self.safe_browsing_client = safe_browsing_client
        self.check_redirects = check_redirects
        self.analysis_lock = threading.Lock()


class QRRequestHandler(BaseHTTPRequestHandler):
    server: QRAnalysisServer

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(self)")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' blob: data:; media-src 'self' blob:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self._headers(status, content_type, len(body))
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "model_available": self.server.model_dir.is_dir(),
                    "safe_browsing_configured": self.server.safe_browsing_client is not None,
                    "redirect_checks_enabled": self.server.check_redirects,
                },
            )
            return
        static = STATIC_FILES.get(path)
        if static is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Ruta no encontrada"})
            return
        filename, content_type = static
        try:
            body = (STATIC_DIR / filename).read_bytes()
        except OSError:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "No fue posible cargar la interfaz"},
            )
            return
        self._send_bytes(HTTPStatus.OK, body, content_type)

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/api/analyze":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Ruta no encontrada"})
            return
        if self.headers.get_content_type() != "application/json":
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "Se requiere Content-Type application/json"},
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "Solicitud vacia o demasiado grande"},
            )
            return

        try:
            payload = json.loads(self.rfile.read(length))
            url = payload.get("url") if isinstance(payload, dict) else None
            if not isinstance(url, str):
                raise ValueError("El campo url es obligatorio")
            # TensorFlow y sus modelos se comparten de forma serializada para
            # evitar picos de memoria si llegan dos solicitudes simultaneas.
            with self.server.analysis_lock:
                result = analyze_qr_url(
                    url,
                    self.server.model_dir,
                    safe_browsing_client=self.server.safe_browsing_client,
                    check_redirects=self.server.check_redirects,
                )
        except (json.JSONDecodeError, ValueError) as error:
            self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
            return
        except (OSError, RuntimeError) as error:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"No fue posible completar el analisis: {error}"},
            )
            return
        self._send_json(HTTPStatus.OK, result)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--model-dir", type=Path, default=ROOT / "artifacts" / "url_autoencoder"
    )
    parser.add_argument(
        "--no-redirects",
        action="store_true",
        help="no realiza solicitudes HTTP para comprobar el destino final",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not args.model_dir.is_dir():
        parser.error(f"No existe el directorio del modelo: {args.model_dir}")
    client = SafeBrowsingClient.from_environment(timeout=5.0)
    server = QRAnalysisServer(
        (args.host, args.port),
        model_dir=args.model_dir,
        safe_browsing_client=client,
        check_redirects=not args.no_redirects,
    )
    address = f"http://{args.host}:{server.server_port}"
    print(f"Lector QR disponible en {address}")
    print("Presiona Ctrl+C para detenerlo.")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(address,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

