"""Fallback-chain tests for build_staples: fresh scrape -> previous list ->
seed -> Scryfall popularity -> unavailable."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import build_staples  # noqa: E402
import staple_sources  # noqa: E402
from staples_seed import FORMATS, STAPLES  # noqa: E402

GOOD = [f"Card {i}" for i in range(30)]  # above MIN_ACCEPT


def run_build(out_path, fetch_for=None, scryfall=None):
    with mock.patch.object(staple_sources, "fetch_for",
                           side_effect=fetch_for or (lambda s, f: (GOOD, "test-source"))), \
         mock.patch.object(build_staples, "scryfall_popular",
                           side_effect=scryfall or (lambda s, f: [])):
        code = build_staples.main(out_path)
    with open(out_path) as f:
        return code, json.load(f)


class BuildStaplesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "staples.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_scrape_all_formats(self):
        code, data = run_build(self.out)
        self.assertEqual(code, 0)
        self.assertEqual([e["key"] for e in data["formats"]],
                         [k for k, _ in FORMATS])
        for e in data["formats"]:
            self.assertEqual(e["names"], GOOD)
            self.assertEqual(e["source"], "test-source")
            self.assertIsNotNone(e["updated_at"])
        self.assertIn("generated_at", data)

    def test_short_scrape_rejected_keeps_previous_list(self):
        run_build(self.out)  # good previous file
        few = ["Only", "Two"]
        code, data = run_build(self.out, fetch_for=lambda s, f: (few, "test-source"))
        self.assertEqual(code, 1)  # nothing freshly scraped
        for e in data["formats"]:
            self.assertEqual(e["names"], GOOD, f"{e['key']} lost its previous list")

    def test_failure_without_previous_uses_seed(self):
        def boom(s, f):
            raise RuntimeError("site down")
        code, data = run_build(self.out, fetch_for=boom)
        by_key = {e["key"]: e for e in data["formats"]}
        self.assertEqual(by_key["modern"]["names"], STAPLES["modern"])
        self.assertEqual(by_key["modern"]["source"], "built-in seed list")

    def test_standard_falls_back_to_scryfall(self):
        # Standard has an empty seed, so with no previous file and a dead
        # scrape it must reach the Scryfall popularity fallback.
        def boom(s, f):
            raise RuntimeError("site down")
        pop = [f"Popular {i}" for i in range(10)]
        code, data = run_build(self.out, fetch_for=boom, scryfall=lambda s, f: pop)
        std = next(e for e in data["formats"] if e["key"] == "standard")
        self.assertEqual(std["names"], pop)
        self.assertIn("scryfall", std["source"])

    def test_everything_dead_yields_empty_but_valid_entry(self):
        def boom(s, f):
            raise RuntimeError("site down")
        def boom2(s, f):
            raise RuntimeError("also down")
        code, data = run_build(self.out, fetch_for=boom, scryfall=boom2)
        self.assertEqual(code, 1)
        std = next(e for e in data["formats"] if e["key"] == "standard")
        self.assertEqual(std["names"], [])
        self.assertEqual(std["source"], "unavailable")
        # File must still be valid JSON with every format present (the
        # frontend disables empty tiles rather than crashing).
        self.assertEqual(len(data["formats"]), len(FORMATS))


class SeedListTest(unittest.TestCase):
    def test_seeds_exist_for_every_format_except_standard(self):
        for key, _ in FORMATS:
            if key == "standard":
                self.assertEqual(STAPLES[key], [])
            else:
                self.assertGreaterEqual(len(STAPLES[key]), 20, key)

    def test_no_duplicate_names_within_a_seed(self):
        for key, names in STAPLES.items():
            self.assertEqual(len(names), len(set(names)), key)


if __name__ == "__main__":
    unittest.main()
