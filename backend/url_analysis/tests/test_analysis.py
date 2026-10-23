import math
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from backend.url_analysis.features import FEATURE_NAMES, extract_features
from backend.url_analysis.preprocessing import (
    LABEL_LEGITIMATE,
    LABEL_PHISHING,
    SourceSpec,
    UrlRecord,
    merge_sources,
    normalize_url,
    split_records,
)
from backend.url_analysis.layered import analyze
from backend.url_analysis.safe_browsing import SafeBrowsingClient


class FakeResponse:
    def __init__(self, payload):
        self._body = BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self._body.read()


class NormalizeUrlTests(unittest.TestCase):
    def test_normalizes_host_scheme_and_default_port(self):
        self.assertEqual(
            normalize_url(" HTTPS://Example.COM:443/login?q=1#step "),
            "https://example.com/login?q=1#step",
        )

    def test_accepts_domain_without_scheme(self):
        self.assertEqual(normalize_url("example.com"), "http://example.com/")

    def test_rejects_non_http_or_malformed_values(self):
        self.assertIsNone(normalize_url("javascript:alert(1)"))
        self.assertIsNone(normalize_url("not-a-domain"))
        self.assertIsNone(normalize_url("https://example.com/a b"))


class FeatureTests(unittest.TestCase):
    def test_features_are_stable_and_finite(self):
        url = "https://secure-login.example.com/account?id=123"
        first = extract_features(url)
        second = extract_features(url)
        self.assertEqual(first, second)
        self.assertEqual(len(first), len(FEATURE_NAMES))
        self.assertTrue(all(math.isfinite(value) for value in first))

    def test_www_does_not_change_learned_representation(self):
        self.assertEqual(
            extract_features("https://google.com/"),
            extract_features("https://www.google.com/"),
        )


class SplitTests(unittest.TestCase):
    def test_splits_are_disjoint_and_keep_all_rows(self):
        records = [
            UrlRecord(f"https://example{i}.com/", i % 2, "test") for i in range(100)
        ]
        splits = split_records(records, seed=7)
        urls = [{row.url for row in split} for split in splits.values()]
        self.assertFalse(urls[0] & urls[1])
        self.assertFalse(urls[0] & urls[2])
        self.assertFalse(urls[1] & urls[2])
        self.assertEqual(sum(map(len, urls)), len(records))

    def test_same_registrable_domain_stays_in_one_split(self):
        records = [
            UrlRecord("https://example.co.uk/a", 0, "test"),
            UrlRecord("https://www.example.co.uk/b", 0, "test"),
            UrlRecord("https://login.example.co.uk/c", 1, "test"),
        ] + [
            UrlRecord(f"https://domain{i}.com/", i % 2, "test") for i in range(30)
        ]
        splits = split_records(records, seed=9)
        locations = {
            name
            for name, rows in splits.items()
            if any("example.co.uk" in row.url for row in rows)
        }
        self.assertEqual(len(locations), 1)


