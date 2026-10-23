"""Ejecuta toda la suite contra un mongod temporal, sin usar credenciales reales."""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError


def main():
    executable = shutil.which("mongod")
    if not executable:
        raise SystemExit("Instala mongod o ejecuta unittest con TEST_MONGODB_URI apuntando a una instancia de pruebas.")
    with socket.socket() as address:
        address.bind(("127.0.0.1", 0))
        port = address.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fraud-mongo-test-") as directory:
        process = subprocess.Popen([executable, "--dbpath", directory, "--port", str(port),
                                    "--bind_ip", "127.0.0.1", "--nounixsocket",
                                    "--logpath", str(Path(directory) / "mongod.log")],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        uri = f"mongodb://127.0.0.1:{port}/"
        try:
            with MongoClient(uri, serverSelectionTimeoutMS=500) as client:
                for _ in range(30):
                    if process.poll() is not None:
                        raise SystemExit("El mongod de pruebas no pudo iniciarse.")
                    try:
                        client.admin.command("ping")
                        break
                    except PyMongoError:
                        time.sleep(0.1)
                else:
                    raise SystemExit("El mongod de pruebas no estuvo disponible a tiempo.")
            result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "backend/reporting/tests", "-v"],
                                    cwd=Path(__file__).resolve().parents[3],
                                    env={**os.environ, "TEST_MONGODB_URI": uri})
            return result.returncode
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    sys.exit(main())
