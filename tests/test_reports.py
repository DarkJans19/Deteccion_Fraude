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
from pathlib import Path

from fraud_reports.ledger import IntegrityError, Ledger, canonical, digest
from fraud_reports.server import ReportServer


def analysis_result(label="fraude"):
    return {"label": label, "score": None, "explanation": "Resultado de prueba",
            "model_name": "test-only", "model_version": "1", "model_digest": "a" * 64,
            "prompt_version": "1"}


class StartupTests(unittest.TestCase):
    def test_module_and_direct_script_entrypoints(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as other_directory:
            for arguments, directory in ((["-m", "fraud_reports.server"], root),
                                         ([str(root / "fraud_reports" / "server.py")], other_directory)):
                with self.subTest(arguments=arguments):
                    result = subprocess.run([sys.executable, *arguments, "--help"], cwd=directory,
                                            capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                    self.assertIn(b"--db", result.stdout)
                    self.assertNotIn(b"--verify-checkpoint", result.stdout)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "reports.sqlite3"
        self.ledger = Ledger(self.path)

    def create(self, title="Prueba", analysis=None):
        return self.ledger.create_report(title, "Texto original: áéñ\n<script>alert(1)</script>",
                                         "sms", reported_by="Sebastián", analysis=analysis)

    def test_one_record_hash_covers_all_fields(self):
        before = datetime.now(timezone.utc)
        report = self.create(analysis=analysis_result())
        after = datetime.now(timezone.utc)
        payload = {key: value for key, value in report.items() if key != "hash"}
        self.assertEqual(report["hash"], digest(payload))
        self.assertLessEqual(before, datetime.fromisoformat(report["created_at"]))
        self.assertLessEqual(datetime.fromisoformat(report["created_at"]), after)
        self.assertEqual(report["reported_by"], "Sebastián")
        for field, altered in (("reported_by", "Otro"), ("created_at", "2000-01-01"),
                               ("fraud_positive", False), ("analysis", None), ("text", "Otro texto")):
            self.assertNotEqual(report["hash"], digest({**payload, field: altered}))
        with self.ledger.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='events'").fetchone())
        self.assertNotIn("events", report)
        self.assertNotIn("previous_hash", report)

    def test_reads_and_reopening_never_add_records_or_change_hash(self):
        report = self.create()
        reopened = Ledger(self.path)
        for _ in range(3):
            self.assertEqual(reopened.snapshot(), {"reports": [report]})
            self.assertEqual(reopened.get_report(report["id"]), report)
            self.assertEqual(reopened.export(report["id"]), {"format": "fraud-report-v2", "report": report})
        with self.ledger.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 1)

    def test_all_verdicts_are_saved_at_creation(self):
        for label, expected in (("fraude", True), ("no_fraude", False), ("incierto", None)):
            report = self.create(analysis=analysis_result(label))
            self.assertIs(report["fraud_positive"], expected)
            self.assertEqual(report["analysis"]["label"], label)
        report = self.create()
        self.assertIsNone(report["fraud_positive"])
        self.assertIsNone(report["analysis"])
        self.assertEqual(len(self.ledger.snapshot()["reports"]), 4)

    def test_reporter_and_content_are_validated(self):
        for reporter in (None, "", "   ", 42, "a" * 121):
            with self.subTest(reporter=reporter), self.assertRaises(ValueError):
                self.ledger.create_report("Prueba", "Texto", "sms", reported_by=reporter)
        for values in (("", "Texto", "sms"), ("Título", "   ", "sms"), ("Título", "Texto", "web")):
            with self.assertRaises(ValueError):
                self.ledger.create_report(*values, reported_by="Ana")
        self.assertEqual(self.ledger.snapshot()["reports"], [])

    def test_invalid_model_result_creates_no_record(self):
        for score in (float("nan"), float("inf"), True, -1, 2, "0.5"):
            with self.assertRaises(ValueError):
                self.create(analysis={**analysis_result(), "score": score})
        for result in ({}, {**analysis_result(), "label": "invalid"},
                       {**analysis_result(), "model_digest": "invalid"}):
            with self.assertRaises(ValueError):
                self.create(analysis=result)
        self.assertEqual(self.ledger.snapshot()["reports"], [])

    def test_update_delete_and_replace_blocked(self):
        report = self.create()
        with self.ledger.connection() as db:
            for statement in ("UPDATE reports SET hash='bad'", "DELETE FROM reports",
                              "INSERT OR REPLACE INTO reports SELECT * FROM reports"):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
        self.assertEqual(self.ledger.get_report(report["id"]), report)

    def test_modified_record_is_rejected(self):
        report = self.create()
        with self.ledger.connection() as db:
            db.execute("DROP TRIGGER no_report_update")
            raw = json.loads(db.execute("SELECT report_json FROM reports").fetchone()[0])
            raw["reported_by"] = "Usuario alterado"
            db.execute("UPDATE reports SET report_json=?", (canonical(raw),))
        with self.assertRaises(IntegrityError):
            self.ledger.snapshot()
        with self.assertRaises(IntegrityError):
            self.ledger.export(report["id"])

    def test_concurrent_reports_are_independent(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            created = list(pool.map(lambda i: self.create(f"Reporte {i}"), range(24)))
        self.assertEqual(len(self.ledger.snapshot()["reports"]), 24)
        self.assertEqual(len({r["id"] for r in created}), 24)
        self.assertEqual(len({r["hash"] for r in created}), 24)

    def test_old_database_is_preserved_and_rejected(self):
        old_path = Path(self.temp.name) / "old.sqlite3"
        with closing(sqlite3.connect(old_path)) as db, db:
            db.execute("CREATE TABLE events (seq INTEGER, event_json TEXT, hash TEXT)")
            db.execute("INSERT INTO events VALUES (1, 'legacy', 'saved-hash')")
        original = old_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "formato anterior"):
            Ledger(old_path)
        self.assertEqual(old_path.read_bytes(), original)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "reports.sqlite3")
        self.server = ReportServer(("127.0.0.1", 0), self.ledger)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)
        self.payload = {"title": "SMS de prueba", "text": "Mensaje", "source": "sms", "reported_by": "Ana"}

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
            self.assertEqual(report["reported_by"], "Ana")
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
                self.assertEqual(len(self.ledger.snapshot()["reports"]), detector.calls - 1)
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
        self.assertEqual(len(self.ledger.snapshot()["reports"]), 3)

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
        self.assertEqual(self.ledger.snapshot()["reports"], [])

    def test_removed_features_are_unavailable(self):
        _, report = self.request("POST", "/api/reports", self.payload)
        for method, path in (("GET", "/api/checkpoint"), ("POST", "/api/verify-checkpoint"),
                             ("POST", f"/api/reports/{report['id']}/status"),
                             ("POST", f"/api/reports/{report['id']}/analyze")):
            self.assertEqual(self.request(method, path, {} if method == "POST" else None)[0], 404)
        for method in ("PUT", "PATCH", "DELETE"):
            self.assertEqual(self.request(method, f"/api/reports/{report['id']}", {})[0], 501)
        self.assertEqual(len(self.ledger.snapshot()["reports"]), 1)

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
        self.assertEqual(self.ledger.snapshot()["reports"], [])
        self.assertEqual(self.request("GET", "/api/reports/missing/export")[0], 404)
        self.assertEqual(self.request("GET", "/../README.md")[0], 404)

    def test_cross_origin_and_host_and_token_rejected(self):
        for headers in ({"Origin": "https://attacker.example"}, {"Host": "attacker.example"},
                        {"X-CSRF-Token": "wrong"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("POST", "/api/reports", self.payload, headers)[0], 403)
        self.assertEqual(self.ledger.snapshot()["reports"], [])

    def test_damaged_record_is_not_returned(self):
        _, report = self.request("POST", "/api/reports", self.payload)
        with self.ledger.connection() as db:
            db.execute("DROP TRIGGER no_report_update")
            db.execute("UPDATE reports SET hash='bad'")
        self.assertEqual(self.request("GET", "/api/reports")[0], 409)
        self.assertEqual(self.request("GET", f"/api/reports/{report['id']}/export")[0], 409)


if __name__ == "__main__":
    unittest.main()
