"""Calendar-tier integration tests -- property (d).

Pins the 2026-09-14 3-tier rule end-to-end through run.py's enrich/render
stages (not just classify() in isolation):

  - all-day events (even ones "overlapping" event hours, e.g. the
    "49ers game" all-day placeholder) -> informational, and they must NOT
    suppress any day's events or flag any guide event;
  - timed family-context events ("Nanny's off — Ava day",
    "Candice visiting") -> context_notes (never flags);
  - other timed events -> hard_conflicts (only tier that can flag, and
    only when the windows genuinely overlap);
  - overlaps() boundary: cal end == guide start is NOT an overlap.

No real calendar reads: fixtures are written to a --calendar-json file.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

PIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPE))
import calendar_context as cc  # noqa: E402

spec = importlib.util.spec_from_file_location("kiwi_run_cal", PIPE / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

WEEKEND = ["2026-09-18", "2026-09-19", "2026-09-20"]

CAL = [
    # All-day placeholder whose "hours" cover the whole day -- the case the
    # old single-flag rule would have used to nuke Saturday's events.
    {"title": "49ers game", "start": "2026-09-19", "end": "2026-09-20",
     "all_day": True, "description": "all-day TBD placeholder"},
    {"title": "Nanny's off \u2014 Ava day", "start": "2026-09-18T08:00",
     "end": "2026-09-18T17:00", "all_day": False, "description": ""},
    {"title": "Candice visiting", "start": "2026-09-19T10:00",
     "end": "2026-09-19T12:00", "all_day": False, "description": ""},
    {"title": "Dentist appointment", "start": "2026-09-18T12:00",
     "end": "2026-09-18T12:45", "all_day": False, "description": ""},
]

GUIDE = [
    {"name": "Lunch show", "date": "2026-09-18", "start_time": "12:15 PM",
     "price": "$10", "venue": "V", "url": "https://example.com/1",
     "description": "overlaps the dentist"},
    {"name": "Boundary show", "date": "2026-09-18", "start_time": "12:45 PM",
     "price": "$10", "venue": "V", "url": "https://example.com/2",
     "description": "starts exactly when the dentist ends"},
    {"name": "Evening show", "date": "2026-09-18", "start_time": "6:00 PM",
     "price": "$10", "venue": "V", "url": "https://example.com/3",
     "description": "no overlap"},
    {"name": "Saturday matinee", "date": "2026-09-19", "start_time": "2:00 PM",
     "price": "$10", "venue": "V", "url": "https://example.com/4",
     "description": "49ers placeholder day -- must not be suppressed"},
    {"name": "Brunch concert", "date": "2026-09-19", "start_time": "11:00 AM",
     "price": "$10", "venue": "V", "url": "https://example.com/5",
     "description": "overlaps Candice visiting (context tier) -- no flag"},
]


class TestTierClassification(unittest.TestCase):
    def test_all_day_49ers_placeholder_is_informational_only(self):
        t = cc.classify([CAL[0]], WEEKEND)
        self.assertEqual([e["title"] for e in t["informational"]],
                         ["49ers game"])
        self.assertEqual(t["hard_conflicts"], [])
        self.assertEqual(t["context_notes"], [])

    def test_family_timed_events_are_context_notes(self):
        t = cc.classify(CAL[1:3], WEEKEND)
        self.assertEqual(
            sorted(e["title"] for e in t["context_notes"]),
            ["Candice visiting", "Nanny's off \u2014 Ava day"])
        self.assertEqual(t["hard_conflicts"], [])
        self.assertEqual(t["informational"], [])

    def test_other_timed_events_are_hard_conflicts(self):
        t = cc.classify([CAL[3]], WEEKEND)
        self.assertEqual([e["title"] for e in t["hard_conflicts"]],
                         ["Dentist appointment"])
        self.assertEqual(t["context_notes"], [])
        self.assertEqual(t["informational"], [])


class TestOverlapBoundary(unittest.TestCase):
    def test_end_equals_start_is_not_overlap(self):
        cal = {"title": "Dentist", "start": "2026-09-18T12:00",
               "end": "2026-09-18T12:45"}
        self.assertFalse(cc.overlaps(
            cal, {"date": "2026-09-18", "start_time": "12:45 PM"}))

    def test_genuine_overlap_is_flagged(self):
        cal = {"title": "Dentist", "start": "2026-09-18T12:00",
               "end": "2026-09-18T12:45"}
        self.assertTrue(cc.overlaps(
            cal, {"date": "2026-09-18", "start_time": "12:15 PM"}))


class TestEnrichIntegration(unittest.TestCase):
    """Through run.py's enrich stage: only genuine hard-conflict overlaps
    get annotated; nothing is suppressed."""

    def _enrich(self):
        with tempfile.TemporaryDirectory() as td:
            cal_path = Path(td) / "calendar.json"
            cal_path.write_text(json.dumps({"events": CAL}))
            events = [dict(g) for g in GUIDE]
            out = []
            day_notes, weather, ctx = run.stage_enrich(
                events, WEEKEND, str(cal_path), None, out)
        return events, day_notes, weather, ctx

    def test_only_genuine_hard_overlap_flagged(self):
        events, _, _, _ = self._enrich()
        by_name = {e["name"]: e for e in events}
        self.assertTrue(by_name["Lunch show"].get("conflict"),
                        "12:15 PM genuinely overlaps the 12:00-12:45 dentist")
        self.assertIn("Dentist appointment",
                      by_name["Lunch show"].get("conflict_with", ""))
        for name in ("Boundary show", "Evening show", "Saturday matinee",
                     "Brunch concert"):
            self.assertFalse(by_name[name].get("conflict"),
                             f"{name} must not be flagged")

    def test_boundary_touch_does_not_flag(self):
        events, _, _, _ = self._enrich()
        by_name = {e["name"]: e for e in events}
        self.assertNotIn("conflict", by_name["Boundary show"],
                         "cal end == guide start is not an overlap")

    def test_all_day_placeholder_never_flags_or_suppresses(self):
        events, _, _, ctx = self._enrich()
        by_name = {e["name"]: e for e in events}
        self.assertNotIn("conflict", by_name["Saturday matinee"])
        self.assertEqual(len(events), len(GUIDE),
                         "enrich must never drop guide events")
        self.assertEqual([e["title"] for e in ctx["informational"]],
                         ["49ers game"])

    def test_context_tier_never_flags_even_on_real_overlap(self):
        # Brunch concert 11:00 AM + 120 min genuinely overlaps Candice
        # visiting 10:00-12:00, but context_notes must never flag events.
        self.assertTrue(cc.overlaps(
            CAL[2], {"date": "2026-09-19", "start_time": "11:00 AM"}))
        events, _, _, _ = self._enrich()
        by_name = {e["name"]: e for e in events}
        self.assertNotIn("conflict", by_name["Brunch concert"])

    def test_context_notes_reach_day_notes(self):
        _, day_notes, _, ctx = self._enrich()
        self.assertEqual(len(ctx["context_notes"]), 2)
        self.assertTrue(any("Ava day" in n for n in day_notes["2026-09-18"]))
        self.assertTrue(any("Candice" in n for n in day_notes["2026-09-19"]))


class TestRenderKeepsAllEvents(unittest.TestCase):
    """Through run.py's render stage: informational/context calendar
    events never suppress guide events."""

    def _render(self, events, day_notes):
        with tempfile.TemporaryDirectory() as td:
            header = Path(td) / "header.html"
            header.write_text("<p>Test header.</p>")
            html_doc, subject = run.stage_render(
                events, WEEKEND, Path(td), str(header), None, day_notes,
                {"note": "", "status": "not_provided", "rain_days": []}, [])
        return html_doc, subject

    def test_all_guide_events_rendered_with_no_conflict_annotations(self):
        events = [dict(g) for g in GUIDE]  # unflagged: clean calendar day
        html_doc, _ = self._render(events, {})
        for g in GUIDE:
            self.assertIn(g["name"], html_doc,
                          f"{g['name']} must survive render")
        self.assertNotIn("Overlaps your", html_doc)

    def test_flagged_event_gets_annotation_others_untouched(self):
        events = [dict(g) for g in GUIDE]
        events[0]["conflict"] = True
        events[0]["conflict_with"] = "Overlaps your 12:00 PM-12:45 PM: Dentist appointment"
        html_doc, _ = self._render(events, {})
        self.assertIn("Overlaps your", html_doc)
        self.assertIn("Dentist appointment", html_doc)
        for g in GUIDE:
            self.assertIn(g["name"], html_doc)


if __name__ == "__main__":
    unittest.main()
