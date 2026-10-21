"""Registro único de cada reporte, sellado con SHA-256 al crearlo."""

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class IntegrityError(Exception):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def required_text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name}: se requiere texto de 1 a {limit} caracteres")
    return value


class Ledger:
    """Almacena reportes independientes; no genera eventos ni permite modificarlos."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
                raise ValueError("Esta base usa el formato anterior de eventos. Consérvala y elige otra ruta con --db.")
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
    def validate_content(title, text, source, source_ref, reported_by):
        content = {
            "title": required_text(title, "título", 160),
            "text": required_text(text, "contenido", 50000),
            "source": source, "source_ref": source_ref,
            "reported_by": required_text(reported_by, "usuario que reporta", 120).strip(),
        }
        if source not in ("sms", "correo", "otro"):
            raise ValueError("Origen inválido")
        if not isinstance(source_ref, str) or len(source_ref) > 500:
            raise ValueError("La referencia admite hasta 500 caracteres")
        return content

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

    def create_report(self, title, text, source, source_ref="", *, reported_by, analysis=None):
        content = self.validate_content(title, text, source, source_ref, reported_by)
        analysis = self.validate_analysis(analysis)
        report = {
            "schema_version": 2, "id": str(uuid4()), **content,
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
            if (report["id"] != report_id or report["schema_version"] != 2
                    or "hash" in report or digest(report) != saved_hash):
                raise IntegrityError(f"El reporte {report_id} fue alterado")
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
        return {"format": "fraud-report-v2", "report": self.get_report(report_id)}
