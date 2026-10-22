"""Reportes BSON inmutables y seguimiento por eventos independientes.

fraud_reports separa description del texto original en messageContent.
El hash cubre ambos textos, date UTC con milisegundos y location normalizada.
Las escrituras son inserciones atómicas; no requieren un replica set.
"""

import json
import math
import os
from datetime import datetime, timezone
from functools import wraps
from io import StringIO
from pathlib import Path

from bson import ObjectId, json_util
from dotenv import dotenv_values
from pymongo import ASCENDING, DESCENDING, GEOSPHERE, MongoClient
from pymongo.errors import CollectionInvalid, DuplicateKeyError, PyMongoError

from backend.core.traceability.hasher import (
    ConflictError, IntegrityError, StorageError, digest, required_text,
)

SCHEMAS = json.loads((Path(__file__).resolve().parents[2] / "mongo" / "schema.json").read_text(encoding="utf-8"))
REPORT_SCHEMA = SCHEMAS["fraud_reports"]
EVENT_SCHEMA = SCHEMAS["fraud_report_events"]
STATUSES = tuple(EVENT_SCHEMA["properties"]["status"]["enum"])
REPORT_FIELDS = set(REPORT_SCHEMA["required"])
LEGACY_REPORT_FIELDS = REPORT_FIELDS - {"messageContent"}


def storage_operation(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except DuplicateKeyError:
            raise ConflictError("Este reporte ya está registrado (hashCode duplicado)") from None
        except PyMongoError:
            raise StorageError("No se pudo acceder a MongoDB") from None
    return wrapped


def utc_date(value):
    if isinstance(value, dict) and set(value) == {"$date"}:
        value = value["$date"]
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("date debe ser una fecha ISO 8601 con zona horaria") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("date debe ser una fecha ISO 8601 con zona horaria")
    value = value.astimezone(timezone.utc)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def iso_date(value):
    return utc_date(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def validate_location(value):
    if not isinstance(value, dict) or set(value) != {"type", "coordinates"} or value["type"] != "Point":
        raise ValueError("location requiere type: Point y coordinates: [longitud, latitud]")
    coords = value["coordinates"]
    if not isinstance(coords, (list, tuple)) or len(coords) != 2:
        raise ValueError("coordinates debe contener [longitud, latitud]")
    for coordinate, limit in zip(coords, (180, 90)):
        if (type(coordinate) not in (int, float) or not math.isfinite(coordinate)
                or not -limit <= coordinate <= limit):
            raise ValueError("Coordenadas inválidas: longitud [-180, 180], latitud [-90, 90]")
    # La representación numérica del hash no depende de recibir enteros o flotantes.
    return {"type": "Point", "coordinates": [float(coord) for coord in coords]}


def report_hash(document):
    return digest({"description": required_text(document["description"], "description", 2000),
                   "messageContent": required_text(document["messageContent"], "messageContent", 50000),
                   "date": iso_date(document["date"]), "location": validate_location(document["location"])})


def legacy_report_hash(document):
    """Comprueba reportes anteriores sin inventar el contenido que no se guardó."""
    return digest({"description": required_text(document["description"], "description", 50000),
                   "date": iso_date(document["date"]), "location": validate_location(document["location"])})


def validate_hash(value):
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in value)):
        raise ValueError("hashCode debe contener 64 caracteres hexadecimales SHA-256")
    return value.lower()


def event_hash(event):
    return digest({"_id": str(event["_id"]), "reportId": str(event["reportId"]),
                   "reportHashCode": event["reportHashCode"], "date": iso_date(event["date"]),
                   "status": event["status"], "note": event["note"]})


def extended_json(value):
    return json.loads(json_util.dumps(value, json_options=json_util.RELAXED_JSON_OPTIONS))


def object_id(value):
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise KeyError(value)
    return ObjectId(value)


def environment_config(environ=None):
    config = dict(os.environ if environ is None else environ)
    if config.pop("MONGODB_ENV_RAW", "") == "1":
        # Compose raw conserva comillas y $. Interpretar las comillas como dotenv,
        # sin expandir referencias que formen parte de una contraseña.
        for key in ("MONGODB_URI", "MONGODB_USERNAME", "MONGODB_PASSWORD", "MONGODB_DATABASE"):
            if key in config:
                config[key] = dotenv_values(stream=StringIO("VALUE=" + config[key]), interpolate=False)["VALUE"] or ""
    return config


def client_from_environment(environ=None):
    config = environment_config(environ)
    uri = config.get("MONGODB_URI", "").strip()
    if not uri.startswith(("mongodb://", "mongodb+srv://")):
        raise ValueError("Configura MONGODB_URI en el archivo de entorno")
    options = {"tz_aware": True, "serverSelectionTimeoutMS": 5000,
               "connectTimeoutMS": 5000, "socketTimeoutMS": 10000,
               "appname": "deteccion-fraude"}
    # Las credenciales separadas se pasan al driver sin interpolarlas ni registrarlas.
    if "@" not in uri.split("?", 1)[0]:
        username, password = config.get("MONGODB_USERNAME"), config.get("MONGODB_PASSWORD")
        if bool(username) != bool(password):
            raise ValueError("Define MONGODB_USERNAME y MONGODB_PASSWORD juntos")
        if username:
            options.update(username=username, password=password)
    return MongoClient(uri, **options)