class MergeTests(unittest.TestCase):
    def test_maps_labels_deduplicates_and_removes_conflicts(self):
        specs = (
            SourceSpec(
                "source_a.csv",
                "URL",
                "label",
                {"1": LABEL_LEGITIMATE, "0": LABEL_PHISHING},
            ),
            SourceSpec(
                "source_b.csv",
                "url",
                "type",
                {"legitimate": LABEL_LEGITIMATE, "phishing": LABEL_PHISHING},
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source_a.csv").write_text(
                "URL,label\nhttps://safe.example,1\nhttps://conflict.example,0\n",
                encoding="utf-8",
            )
            (root / "source_b.csv").write_text(
                "url,type\nhttps://safe.example/,legitimate\n"
                "https://bad.example,phishing\n"
                "https://conflict.example,legitimate\n",
                encoding="utf-8",
            )
            records, conflicts, report = merge_sources(root, specs=specs)

        self.assertEqual(
            {(row.url, row.label) for row in records},
            {
                ("https://safe.example/", LABEL_LEGITIMATE),
                ("https://bad.example/", LABEL_PHISHING),
            },
        )
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(report["duplicates_removed"], 2)
        self.assertEqual(report["conflicting_urls_removed"], 1)

    def test_accepts_an_official_source_with_a_fixed_legitimate_label(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "official.csv").write_text(
                "website,name\nwww.bank.example,Bank\nuser@bank.example,Email\n"
                "Pendiente,Missing\n",
                encoding="utf-8",
            )
            spec = SourceSpec(
                "official.csv",
                "website",
                None,
                {},
                fixed_label=LABEL_LEGITIMATE,
                allow_userinfo=False,
            )
            records, _, report = merge_sources(root, specs=(spec,))

        self.assertEqual(
            [(row.url, row.label) for row in records],
            [("http://www.bank.example/", LABEL_LEGITIMATE)],
        )
        self.assertEqual(report["discarded_invalid_rows"]["official.csv"], 2)


class SafeBrowsingTests(unittest.TestCase):
    def test_v5_lookup_parses_a_known_threat_without_leaking_configuration(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["api_key"] = request.get_header("X-goog-api-key")
            captured["timeout"] = timeout
            return FakeResponse(
                {
                    "threats": [
                        {
                            "url": "http://bad.example/",
                            "threatTypes": ["SOCIAL_ENGINEERING"],
                        }
                    ],
                    "cacheDuration": "300s",
                }
            )

        result = SafeBrowsingClient("secret", timeout=3, opener=opener).check(
            "http://bad.example/"
        )
        query = parse_qs(urlsplit(captured["url"]).query)
        self.assertEqual(query["urls"], ["http://bad.example/"])
        self.assertNotIn("key", query)
        self.assertEqual(captured["api_key"], "secret")
        self.assertEqual(captured["timeout"], 3)
        self.assertTrue(result.is_threat)
        self.assertEqual(result.threats[0]["threat_types"], ["SOCIAL_ENGINEERING"])

    def test_v5_lookup_reuses_a_response_only_during_its_cache_duration(self):
        calls = []
        now = [100.0]

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeResponse({"threats": [], "cacheDuration": "60s"})

        client = SafeBrowsingClient(
            "secret", opener=opener, clock=lambda: now[0]
        )
        client.check("https://example.com/")
        client.check("https://example.com/")
        self.assertEqual(len(calls), 1)
        now[0] = 161.0
        client.check("https://example.com/")
        self.assertEqual(len(calls), 2)

    def test_layered_analysis_gives_precedence_to_a_known_threat(self):
        client = SafeBrowsingClient(
            "secret",
            opener=lambda request, timeout: FakeResponse(
                {
                    "threats": [
                        {"url": "https://bad.example/", "threatTypes": ["MALWARE"]}
                    ]
                }
            ),
        )

        def local_predictor(url, model_dir):
            return {
                "url": url,
                "normalized_url": url,
                "classification": "likely_legitimate",
                "risk_level": "low",
                "is_suspicious": False,
            }

        result = analyze(
            "https://bad.example",
            Path("unused"),
            safe_browsing_client=client,
            predictor=local_predictor,
        )
        self.assertEqual(result["classification"], "suspicious")
        self.assertEqual(result["model_classification"], "likely_legitimate")
        self.assertEqual(result["detection_source"], "google_safe_browsing")

    def test_official_base_is_low_risk_only_after_a_clean_remote_lookup(self):
        client = SafeBrowsingClient(
            "secret", opener=lambda request, timeout: FakeResponse({"threats": []})
        )
        result = analyze(
            "https://bank.example",
            Path("unused"),
            safe_browsing_client=client,
            predictor=lambda url, model_dir: {
                "classification": "suspicious",
                "risk_level": "high",
                "is_suspicious": True,
                "superfinanciera_host_match": True,
                "superfinanciera_base_url_match": True,
            },
        )
        self.assertEqual(result["classification"], "official_base_url")
        self.assertEqual(result["risk_level"], "low")

    def test_official_internal_path_still_requires_review_when_model_flags_it(self):
        client = SafeBrowsingClient(
            "secret", opener=lambda request, timeout: FakeResponse({"threats": []})
        )
        result = analyze(
            "https://bank.example/login",
            Path("unused"),
            safe_browsing_client=client,
            predictor=lambda url, model_dir: {
                "classification": "suspicious",
                "risk_level": "high",
                "is_suspicious": True,
                "superfinanciera_host_match": True,
                "superfinanciera_base_url_match": False,
            },
        )
        self.assertEqual(result["classification"], "uncertain")
        self.assertEqual(result["risk_level"], "review")

    def test_layered_analysis_reports_when_api_is_not_configured(self):
        result = analyze(
            "https://example.com",
            Path("unused"),
            predictor=lambda url, model_dir: {
                "classification": "likely_legitimate",
                "risk_level": "low",
                "is_suspicious": False,
            },
        )
        self.assertEqual(result["safe_browsing"]["status"], "not_configured")
        self.assertEqual(result["detection_source"], "local_model")

    def test_layered_analysis_reports_an_explicitly_skipped_lookup(self):
        result = analyze(
            "https://example.com",
            Path("unused"),
            safe_browsing_enabled=False,
            predictor=lambda url, model_dir: {
                "classification": "likely_legitimate",
                "risk_level": "low",
                "is_suspicious": False,
            },
        )
        self.assertEqual(result["safe_browsing"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
