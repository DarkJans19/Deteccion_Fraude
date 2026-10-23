"""Pruebas aisladas. TEST_MONGODB_URI habilita además BSON, índices y validadores reales."""

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import mongomock
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError, WriteError

from backend.reporting.hasher import ConflictError, IntegrityError, ReportServer
from backend.reporting.mongo import (MongoReports, REPORT_SCHEMA, client_from_environment,
                                            environment_config, legacy_report_hash, report_hash)


def payload(**changes):
    return {"description": "Mensaje SMS fraudulento que suplanta una entidad bancaria.",
            "messageContent": "BANCO: Su cuenta está bloqueada.\nValide sus datos en https://ejemplo.invalid/verificar\nCódigo: áéñ <script>alert(1)</script>",
            "riskLevel": "RIESGO ALTO",
            "date": "2026-10-22T15:30:00.123456Z",
            "location": {"type": "Point", "coordinates": [-74.0721, 4.7110]}, **changes}


class MongoTests(unittest.TestCase):
    def setUp(self):
        uri = os.getenv("TEST_MONGODB_URI")
        self.client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000) if uri else mongomock.MongoClient(tz_aware=True)
        self.database = self.client[f"test_fraud_{uuid4().hex}"]
        self.store = MongoReports(self.database)
        self.addCleanup(self.client.close)
        self.addCleanup(lambda: self.client.drop_database(self.database.name))
        if uri:
            self.store.initialize()
        else:
            self.store.reports.create_index("hashCode", unique=True)
        self.server = ReportServer(("127.0.0.1", 0), self.store, allowed_hosts=("fraud.example:443",),
                                   frontend_origins=("https://fraud.example",))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, data=None, **headers):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        defaults = {"Content-Type": "application/json", "X-CSRF-Token": self.server.csrf, **headers}
        connection.request(method, path, body=json.dumps(data) if data is not None else None, headers=defaults)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def create(self, **changes):
        document = self.store.create_document(payload(**changes))
        return document["_id"]["$oid"], document

    def test_native_bson_exact_schema_and_precision_survive_reopening(self):
        report_id, document = self.create()
        self.assertEqual(set(document), {"_id", "description", "messageContent", "riskLevel", "date", "hashCode", "location"})
        self.assertEqual(document["riskLevel"], "RIESGO ALTO")
        raw = self.store.reports.find_one({"_id": ObjectId(report_id)})
        self.assertIsInstance(raw["_id"], ObjectId)
        self.assertIsInstance(raw["date"], datetime)
        self.assertEqual(raw["date"].microsecond, 123000)
        self.assertEqual(raw["date"].utcoffset().total_seconds(), 0)
        self.assertEqual(raw["location"]["coordinates"], [-74.0721, 4.711])
        self.assertEqual(document["date"], {"$date": "2026-10-22T15:30:00.123Z"})
        self.assertEqual(raw["hashCode"], report_hash(raw))
        self.assertEqual(raw["messageContent"], payload()["messageContent"])
        self.assertNotEqual(raw["description"], raw["messageContent"])
        reopened = MongoReports(self.database)
        self.assertEqual(reopened.get_report(report_id), document)
        self.assertEqual(reopened.snapshot(), {"reports": [document]})
        self.assertEqual(self.store.events.count_documents({}), 0)

    def test_dates_with_offsets_are_hashed_in_utc_and_default_date_is_generated(self):
        _, document = self.create(date="2026-10-22T10:30:00.123-05:00")
        self.assertEqual(document["date"]["$date"], "2026-10-22T15:30:00.123Z")
        other = payload(description="Sin fecha del cliente")
        other.pop("date")
        before = datetime.now(timezone.utc).timestamp() - 1
        created = self.store.create_document(other)
        self.assertGreaterEqual(datetime.fromisoformat(created["date"]["$date"].replace("Z", "+00:00")).timestamp(), before)

    def test_duplicates_and_invalid_hash_do_not_add_records(self):
        _, document = self.create()
        with self.assertRaises(ConflictError):
            self.store.create_document(payload())
        self.assertEqual(self.request("POST", "/api/reports", payload())[0], 409)
        self.assertEqual(self.request("POST", "/api/reports", payload(description="Otro", hashCode="a" * 64))[0], 400)
        self.assertEqual(self.store.reports.count_documents({}), 1)
        self.assertRegex(document["hashCode"], "^[a-f0-9]{64}$")

    def test_invalid_content_dates_and_coordinates_never_persist(self):
        invalid = [{"description": " "}, {"description": "x" * 2001}, {"date": None},
                   {"messageContent": None}, {"messageContent": " "}, {"messageContent": "x" * 50001},
                   {"date": "2026-10-22"}, {"date": "invalid"}, {"date": {}},
                   {"location": None}, {"location": {"type": "Polygon", "coordinates": []}},
                   {"location": {"type": "Point", "coordinates": [4, 200]}},
                   {"reported_by": "Ana"}, {"_id": {"$oid": str(ObjectId())}}]
        invalid.extend({"location": {"type": "Point", "coordinates": coords}} for coords in
                       ([181, 4], [-74, 91], [True, 4], ["-74", 4], [float("nan"), 4], [-74]))
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assertEqual(self.request("POST", "/api/reports", payload(**changes))[0], 400)
        self.assertEqual(self.store.reports.count_documents({}), 0)
        missing = payload()
        missing.pop("messageContent")
        self.assertEqual(self.request("POST", "/api/reports", missing)[0], 400)

    def test_full_message_changes_hash_and_tampering_is_rejected(self):
        report_id, original = self.create()
        _, other = self.create(messageContent=original["messageContent"] + "\nOtro enlace")
        self.assertNotEqual(original["hashCode"], other["hashCode"])
        self.assertEqual(self.store.export(report_id)["report"]["messageContent"], payload()["messageContent"])
        self.store.reports.update_one({"_id": ObjectId(report_id)}, {"$set": {"messageContent": "Mensaje alterado"}})
        self.assertEqual(self.request("GET", f"/api/reports/{report_id}")[0], 409)

    def test_supplied_hash_can_be_uppercase_but_must_match_content(self):
        data = payload()
        data["hashCode"] = report_hash(data).upper()
        document = self.store.create_document(data)
        self.assertEqual(document["hashCode"], data["hashCode"].lower())
        report_id = document["_id"]["$oid"]
        self.store.reports.update_one({"_id": ObjectId(report_id)}, {"$set": {"hashCode": data["hashCode"]}})
        self.assertEqual(self.store.get_report(report_id)["hashCode"], data["hashCode"])

    def test_legacy_report_is_read_without_fabricating_message_content(self):
        legacy = payload()
        legacy.pop("messageContent")
        legacy.pop("riskLevel")
        legacy["_id"] = ObjectId()
        legacy["date"] = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)
        legacy["hashCode"] = legacy_report_hash(legacy)
        self.store.reports.insert_one(legacy, bypass_document_validation=True)
        loaded = self.store.get_report(str(legacy["_id"]))
        self.assertNotIn("messageContent", loaded)
        self.assertEqual(loaded["hashCode"], legacy["hashCode"])

    def test_existing_full_report_without_risk_field_keeps_its_original_hash(self):
        old_report = payload()
        old_report.pop("riskLevel")
        old_report["_id"] = ObjectId()
        old_report["date"] = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)
        old_report["hashCode"] = report_hash(old_report)
        self.store.reports.insert_one(old_report, bypass_document_validation=True)
        loaded = self.store.get_report(str(old_report["_id"]))
        self.assertNotIn("riskLevel", loaded)
        self.assertEqual(loaded["hashCode"], old_report["hashCode"])

    def test_only_high_risk_reports_are_persisted(self):
        for risk in ("RIESGO BAJO", "RIESGO NO CONCLUYENTE", None):
            data = payload()
            if risk is None:
                data.pop("riskLevel")
            else:
                data["riskLevel"] = risk
            self.assertEqual(self.request("POST", "/api/reports", data)[0], 400)
        self.assertEqual(self.store.reports.count_documents({}), 0)
        self.assertEqual(self.request("POST", "/api/reports", payload())[0], 201)
        self.assertEqual(self.store.reports.count_documents({}), 1)

    def test_http_native_create_read_export_and_unknown_cases(self):
        status, document = self.request("POST", "/api/reports", payload())
        self.assertEqual(status, 201)
        report_id = document["_id"]["$oid"]
        self.assertEqual(self.request("GET", f"/api/reports/{report_id}"), (200, document))
        self.assertEqual(self.request("GET", "/api/reports"), (200, {"reports": [document]}))
        status, exported = self.request("GET", f"/api/reports/{report_id}/export")
        self.assertEqual(status, 200)
        self.assertEqual(exported["report"], document)
        self.assertEqual(exported["tracking"]["status"], "registrado")
        for identifier in ("invalid", str(ObjectId())):
            for suffix in ("", "/export", "/tracking"):
                self.assertEqual(self.request("GET", f"/api/reports/{identifier}{suffix}")[0], 404)

    def test_tracking_retains_original_report_and_is_separate_for_each_case(self):
        report_id, original = self.create()
        other_id, _ = self.create(description="Segundo caso")
        self.assertEqual(self.store.get_tracking(report_id)["status"], "registrado")
        for status in ("en_revision", "confirmado", "cerrado"):
            code, event = self.request("POST", f"/api/reports/{report_id}/tracking", {"status": status, "note": f"Nota: {status}"})
            self.assertEqual(code, 201)
            self.assertEqual(event["reportId"], {"$oid": report_id})
            self.assertEqual(event["reportHashCode"], original["hashCode"])
        status, history = self.request("GET", f"/api/reports/{report_id}/tracking")
        self.assertEqual(status, 200)
        self.assertEqual(history["status"], "cerrado")
        self.assertEqual(len(history["events"]), 3)
        self.assertEqual(self.store.get_report(report_id), original)
        self.assertEqual(self.store.get_tracking(other_id)["events"], [])
        self.assertEqual(self.store.reports.count_documents({}), 2)

    def test_invalid_tracking_and_missing_reports_leave_no_events(self):
        report_id, _ = self.create()
        for value in ({"status": "bad", "note": "x"}, {"status": "cerrado", "note": " "},
                      {"status": "cerrado", "note": "x" * 5001}, {"status": "cerrado"},
                      {"status": "cerrado", "note": "x", "user": "Ana"}):
            self.assertEqual(self.request("POST", f"/api/reports/{report_id}/tracking", value)[0], 400)
        self.assertEqual(self.request("POST", f"/api/reports/{ObjectId()}/tracking", {"status": "cerrado", "note": "x"})[0], 404)
        self.assertEqual(self.store.events.count_documents({}), 0)

    def test_concurrent_tracking_does_not_lose_events_or_change_report(self):
        report_id, original = self.create()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: self.store.add_tracking(report_id, "en_revision", f"Nota {i}"), range(24)))
        history = self.store.get_tracking(report_id)
        self.assertEqual(len(history["events"]), 24)
        self.assertEqual(len({e["_id"]["$oid"] for e in history["events"]}), 24)
        self.assertEqual(self.store.get_report(report_id), original)

    def test_tampered_reports_and_events_are_not_returned(self):
        report_id, _ = self.create()
        self.store.add_tracking(report_id, "confirmado", "Resultado")
        self.store.events.update_one({"reportId": ObjectId(report_id)}, {"$set": {"note": "alterada"}})
        self.assertEqual(self.request("GET", f"/api/reports/{report_id}/tracking")[0], 409)
        self.assertEqual(self.request("GET", f"/api/reports/{report_id}/export")[0], 409)
        self.store.reports.update_one({"_id": ObjectId(report_id)}, {"$set": {"description": "alterada"}})
        with self.assertRaises(IntegrityError):
            self.store.get_report(report_id)
        self.assertEqual(self.request("GET", "/api/reports")[0], 409)

    def test_csrf_origin_and_configured_hosts_apply_to_tracking(self):
        report_id, _ = self.create()
        route = f"/api/reports/{report_id}/tracking"
        data = {"status": "en_revision", "note": "Revisión"}
        for headers in ({"X-CSRF-Token": "bad"}, {"Origin": "https://evil.example"}, {"Host": "evil.example"}):
            self.assertEqual(self.request("POST", route, data, **headers)[0], 403)
        self.assertEqual(self.request("POST", route, data, Host="fraud.example:443", Origin="https://fraud.example")[0], 201)
        self.assertEqual(self.store.events.count_documents({}), 1)

    def test_database_errors_are_sanitized_and_health_returns_unavailable(self):
        with patch.object(self.database, "command", side_effect=ServerSelectionTimeoutError("SECRET_PASSWORD")):
            status, body = self.request("GET", "/api/health")
        self.assertEqual(status, 503)
        self.assertNotIn("SECRET_PASSWORD", json.dumps(body))
        with patch.object(self.store.reports, "insert_one", side_effect=OperationFailure("SECRET_URI")):
            status, body = self.request("POST", "/api/reports", payload())
        self.assertEqual(status, 503)
        self.assertNotIn("SECRET_URI", json.dumps(body))

    @unittest.skipUnless(os.getenv("TEST_MONGODB_URI"), "Requiere un MongoDB real de pruebas")
    def test_app_entrypoint_restart_persistence_and_graceful_shutdown(self):
        with socket.socket() as address:
            address.bind(("127.0.0.1", 0))
            port = address.getsockname()[1]
        env = {**os.environ, "MONGODB_URI": os.environ["TEST_MONGODB_URI"],
               "MONGODB_USERNAME": "", "MONGODB_PASSWORD": "", "MONGODB_ENV_RAW": "",
               "MONGODB_DATABASE": self.database.name, "HOST": "127.0.0.1", "PORT": str(port)}
        created = None
        for _ in range(2):
            process = subprocess.Popen([sys.executable, "-m", "backend.api.main"], env=env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            try:
                session = None
                for _ in range(50):
                    if process.poll() is not None:
                        self.fail("El servidor terminó antes de estar disponible: " + process.stderr.read())
                    try:
                        with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=1)) as connection:
                            connection.request("GET", "/api/session")
                            response = connection.getresponse()
                            if response.status == 200:
                                session = json.loads(response.read())
                                break
                    except OSError:
                        time.sleep(0.1)
                self.assertIsNotNone(session)
                with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=5)) as connection:
                    if created is None:
                        connection.request("POST", "/api/reports", json.dumps(payload()),
                                           {"Content-Type": "application/json", "X-CSRF-Token": session["csrf"]})
                        response = connection.getresponse()
                        self.assertEqual(response.status, 201)
                        created = json.loads(response.read())
                    else:
                        connection.request("GET", f"/api/reports/{created['_id']['$oid']}")
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(json.loads(response.read()), created)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stderr.close()
            self.assertEqual(process.returncode, 0)
        before = (self.store.reports.count_documents({}), self.store.events.count_documents({}),
                  sorted(self.database.list_collection_names()))
        checked = subprocess.run([sys.executable, "-m", "backend.api.main", "--check"], env=env,
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn("1 reportes y 0 eventos verificados", checked.stdout)
        self.assertEqual(before, (self.store.reports.count_documents({}), self.store.events.count_documents({}),
                                  sorted(self.database.list_collection_names())))
        self.store.reports.update_one({"_id": ObjectId(created["_id"]["$oid"])}, {"$set": {"hashCode": "b" * 64}})
        checked = subprocess.run([sys.executable, "-m", "backend.api.main", "--check"], env=env,
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(checked.returncode, 1)
        self.assertIn("formato o hash incompatible", checked.stderr)
        self.assertEqual(self.store.reports.find_one()["hashCode"], "b" * 64)

    @unittest.skipUnless(os.getenv("TEST_MONGODB_URI"), "Requiere un MongoDB real de pruebas")
    def test_real_validators_indexes_and_idempotent_initialization(self):
        self.store.initialize()
        self.assertEqual(self.request("GET", "/api/health"), (200, {"status": "ok"}))
        self.create()
        indexes = self.store.reports.index_information()
        self.assertTrue(indexes["report_hash_unique"]["unique"])
        self.assertEqual(indexes["report_location"]["key"], [("location", "2dsphere")])
        for changes in ({"date": "not BSON"}, {"extra": "forbidden"}, {"hashCode": "bad"},
                        {"riskLevel": "RIESGO BAJO"},
                        {"description": "x" * 2001}, {"messageContent": None}, {"messageContent": "x" * 50001}):
            raw = {"_id": ObjectId(), "description": "Prueba", "date": datetime.now(timezone.utc),
                   "messageContent": "Texto completo del mensaje original",
                   "riskLevel": "RIESGO ALTO",
                   "hashCode": "b" * 64, "location": {"type": "Point", "coordinates": [-74, 4]}, **changes}
            with self.assertRaises(WriteError):
                self.store.reports.insert_one(raw)
        raw = payload()
        raw.pop("messageContent")
        raw.pop("riskLevel")
        raw["date"] = datetime.now(timezone.utc)
        raw["hashCode"] = "c" * 64
        with self.assertRaises(WriteError):
            self.store.reports.insert_one(raw)

    @unittest.skipUnless(os.getenv("TEST_MONGODB_URI"), "Requiere un MongoDB real de pruebas")
    def test_existing_validator_is_upgraded_without_rewriting_legacy_documents(self):
        legacy_schema = {**REPORT_SCHEMA, "required": [field for field in REPORT_SCHEMA["required"]
                                                         if field not in {"messageContent", "riskLevel"}],
                         "properties": {key: value for key, value in REPORT_SCHEMA["properties"].items()
                                        if key != "riskLevel"}}
        self.database.command("collMod", "fraud_reports", validator={"$jsonSchema": legacy_schema})
        legacy = payload()
        legacy.pop("messageContent")
        legacy.pop("riskLevel")
        legacy["date"] = datetime.now(timezone.utc).replace(microsecond=0)
        legacy["hashCode"] = legacy_report_hash(legacy)
        inserted = self.store.reports.insert_one(legacy).inserted_id
        before = self.store.reports.find_one({"_id": inserted})
        self.store.initialize()
        options = next(self.database.list_collections(filter={"name": "fraud_reports"}))["options"]
        self.assertIn("messageContent", options["validator"]["$jsonSchema"]["required"])
        self.assertEqual(options["validationLevel"], "strict")
        self.assertEqual(self.store.reports.find_one({"_id": inserted}), before)
        self.assertEqual(self.store.get_report(str(inserted))["hashCode"], legacy["hashCode"])
        with self.assertRaises(WriteError):
            self.store.reports.insert_one({key: value for key, value in legacy.items() if key != "_id"})

    @unittest.skipUnless(os.getenv("TEST_MONGODB_URI") and shutil.which("mongosh"), "Requiere MongoDB y mongosh de pruebas")
    def test_docker_init_script_uses_the_same_validator_and_is_idempotent(self):
        root = Path(__file__).resolve().parents[3]
        env = {**os.environ, "FRAUD_SCHEMA_PATH": str(root / "backend/reporting/database/schema.json"),
               "MONGO_INITDB_DATABASE": self.database.name}
        report_id, original = self.create()
        self.store.reports.drop_index("report_location")
        self.store.reports.create_index([("location", "2dsphere")], name="location_2dsphere")
        for _ in range(2):
            result = subprocess.run(["mongosh", os.environ["TEST_MONGODB_URI"], "--quiet", "--file",
                                     str(root / "backend/reporting/database/10-init.js")],
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.store.initialize()
        self.assertIn("location_2dsphere", self.store.reports.index_information())
        self.assertNotIn("report_location", self.store.reports.index_information())
        self.assertEqual(self.store.get_report(report_id), original)
        options = next(self.database.list_collections(filter={"name": "fraud_reports"}))["options"]
        self.assertEqual(options["validator"]["$jsonSchema"], REPORT_SCHEMA)


class ConfigurationTests(unittest.TestCase):
    def test_compose_raw_values_preserve_password_literals_and_decode_dotenv_quotes(self):
        raw = {"MONGODB_ENV_RAW": "1", "MONGODB_URI": '"mongodb://localhost/"',
               "MONGODB_USERNAME": "'test'", "MONGODB_PASSWORD": '"p@$${literal}:word"',
               "MONGODB_DATABASE": "fraud_detection"}
        config = environment_config(raw)
        self.assertEqual(config["MONGODB_URI"], "mongodb://localhost/")
        self.assertEqual(config["MONGODB_PASSWORD"], "p@$${literal}:word")
        self.assertEqual(config["MONGODB_USERNAME"], "test")
        self.assertNotIn("MONGODB_ENV_RAW", config)
        self.assertEqual(environment_config({"MONGODB_PASSWORD": "'literal-quotes'"})["MONGODB_PASSWORD"], "'literal-quotes'")

    def test_separate_credentials_are_not_interpolated_into_uri(self):
        config = {"MONGODB_URI": "mongodb://localhost:27017/?authSource=admin",
                  "MONGODB_USERNAME": "test", "MONGODB_PASSWORD": "p@$${literal}:word"}
        with patch("backend.reporting.mongo.MongoClient") as client:
            client_from_environment(config)
        args, kwargs = client.call_args
        self.assertEqual(args, (config["MONGODB_URI"],))
        self.assertEqual(kwargs["password"], config["MONGODB_PASSWORD"])

    def test_embedded_credentials_take_precedence_and_invalid_configuration_is_rejected(self):
        with patch("backend.reporting.mongo.MongoClient") as client:
            client_from_environment({"MONGODB_URI": "mongodb://name:secret@localhost/", "MONGODB_PASSWORD": "unused"})
            self.assertNotIn("password", client.call_args.kwargs)
        for config in ({}, {"MONGODB_URI": "https://example.com"},
                       {"MONGODB_URI": "mongodb://localhost/", "MONGODB_USERNAME": "test"}):
            with self.assertRaises(ValueError):
                client_from_environment(config)


if __name__ == "__main__":
    unittest.main()
