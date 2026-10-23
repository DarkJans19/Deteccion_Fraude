import json
import tempfile
import unittest
from pathlib import Path

from backend.payment_receipts.redirects import (
    RedirectResolution,
    RedirectResolutionError,
    resolve_redirects,
)
from backend.payment_receipts.service import analyze_qr_url


def fake_analysis(url, model_dir, safe_browsing_client=None):
    official = "bancolombia.com.co" in url
    return {
        "normalized_url": url,
        "classification": "likely_legitimate",
        "risk_level": "low",
        "is_suspicious": False,
        "phishing_probability": 0.02,
        "superfinanciera_host_match": official,
        "superfinanciera_base_url_match": official,
        "safe_browsing": {"status": "checked", "verdict": "no_match", "is_threat": False},
    }


class QRAnalysisTests(unittest.TestCase):
    def test_rejects_qr_content_that_is_not_an_http_url(self):
        with self.assertRaisesRegex(ValueError, "HTTP"):
            analyze_qr_url("WIFI:T:WPA;S:Red;P:secreto;;", Path("unused"), analyzer=fake_analysis)

    def test_analyzes_both_urls_and_requires_review_when_domain_changes(self):
        calls = []

        def analyzer(url, model_dir, safe_browsing_client=None):
            calls.append(url)
            return fake_analysis(url, model_dir, safe_browsing_client)

        def resolver(url):
            return RedirectResolution(
                status="resolved",
                final_url="https://bancolombia.com.co/",
                chain=(
                    {"url": url, "status_code": 302},
                    {"url": "https://bancolombia.com.co/", "status_code": 200},
                ),
                domain_changed=True,
            )

        result = analyze_qr_url(
            "https://short.example/abc",
            Path("unused"),
            analyzer=analyzer,
            redirect_resolver=resolver,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["destination"]["hostname"], "bancolombia.com.co")
        self.assertEqual(result["summary"]["risk_level"], "review")
        self.assertTrue(result["destination_analysis"]["superfinanciera_host_match"])

    def test_high_risk_destination_takes_precedence(self):
        def analyzer(url, model_dir, safe_browsing_client=None):
            result = fake_analysis(url, model_dir, safe_browsing_client)
            if "bad.example" in url:
                result.update(risk_level="high", classification="suspicious", is_suspicious=True)
            return result

        result = analyze_qr_url(
            "https://bad.example/",
            Path("unused"),
            analyzer=analyzer,
            redirect_resolver=lambda url: RedirectResolution(
                "resolved", url, ({"url": url, "status_code": 200},), False
            ),
        )
        self.assertEqual(result["summary"]["risk_level"], "high")

    def test_unconfirmed_destination_never_reports_low_risk(self):
        result = analyze_qr_url(
            "https://example.com/",
            Path("unused"),
            analyzer=fake_analysis,
            redirect_resolver=lambda url: RedirectResolution(
                "unavailable", url, (), False, "timeout"
            ),
        )
        self.assertEqual(result["summary"]["risk_level"], "review")

    def test_lookalike_of_an_official_domain_is_high_risk(self):
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory)
            (model_dir / "trusted_reference.json").write_text(
                json.dumps({"hosts": ["bancolombia.com.co"]}),
                encoding="utf-8",
            )
            result = analyze_qr_url(
                "https://bancol0mbia.com.co/",
                model_dir,
                analyzer=fake_analysis,
                redirect_resolver=lambda url: RedirectResolution(
                    "resolved", url, ({"url": url, "status_code": 200},), False
                ),
            )
        self.assertEqual(result["authenticity"]["status"], "suspected_impersonation")
        self.assertEqual(
            result["authenticity"]["suspected_official_host"],
            "bancolombia.com.co",
        )
        self.assertEqual(result["summary"]["risk_level"], "high")


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeRedirectResponse:
    def __init__(self, code, location=None):
        self._code = code
        self.headers = FakeHeaders()
        if location:
            self.headers["Location"] = location

    def getcode(self):
        return self._code

    def close(self):
        pass


class FakeRedirectOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request.full_url, request.method, timeout))
        return next(self.responses)


class RedirectResolutionTests(unittest.TestCase):
    def test_follows_locations_and_detects_a_domain_change(self):
        opener = FakeRedirectOpener(
            [
                FakeRedirectResponse(302, "https://destination.test/login"),
                FakeRedirectResponse(200),
            ]
        )
        result = resolve_redirects(
            "https://short.example/a",
            opener=opener,
            destination_validator=lambda url: None,
        )
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.final_url, "https://destination.test/login")
        self.assertTrue(result.domain_changed)
        self.assertEqual(len(result.chain), 2)

    def test_stops_before_requesting_a_blocked_destination(self):
        checked = []

        def validator(url):
            checked.append(url)
            if "127.0.0.1" in url:
                raise RedirectResolutionError("blocked")

        opener = FakeRedirectOpener(
            [FakeRedirectResponse(302, "http://127.0.0.1/admin")]
        )
        result = resolve_redirects(
            "https://safe.example/",
            opener=opener,
            destination_validator=validator,
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(len(opener.requests), 1)


if __name__ == "__main__":
    unittest.main()

