"""Servidor MongoDB del despliegue Docker: python -m backend.app."""

import argparse
import os
import signal
import threading
from pathlib import Path

from dotenv import load_dotenv
from pymongo.errors import PyMongoError

from backend.core.traceability.hasher import IntegrityError, ReportServer, StorageError
from backend.core.traceability.mongo import MongoReports, client_from_environment, environment_config


def main():
    parser = argparse.ArgumentParser(description="Dashboard y API de reportes con MongoDB")
    parser.add_argument("--env-file", type=Path, help="Archivo de credenciales; las variables del entorno tienen prioridad")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="Verifica conexión, lectura e integridad sin modificar MongoDB")
    actions.add_argument("--init-db", action="store_true", help="Crea o actualiza validadores e índices sin modificar reportes")
    args = parser.parse_args()
    backend = Path(__file__).resolve().parent
    env_file = args.env_file or backend.parent / ".env"
    # Sin interpolación: una contraseña con $ o ${...} se conserva literalmente.
    load_dotenv(env_file, override=False, interpolate=False)
    client = None
    try:
        config = environment_config()
        client = client_from_environment(config)
        database = client.get_database(config.get("MONGODB_DATABASE") or client.get_default_database("fraud_detection").name)
        reports = MongoReports(database)
        if args.check:
            reports.health()
            snapshot = reports.snapshot()
            event_count = 0
            for report in snapshot["reports"]:
                event_count += len(reports.get_tracking(report["_id"]["$oid"])["events"])
            print(f"MongoDB: conexión y lectura correctas; {len(snapshot['reports'])} reportes y {event_count} eventos verificados.", flush=True)
            client.close()
            return
        reports.initialize()
        if args.init_db:
            print("MongoDB: validadores e índices preparados; los reportes existentes se conservan.", flush=True)
            client.close()
            return
        server = ReportServer(
            (os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8765"))), reports,
            allowed_hosts=tuple(host.strip() for host in os.getenv("ALLOWED_HOSTS", "").split(",") if host.strip()),
            frontend_origins=tuple(origin.strip() for origin in os.getenv(
                "FRONTEND_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",") if origin.strip()),
        )
    except IntegrityError:
        if client is not None:
            client.close()
        parser.exit(1, "La conexión funciona, pero hay reportes o eventos con formato o hash incompatible.\n")
    except (PyMongoError, StorageError, ValueError, OSError):
        if client is not None:
            client.close()
        parser.exit(1, "No se pudo iniciar el servicio. Revisa la configuración de MongoDB, sus permisos y el puerto.\n")

    # shutdown debe ejecutarse desde otro hilo para no bloquear serve_forever.
    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Dashboard / API MongoDB en el puerto {server.server_port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        client.close()


if __name__ == "__main__":
    main()
