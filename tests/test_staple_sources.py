"""Parser tests for staple_sources, using real captured pages as fixtures."""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import staple_sources  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8", errors="replace") as f:
        return f.read()


class FakeResponse:
    def __init__(self, text="", payload=None):
        self.text = text
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    """Serves canned responses; records requests it saw."""

    def __init__(self, get_response=None, post_responses=None):
        self._get = get_response
        self._posts = list(post_responses or [])
        self.post_data = []

    def get(self, url, **kw):
        return self._get

    def post(self, url, data=None, **kw):
        self.post_data.append(data)
        return self._posts.pop(0) if self._posts else FakeResponse(text="")


class MetagameIdTest(unittest.TestCase):
    def setUp(self):
        staple_sources._meta_cache.update(ids=None, at=0.0)

    def test_parses_all_format_codes(self):
        session = FakeSession(get_response=FakeResponse(text=fixture("mtgtop8_form.html")))
        ids = staple_sources._metagame_ids(session)
        for code in staple_sources.MTGTOP8_CODES.values():
            self.assertIn(code, ids, f"missing metagame id for {code}")

    def test_prefers_last_2_months_window(self):
        session = FakeSession(get_response=FakeResponse(text=fixture("mtgtop8_form.html")))
        ids = staple_sources._metagame_ids(session)
        # Values observed in the captured form: Modern "Last 2 Months" is 51,
        # Legacy 39, Standard 52 — the parser must pick those, not the first
        # option ("Last 2 Weeks").
        self.assertEqual(ids["MO"], "51")
        self.assertEqual(ids["LE"], "39")
        self.assertEqual(ids["ST"], "52")

    def test_caches_result(self):
        session = FakeSession(get_response=FakeResponse(text=fixture("mtgtop8_form.html")))
        first = staple_sources._metagame_ids(session)
        # Second call with a broken session must serve from cache.
        second = staple_sources._metagame_ids(FakeSession(get_response=FakeResponse(text="")))
        self.assertEqual(first, second)


class Mtgtop8Test(unittest.TestCase):
    def setUp(self):
        staple_sources._meta_cache.update(ids=None, at=0.0)
        self._delay = staple_sources.POLITE_DELAY
        staple_sources.POLITE_DELAY = 0

    def tearDown(self):
        staple_sources.POLITE_DELAY = self._delay

    def _session(self, pages):
        return FakeSession(
            get_response=FakeResponse(text=fixture("mtgtop8_form.html")),
            post_responses=[FakeResponse(text=p) for p in pages])

    def test_parses_card_names_from_results(self):
        results = fixture("mtgtop8_results.html")
        session = self._session([results, "", ""])
        names = staple_sources.fetch_mtgtop8(session, "modern")
        self.assertEqual(len(names), 20)  # 20 cards per page in the fixture
        self.assertIn("Flooded Strand", names)
        self.assertIn("Arid Mesa", names)
        # Percentages/averages from other columns must not leak in.
        for n in names:
            self.assertNotRegex(n, r"%|\d+\.\d")

    def test_deduplicates_across_pages(self):
        results = fixture("mtgtop8_results.html")
        session = self._session([results, results, results])
        names = staple_sources.fetch_mtgtop8(session, "modern")
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), 20)

    def test_paginates_with_current_page_field(self):
        results = fixture("mtgtop8_results.html")
        session = self._session([results, results, results])
        staple_sources.fetch_mtgtop8(session, "modern")
        pages = [d["current_page"] for d in session.post_data]
        self.assertEqual(pages, ["1", "2", "3"])

    def test_stops_on_empty_page(self):
        results = fixture("mtgtop8_results.html")
        session = self._session([results, ""])
        staple_sources.fetch_mtgtop8(session, "modern")
        self.assertEqual(len(session.post_data), 2)


class EdhrecTest(unittest.TestCase):
    def test_parses_names_in_order(self):
        payload = json.loads(fixture("edhrec_top.json"))
        session = FakeSession(get_response=FakeResponse(payload=payload))
        names = staple_sources.fetch_edhrec(session)
        self.assertEqual(names[0], "Sol Ring")
        self.assertEqual(len(names), 25)
        self.assertEqual(len(names), len(set(names)))

    def test_respects_limit(self):
        payload = json.loads(fixture("edhrec_top.json"))
        session = FakeSession(get_response=FakeResponse(payload=payload))
        self.assertEqual(len(staple_sources.fetch_edhrec(session, limit=5)), 5)


class FetchForTest(unittest.TestCase):
    def test_unknown_format_raises(self):
        with self.assertRaises(KeyError):
            staple_sources.fetch_for(FakeSession(), "pauper")

    def test_commander_uses_edhrec(self):
        payload = json.loads(fixture("edhrec_top.json"))
        session = FakeSession(get_response=FakeResponse(payload=payload))
        names, source = staple_sources.fetch_for(session, "commander")
        self.assertEqual(source, "edhrec.com")
        self.assertIn("Sol Ring", names)


if __name__ == "__main__":
    unittest.main()
