"""Servidor exclusivo de pruebas de navegador; nunca escribe en la base real."""

import argparse
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core.traceability.hasher import Hasher, ReportServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument(
        "--visual-demo",
        action="store_true",
        help="Añade reportes ficticios para revisar la legibilidad visual del mapa.",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="centinela-e2e-") as directory:
        hasher = Hasher(Path(directory) / "reports.sqlite3")
        examples = [
            ("Promoción de inversión por mensaje", "sms", "fraude", 6.244, -75.581),
            ("Confirmación de un envío", "correo", "no_fraude", 4.619, -74.155),
            ("Notificación de inicio de sesión", "correo", "incierto", 4.650, -74.055),
            ("Enlace a un supuesto sorteo", "sms", "fraude", 4.743, -74.095),
            ("Solicitud de actualización de datos", "otro", None, 4.675, -74.141),
            ("Comprobante de una compra", "correo", "no_fraude", 4.646, -74.095),
            ("Mensaje con remitente desconocido", "sms", None, 4.702, -74.116),
            ("Aviso de una transferencia pendiente", "correo", "incierto", 4.730, -74.035),
            ("SMS con un enlace sospechoso", "sms", "fraude", 4.650, -74.055),
        ]
        for title, source, label, latitude, longitude in examples:
            analysis = None if label is None else {
                "label": label, "score": None,
                "explanation": "Resultado simulado para comprobar la interfaz. No corresponde a un detector real.",
                "model_name": "detector-de-prueba", "model_version": "1.0",
                "model_digest": "a" * 64, "prompt_version": "test-1",
            }
            hasher.create_report(title, "Ejemplo ficticio de evidencia para las pruebas del dashboard.", source,
                                 geolocation={"latitude": latitude, "longitude": longitude, "accuracy_m": 25},
                                 analysis=analysis)
        if args.visual_demo:
            visual_examples = [
                ("Demo · Chapinero 1", 4.648, -74.064),
                ("Demo · Chapinero 2", 4.649, -74.063),
                ("Demo · Chapinero 3", 4.650, -74.062),
                ("Demo · Kennedy 1", 4.629, -74.157),
                ("Demo · Kennedy 2", 4.630, -74.156),
                ("Demo · Engativá", 4.711, -74.111),
                ("Demo · Suba", 4.744, -74.095),
                ("Demo · Usaquén", 4.695, -74.031),
                ("Demo · Bosa", 4.614, -74.193),
                ("Demo · Ciudad Bolívar", 4.570, -74.160),
            ]
            for title, latitude, longitude in visual_examples:
                hasher.create_report(
                    title,
                    "Registro ficticio para revisar el contraste del mapa.",
                    "otro",
                    geolocation={
                        "latitude": latitude,
                        "longitude": longitude,
                        "accuracy_m": 100,
                    },
                )
        server = ReportServer(("127.0.0.1", args.port), hasher)
        print(f"Test server: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
