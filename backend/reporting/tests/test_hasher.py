import http.client
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from backend.reporting.hasher import IntegrityError, Hasher, ReportServer, canonical, digest


def location():
    return {"latitude": 4.61, "longitude": -74.082, "accuracy_m": 20}


def analysis_result(label="fraude"):
    return {"label": label, "score": None, "explanation": "Resultado de prueba",
            "model_name": "test-only", "model_version": "1", "model_digest": "a" * 64,
            "prompt_version": "1"}


class StartupTests(unittest.TestCase):
    def test_module_and_direct_script_entrypoints(self):
        root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as other_directory:
            for arguments, directory in ((["-m", "backend.reporting.hasher"], root),
                                         ([str(root / "backend" / "reporting" / "hasher.py")], other_directory)):
                with self.subTest(arguments=arguments):
                    result = subprocess.run([sys.executable, *arguments, "--help"], cwd=directory,
                                            capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                    self.assertIn(b"--db", result.stdout)
                    self.assertNotIn(b"--verify-checkpoint", result.stdout)


class HasherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "reports.sqlite3"
        self.hasher = Hasher(self.path)

    def create(self, title="Prueba", analysis=None):
        return self.hasher.create_report(title, "Texto original: áéñ\n<script>alert(1)</script>",
                                         "sms", geolocation=location(), analysis=analysis)

    def test_one_record_hash_covers_all_fields(self):
        before = datetime.now(timezone.utc)
        report = self.create(analysis=analysis_result())
        after = datetime.now(timezone.utc)
        payload = {key: value for key, value in report.items() if key != "hash"}
        self.assertEqual(report["hash"], digest(payload))
        self.assertLessEqual(before, datetime.fromisoformat(report["created_at"]))
        self.assertLessEqual(datetime.fromisoformat(report["created_at"]), after)
        self.assertEqual(report["geolocation"], location())
        self.assertNotIn("reported_by", report)
        for field, altered in (("geolocation", {**location(), "latitude": 1}), ("created_at", "2000-01-01"),
                               ("fraud_positive", False), ("analysis", None), ("text", "Otro texto")):
            self.assertNotEqual(report["hash"], digest({**payload, field: altered}))
        with self.hasher.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='events'").fetchone())
        self.assertNotIn("events", report)
        self.assertNotIn("previous_hash", report)

    def test_reads_and_reopening_never_add_records_or_change_hash(self):
        report = self.create()
        reopened = Hasher(self.path)
        for _ in range(3):
            self.assertEqual(reopened.snapshot(), {"reports": [report]})
            self.assertEqual(reopened.get_report(report["id"]), report)
            self.assertEqual(reopened.export(report["id"]), {"format": "fraud-report-v3", "report": report})
        with self.hasher.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 1)

    def test_all_verdicts_are_saved_at_creation(self):
        for label, expected in (("fraude", True), ("no_fraude", False), ("incierto", None)):
            report = self.create(analysis=analysis_result(label))
            self.assertIs(report["fraud_positive"], expected)
            self.assertEqual(report["analysis"]["label"], label)
        report = self.create()
        self.assertIsNone(report["fraud_positive"])
        self.assertIsNone(report["analysis"])
        self.assertEqual(len(self.hasher.snapshot()["reports"]), 4)

    def test_geolocation_and_content_are_validated(self):
        invalid = [None, {}, [], {**location(), "user": "Ana"}]
        for field, values in (("latitude", [-91, 91, True, "4", None, float("nan")]),
                              ("longitude", [-181, 181, float("inf")]),
                              ("accuracy_m", [-1, True, "12", 40075001])):
            invalid.extend({**location(), field: value} for value in values)
        for geo in invalid:
            with self.subTest(geo=geo), self.assertRaises(ValueError):
                self.hasher.create_report("Prueba", "Texto", "sms", geolocation=geo)
        for values in (("", "Texto", "sms"), ("Título", "   ", "sms"), ("Título", "Texto", "web")):
            with self.assertRaises(ValueError):
                self.hasher.create_report(*values, geolocation=location())
        self.assertEqual(self.hasher.snapshot()["reports"], [])

    def test_location_is_minimized_before_persistence_and_hashing(self):
        report = self.hasher.create_report("Prueba", "Texto", "sms", geolocation={
            "latitude": 4.609719, "longitude": -74.081749, "accuracy_m": 12.4})
        self.assertEqual(report["geolocation"], {"latitude": 4.61, "longitude": -74.082, "accuracy_m": 13})
        self.assertEqual(report["hash"], digest({key: value for key, value in report.items() if key != "hash"}))
        with self.hasher.connection() as db:
            raw = db.execute("SELECT report_json FROM reports").fetchone()[0]
        self.assertNotIn("4.609719", raw)
        self.assertNotIn("74.081749", raw)
        self.assertNotIn("reported_by", raw)

    def test_identified_v2_database_is_preserved_and_rejected(self):
        old_path = Path(self.temp.name) / "identified.sqlite3"
        with closing(sqlite3.connect(old_path)) as db, db:
            db.execute("CREATE TABLE reports (id TEXT, report_json TEXT, hash TEXT)")
            db.execute("INSERT INTO reports VALUES (?, ?, ?)",
                       ("old", canonical({"schema_version": 2, "reported_by": "Ana"}), "original"))
        original = old_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "formato anterior"):
            Hasher(old_path)
        self.assertEqual(old_path.read_bytes(), original)

    def test_invalid_model_result_creates_no_record(self):
        for score in (float("nan"), float("inf"), True, -1, 2, "0.5"):
            with self.assertRaises(ValueError):
                self.create(analysis={**analysis_result(), "score": score})
        for result in ({}, {**analysis_result(), "label": "invalid"},
                       {**analysis_result(), "model_digest": "invalid"}):
            with self.assertRaises(ValueError):
                self.create(analysis=result)
        self.assertEqual(self.hasher.snapshot()["reports"], [])

    def test_update_delete_and_replace_blocked(self):
        report = self.create()
        with self.hasher.connection() as db:
            for statement in ("UPDATE reports SET hash='bad'", "DELETE FROM reports",
                              "INSERT OR REPLACE INTO reports SELECT * FROM reports"):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
        self.assertEqual(self.hasher.get_report(report["id"]), report)

    def test_modified_record_is_rejected(self):
        report = self.create()
        with self.hasher.connection() as db:
            db.execute("DROP TRIGGER no_report_update")
            raw = json.loads(db.execute("SELECT report_json FROM reports").fetchone()[0])
            raw["geolocation"]["latitude"] = 1
            db.execute("UPDATE reports SET report_json=?", (canonical(raw),))
        with self.assertRaises(IntegrityError):
            self.hasher.snapshot()
        with self.assertRaises(IntegrityError):
            self.hasher.export(report["id"])

    def test_concurrent_reports_are_independent(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            created = list(pool.map(lambda i: self.create(f"Reporte {i}"), range(24)))
        self.assertEqual(len(self.hasher.snapshot()["reports"]), 24)
        self.assertEqual(len({r["id"] for r in created}), 24)
        self.assertEqual(len({r["hash"] for r in created}), 24)

    def test_old_database_is_preserved_and_rejected(self):
        old_path = Path(self.temp.name) / "old.sqlite3"
        with closing(sqlite3.connect(old_path)) as db, db:
            db.execute("CREATE TABLE events (seq INTEGER, event_json TEXT, hash TEXT)")
            db.execute("INSERT INTO events VALUES (1, 'legacy', 'saved-hash')")
        original = old_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "formato anterior"):
            Hasher(old_path)
        self.assertEqual(old_path.read_bytes(), original)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hasher = Hasher(Path(self.temp.name) / "reports.sqlite3")
        self.static = Path(self.temp.name) / "dist"
        self.static.mkdir()
        (self.static / "index.html").write_text("<!doctype html><div id='root'></div>", encoding="utf-8")
        (self.static / "assets").mkdir()
        (self.static / "assets" / "app.js").write_text("export const app = true", encoding="utf-8")
        self.server = ReportServer(("127.0.0.1", 0), self.hasher, static_dir=self.static)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)
        self.payload = {"title": "SMS de prueba", "text": "Mensaje", "source": "sms", "geolocation": location()}

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        default = {"Content-Type": "application/json", "X-CSRF-Token": self.server.csrf}
        default.update(headers or {})
        connection.request(method, path, body=None if data is None else json.dumps(data), headers=default)
        response = connection.getresponse()
        body, status = response.read(), response.status
        connection.close()
        return status, json.loads(body) if body.startswith(b"{") else body

    def test_create_read_export_one_record_without_model(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        status, created = self.request("POST", "/api/reports", self.payload)
        self.assertEqual(status, 201)
        for _ in range(2):
            status, export = self.request("GET", f"/api/reports/{created['id']}/export")
            self.assertEqual(status, 200)
            report = export["report"]
            self.assertEqual(report["hash"], created["hash"])
            self.assertNotIn("reported_by", report)
            self.assertEqual(report["geolocation"], location())
            self.assertIsNone(report["analysis"])
            self.assertIsNone(report["fraud_positive"])
            self.assertNotIn("checkpoint", export)
            self.assertNotIn("events", report)
            self.assertEqual(self.request("GET", f"/api/reports/{created['id']}")[1], report)
        self.assertEqual(len(self.request("GET", "/api/reports")[1]["reports"]), 1)

    def test_detector_runs_before_the_single_insert_and_never_on_reads(self):
        class TestDetector:
            label = "fraude"
            calls = 0

            def analyze(detector, text):
                detector.calls += 1
                self.assertEqual(text, self.payload["text"])
                self.assertEqual(len(self.hasher.snapshot()["reports"]), detector.calls - 1)
                return analysis_result(detector.label)

        self.server.detector = TestDetector()
        for label, expected in (("fraude", True), ("no_fraude", False), ("incierto", None)):
            self.server.detector.label = label
            status, created = self.request("POST", "/api/reports", self.payload)
            self.assertEqual(status, 201)
            report = self.request("GET", f"/api/reports/{created['id']}")[1]
            self.assertIs(report["fraud_positive"], expected)
            self.assertEqual(report["analysis"]["label"], label)
            self.request("GET", "/api/reports")
            self.request("GET", f"/api/reports/{created['id']}/export")
        self.assertEqual(self.server.detector.calls, 3)
        self.assertEqual(len(self.hasher.snapshot()["reports"]), 3)

    def test_detector_failure_or_invalid_output_saves_nothing(self):
        class TestDetector:
            value = None

            def analyze(detector, text):
                if isinstance(detector.value, Exception):
                    raise detector.value
                return detector.value

        self.server.detector = TestDetector()
        for value, expected_status in ((None, 503), (RuntimeError("failed"), 503), ({}, 502)):
            self.server.detector.value = value
            self.assertEqual(self.request("POST", "/api/reports", self.payload)[0], expected_status)
        self.assertEqual(self.hasher.snapshot()["reports"], [])

    def test_removed_features_are_unavailable(self):
        _, report = self.request("POST", "/api/reports", self.payload)
        for method, path in (("GET", "/api/checkpoint"), ("POST", "/api/verify-checkpoint"),
                             ("POST", f"/api/reports/{report['id']}/status"),
                             ("POST", f"/api/reports/{report['id']}/analyze")):
            self.assertEqual(self.request(method, path, {} if method == "POST" else None)[0], 404)
        for method in ("PUT", "PATCH", "DELETE"):
            self.assertEqual(self.request(method, f"/api/reports/{report['id']}", {})[0], 501)
        self.assertEqual(len(self.hasher.snapshot()["reports"]), 1)

    def test_invalid_requests_are_rejected_before_model_execution(self):
        class UnexpectedDetector:
            calls = 0

            def analyze(detector, text):
                detector.calls += 1
                return analysis_result()

        self.server.detector = UnexpectedDetector()
        for payload in ([1, 2], {**self.payload, "reported_by": " "},
                        {**self.payload, "created_at": "2000-01-01"},
                        {**self.payload, "analysis": analysis_result()}):
            self.assertEqual(self.request("POST", "/api/reports", payload)[0], 400)
        self.assertEqual(self.server.detector.calls, 0)
        self.assertEqual(self.hasher.snapshot()["reports"], [])
        self.assertEqual(self.request("GET", "/api/reports/missing/export")[0], 404)
        self.assertEqual(self.request("GET", "/../README.md")[0], 404)

    def test_cross_origin_and_host_and_token_rejected(self):
        for headers in ({"Origin": "https://attacker.example"}, {"Host": "attacker.example"},
                        {"X-CSRF-Token": "wrong"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("POST", "/api/reports", self.payload, headers)[0], 403)
        self.assertEqual(self.hasher.snapshot()["reports"], [])

    def test_anonymity_is_enforced_before_detector_and_no_metadata_is_saved(self):
        for field in ("reported_by", "user", "user_id", "ip", "user_agent"):
            status, _ = self.request("POST", "/api/reports", {**self.payload, field: "private"})
            self.assertEqual(status, 400)
        self.assertEqual(self.hasher.snapshot()["reports"], [])
        status, created = self.request("POST", "/api/reports", self.payload,
                                       {"User-Agent": "private-browser", "X-Forwarded-For": "192.0.2.12"})
        self.assertEqual(status, 201)
        export = self.request("GET", f"/api/reports/{created['id']}/export")[1]
        serialized = json.dumps(export)
        for value in ("private-browser", "192.0.2.12", "reported_by", "user_id", "user_agent"):
            self.assertNotIn(value, serialized)

    def test_location_required_over_http(self):
        for geo in (None, {}, {**location(), "latitude": 91}, {**location(), "longitude": float("nan")}):
            self.assertEqual(self.request("POST", "/api/reports", {**self.payload, "geolocation": geo})[0], 400)
        self.assertEqual(self.hasher.snapshot()["reports"], [])

    def test_development_proxy_origin_is_allowed_but_still_needs_csrf(self):
        for origin in ("http://127.0.0.1:5173", "http://localhost:5173"):
            self.assertEqual(self.request("POST", "/api/reports", self.payload, {"Origin": origin})[0], 201)
            self.assertEqual(self.request("POST", "/api/reports", self.payload,
                                          {"Origin": origin, "X-CSRF-Token": "wrong"})[0], 403)
        self.assertEqual(self.request("POST", "/api/reports", self.payload,
                                      {"Origin": "http://127.0.0.1:9000"})[0], 403)

    def test_analysis_api_is_proxied_under_the_dashboard_origin(self):
        received = []

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def send_json(self, body):
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                received.append(("GET", self.path, b""))
                self.send_json({"status": "ok"})

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append(("POST", self.path, body))
                self.send_json({"veredicto": "RIESGO BAJO"})

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        def cleanup_upstream():
            upstream.shutdown()
            upstream.server_close()
            thread.join()
        self.addCleanup(cleanup_upstream)
        self.server.analysis_base_url = f"http://127.0.0.1:{upstream.server_port}"

        self.assertEqual(self.request("GET", "/analysis-api/health"), (200, {"status": "ok"}))
        status, result = self.request("POST", "/analysis-api/analizar", {"texto": "Mensaje"})
        self.assertEqual((status, result), (200, {"veredicto": "RIESGO BAJO"}))
        self.assertEqual(received[0][:2], ("GET", "/api/health"))
        self.assertEqual(received[1][:2], ("POST", "/api/analizar"))
        self.assertEqual(json.loads(received[1][2]), {"texto": "Mensaje"})
        self.assertEqual(
            self.request("POST", "/analysis-api/analizar", {"texto": "Mensaje"},
                         {"Origin": "https://attacker.example"})[0],
            403,
        )

    def test_static_dashboard_and_path_traversal(self):
        (self.static / "geo").mkdir()
        (self.static / "geo" / "localidades.geojson").write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        for path in ("/", "/dashboard", "/dashboard/", "/dashboard/trazabilidad", "/assets/app.js", "/geo/localidades.geojson"):
            self.assertEqual(self.request("GET", path)[0], 200)
        (Path(self.temp.name) / "private.txt").write_text("secret", encoding="utf-8")
        for path in ("/assets/../../private.txt", "/assets/%2e%2e/%2e%2e/private.txt",
                     "/assets/%2e%2e%5c%2e%2e%5cprivate.txt", "/geo/../../private.txt", "/private.txt", "/missing-route"):
            self.assertEqual(self.request("GET", path)[0], 404)
        (self.static / "index.html").unlink()
        self.assertEqual(self.request("GET", "/")[0], 503)
        self.assertEqual(self.request("GET", "/api/session")[0], 200)

    def test_damaged_record_is_not_returned(self):
        _, report = self.request("POST", "/api/reports", self.payload)
        with self.hasher.connection() as db:
            db.execute("DROP TRIGGER no_report_update")
            db.execute("UPDATE reports SET hash='bad'")
        self.assertEqual(self.request("GET", "/api/reports")[0], 409)
        self.assertEqual(self.request("GET", f"/api/reports/{report['id']}/export")[0], 409)


if __name__ == "__main__":
    unittest.main()