def ensure_index(collection, keys, name, *, unique=False):
    """Reutiliza índices equivalentes creados fuera de la aplicación."""
    for options in collection.index_information().values():
        if (options.get("key") == keys and bool(options.get("unique", False)) == unique
                and not options.get("partialFilterExpression") and not options.get("sparse")
                and not options.get("collation")):
            return
    collection.create_index(keys, name=name, unique=unique)


class MongoReports:
    def __init__(self, database):
        self.database = database
        self.reports = database.fraud_reports
        self.events = database.fraud_report_events

    @storage_operation
    def initialize(self):
        self.health()
        for name, schema in (("fraud_reports", REPORT_SCHEMA), ("fraud_report_events", EVENT_SCHEMA)):
            validator = {"$jsonSchema": schema}
            existing = next(self.database.list_collections(filter={"name": name}), None)
            if existing is None:
                try:
                    self.database.create_collection(name, validator=validator,
                                                    validationLevel="strict", validationAction="error")
                    continue
                except CollectionInvalid:
                    existing = next(self.database.list_collections(filter={"name": name}))
            options = existing["options"]
            if (options.get("validator") != validator or options.get("validationLevel", "strict") != "strict"
                    or options.get("validationAction", "error") != "error"):
                # collMod actualiza las reglas de escritura sin reescribir documentos.
                self.database.command("collMod", name, validator=validator,
                                      validationLevel="strict", validationAction="error")
        ensure_index(self.reports, [("hashCode", ASCENDING)], "report_hash_unique", unique=True)
        ensure_index(self.reports, [("date", DESCENDING), ("_id", DESCENDING)], "report_date")
        ensure_index(self.reports, [("location", GEOSPHERE)], "report_location")
        ensure_index(self.events, [("reportId", ASCENDING), ("date", ASCENDING), ("_id", ASCENDING)], "report_history")

    @storage_operation
    def health(self):
        self.database.command("ping")

    @staticmethod
    def verify(document):
        try:
            fields = set(document)
            expected = (report_hash(document) if fields == REPORT_FIELDS else
                        legacy_report_hash(document) if fields == LEGACY_REPORT_FIELDS else None)
            if (not isinstance(document["_id"], ObjectId) or expected is None
                    or validate_hash(document["hashCode"]) != expected):
                raise ValueError("Formato o hash inválido")
        except (ValueError, TypeError, KeyError, OverflowError):
            raise IntegrityError("El reporte fue alterado o tiene un hash incompatible") from None
        return document

    @storage_operation
    def create_document(self, data):
        if set(data) - {"description", "messageContent", "date", "hashCode", "location"}:
            raise ValueError("Campos permitidos: description, messageContent, date, hashCode, location; _id lo genera MongoDB")
        document = {"_id": ObjectId(),
                    "description": required_text(data.get("description"), "description", 2000),
                    "messageContent": required_text(data.get("messageContent"), "messageContent", 50000),
                    "date": utc_date(data.get("date", datetime.now(timezone.utc))),
                    "location": validate_location(data.get("location"))}
        document["hashCode"] = report_hash(document)
        if "hashCode" in data and validate_hash(data["hashCode"]) != document["hashCode"]:
            raise ValueError("hashCode no coincide con el SHA-256 del reporte; omítelo para calcularlo en el servidor")
        self.reports.insert_one(document)
        return extended_json(document)

    def _get(self, report_id):
        document = self.reports.find_one({"_id": object_id(report_id)})
        if document is None:
            raise KeyError(report_id)
        return self.verify(document)

    @storage_operation
    def get_report(self, report_id):
        return extended_json(self._get(report_id))

    @storage_operation
    def snapshot(self):
        documents = self.reports.find().sort([("date", DESCENDING), ("_id", DESCENDING)])
        return {"reports": [extended_json(self.verify(doc)) for doc in documents]}

    @storage_operation
    def get_tracking(self, report_id):
        report = self._get(report_id)
        events = list(self.events.find({"reportId": report["_id"]}).sort([("date", ASCENDING), ("_id", ASCENDING)]))
        for event in events:
            try:
                if (set(event) != set(EVENT_SCHEMA["required"])
                        or not isinstance(event["_id"], ObjectId)
                        or event["reportHashCode"] != report["hashCode"]
                        or event["status"] not in STATUSES
                        or event["hashCode"] != event_hash(event)):
                    raise ValueError("Evento inválido")
                required_text(event["note"], "note", 5000)
            except (ValueError, TypeError, KeyError):
                raise IntegrityError("El seguimiento del reporte fue alterado") from None
        return {"reportId": report_id, "status": events[-1]["status"] if events else "registrado",
                "registeredAt": iso_date(report["date"]), "events": extended_json(events)}

    @storage_operation
    def add_tracking(self, report_id, status, note):
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError(f"status debe ser uno de: {', '.join(STATUSES)}")
        note = required_text(note, "note", 5000)
        report = self._get(report_id)
        event = {"_id": ObjectId(), "reportId": report["_id"], "reportHashCode": report["hashCode"],
                 "date": utc_date(datetime.now(timezone.utc)), "status": status, "note": note}
        event["hashCode"] = event_hash(event)
        self.events.insert_one(event)
        return extended_json(event)

    def export(self, report_id):
        return {"format": "fraud-report-mongodb-v1", "report": self.get_report(report_id),
                "tracking": self.get_tracking(report_id)}
