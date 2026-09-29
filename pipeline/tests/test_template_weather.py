"""Tests for pipeline/weather_hook.py and pipeline/template.py."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import template as t
import weather_hook as wh

WK = ["2026-09-18", "2026-09-19", "2026-09-20"]


def event(name="Test Event", date="2026-09-18", start_time="7:00 PM",
          price="$20", **kw):
    e = {"name": name, "date": date, "venue": "Test Venue",
         "address_or_area": "Denver, CO", "start_time": start_time,
         "url": "https://example.com/event", "price": price,
         "description": "A test event.", "tags": ["family-friendly"],
         "ava_friendly": False, "drive_time_from_englewood": "~25 min",
         "recurring": False, "sources": []}
    e.update(kw)
    return e


class TestWeatherHook(unittest.TestCase):
    def test_rain_days_detected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as f:
            json.dump({
                "2026-09-18": {"precip_prob": 65, "high_f": 82,
                               "summary": "Rain"},
                "2026-09-19": {"precip_prob": 20, "high_f": 84,
                               "summary": "Sunny"},
                "2026-09-20": {"precip_prob": 66, "high_f": 79,
                               "summary": "Overcast"},
            }, f)
            path = f.name
        w = wh.summarize(path, WK)
        self.assertEqual(w["status"], "ok")
        self.assertEqual(w["rain_days"], ["2026-09-18", "2026-09-20"])
        self.assertIn("65%", w["note"])
        Path(path).unlink()

    def test_no_file_is_not_provided(self):
        w = wh.summarize(None, WK)
        self.assertEqual(w["status"], "not_provided")
        self.assertEqual(w["rain_days"], [])

    def test_unreadable_file(self):
        w = wh.summarize("/tmp/does-not-exist-xyz.json", WK)
        self.assertEqual(w["status"], "unreadable")


class TestTemplate(unittest.TestCase):
    def test_subject_format(self):
        self.assertEqual(
            t.subject_for(WK),
            "Kiwi's Weekend Guide \u2014 Friday, September 18 "
            "\u2013 Sunday, September 20")

    def test_day_label(self):
        self.assertEqual(t.day_label("2026-09-19"), "Saturday, September 19")

    def test_chronological_per_day_order(self):
        evs = [event("Late", start_time="9:00 PM"),
               event("Morning", start_time="9:00 AM"),
               event("NoTime", start_time=None),
               event("Mid", start_time="1:00 PM")]
        by_day, _ = t.group_events(evs)
        self.assertEqual([e["name"] for e in by_day["2026-09-18"]],
                         ["Morning", "Mid", "Late", "NoTime"])

    def test_multi_day_consolidation(self):
        evs = [event("Fest", date="2026-09-18"),
               event("Fest", date="2026-09-19"),
               event("Solo", date="2026-09-19")]
        by_day, multi = t.group_events(evs)
        self.assertEqual(list(multi.keys()), ["Fest"])
        self.assertNotIn("Fest", [e["name"]
                                  for v in by_day.values() for e in v])
        doc = t.render_newsletter(evs, WK, "<p>Header</p>")
        self.assertIn("All Weekend", doc)

    def test_missing_price_is_hard_fail(self):
        with self.assertRaises(ValueError):
            t.render_newsletter([event("Priceless", price="")], WK,
                                "<p>Header</p>")
        with self.assertRaises(ValueError):
            t.render_newsletter([event("Priceless", price=None)], WK,
                                "<p>Header</p>")

    def test_blank_header_rejected(self):
        with self.assertRaises(ValueError):
            t.render_newsletter([event()], WK, "   ")

    def test_footer_branding(self):
        import html as _html
        doc = t.render_newsletter([event()], WK, "<p>Header</p>")
        plain = _html.unescape(doc)
        self.assertIn(t.FOOTER_BRAND, plain)
        self.assertNotIn(t.LEGACY_BUG_BRAND, plain)

    def test_conflict_annotation_rendered(self):
        e = event(conflict=True,
                  conflict_with="Overlaps your 12:00 PM-12:45 PM: Crew call")
        doc = t.render_newsletter([e], WK, "<p>Header</p>")
        self.assertIn("Overlaps your", doc)

    def test_context_notes_rendered_on_first_event_of_day(self):
        e = event()
        doc = t.render_newsletter([e], WK, "<p>Header</p>",
                                  day_context_notes={
                                      "2026-09-18": ["Nanny's off"]})
        self.assertIn("Nanny&#x27;s off", doc)

    def test_free_price_styled(self):
        doc = t.render_newsletter([event(price="Free")], WK, "<p>Header</p>")
        self.assertIn(">FREE</span>", doc)


if __name__ == "__main__":
    unittest.main()
