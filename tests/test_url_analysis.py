import math
import tempfile
import unittest
from pathlib import Path

from url_analysis.features import FEATURE_NAMES, extract_features
from url_analysis.preprocessing import (
    LABEL_LEGITIMATE,
    LABEL_PHISHING,
    SourceSpec,
    UrlRecord,
    merge_sources,
    normalize_url,
    split_records,
)


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


if __name__ == "__main__":
    unittest.main()
