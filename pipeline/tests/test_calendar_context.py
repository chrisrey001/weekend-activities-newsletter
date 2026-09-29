"""Tests for pipeline/calendar_context.py -- the deterministic 3-tier
calendar classification. Run from the pipeline dir:
    python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import calendar_context as cc

WK = {"friday": "2026-09-18", "saturday": "2026-09-19", "sunday": "2026-09-20"}


def ev(title, start, end=None, all_day=False, description=""):
    return {"title": title, "start": start, "end": end,
            "all_day": all_day, "description": description}


class TestClassify(unittest.TestCase):
    def test_all_day_is_informational_only(self):
        # All-day events MUST NOT invalidate the day or suppress events --
        # even when the title matches a family keyword (the 9/14 fix).
        e = ev("Anniversary", "2026-09-18", "2026-09-19", all_day=True,
               description="4th anniversary")
        t = cc.classify([e], WK)
        self.assertEqual([x["title"] for x in t["informational"]],
                         ["Anniversary"])
        self.assertEqual(t["hard_conflicts"], [])
        self.assertEqual(t["context_notes"], [])

    def test_family_keywords_go_to_context_notes(self):
        cases = [
            ("Nanny off", ""),
            ("Ava doctor visit", ""),
            ("Flight to Chicago", ""),
            ("Kristen's birthday dinner", ""),
            ("Potty training block", ""),
            ("Daycare tour", ""),
            ("School orientation", ""),
            ("Camp pickup", ""),
            ("Aunt Cathy visiting", ""),
            ("Travel day", ""),
        ]
        for title, desc in cases:
            e = ev(title, "2026-09-19T09:00", "2026-09-19T10:00",
                   description=desc)
            t = cc.classify([e], WK)
            self.assertEqual(len(t["context_notes"]), 1, title)
            self.assertEqual(t["hard_conflicts"], [], title)
            self.assertEqual(t["informational"], [], title)

    def test_keyword_match_is_case_insensitive_and_covers_description(self):
        e = ev("Dentist", "2026-09-18T10:00", "2026-09-18T11:00",
               description="AVA comes along")
        t = cc.classify([e], WK)
        self.assertEqual(len(t["context_notes"]), 1)

    def test_other_timed_events_are_hard_conflicts(self):
        e = ev("Catchup with the Crew", "2026-09-18T12:00",
               "2026-09-18T12:45")
        t = cc.classify([e], WK)
        self.assertEqual(len(t["hard_conflicts"]), 1)
        self.assertEqual(t["context_notes"], [])

    def test_events_outside_weekend_are_dropped(self):
        e = ev("Next Monday", "2026-09-21T09:00", "2026-09-21T10:00")
        t = cc.classify([e], WK)
        self.assertEqual(t, {"hard_conflicts": [], "context_notes": [],
                             "informational": []})

    def test_multi_day_all_day_touching_weekend_is_kept(self):
        e = ev("Trip", "2026-09-20", "2026-09-22", all_day=True)
        t = cc.classify([e], WK)
        self.assertEqual(len(t["informational"]), 1)

    def test_weekend_accepts_ordered_list(self):
        e = ev("Call", "2026-09-19T09:00", "2026-09-19T09:30")
        t = cc.classify([e], ["2026-09-18", "2026-09-19", "2026-09-20"])
        self.assertEqual(len(t["hard_conflicts"]), 1)


class TestOverlaps(unittest.TestCase):
    def test_true_overlap(self):
        cal = ev("Crew call", "2026-09-18T12:00", "2026-09-18T12:45")
        guide = {"date": "2026-09-18", "start_time": "11:30 AM"}
        self.assertTrue(cc.overlaps(cal, guide))

    def test_no_overlap(self):
        cal = ev("Facial", "2026-09-19T14:15", "2026-09-19T16:15")
        guide = {"date": "2026-09-19", "start_time": "6:00 PM"}
        self.assertFalse(cc.overlaps(cal, guide))

    def test_boundary_touch_is_not_overlap(self):
        cal = ev("Call", "2026-09-18T10:00", "2026-09-18T11:00")
        guide = {"date": "2026-09-18", "start_time": "11:00 AM"}
        self.assertFalse(cc.overlaps(cal, guide))

    def test_guide_without_start_time_never_overlaps(self):
        # No guessing: unparseable/absent start_time -> False.
        cal = ev("Call", "2026-09-18T12:00", "2026-09-18T12:45")
        self.assertFalse(cc.overlaps(cal, {"date": "2026-09-18",
                                           "start_time": None}))
        self.assertFalse(cc.overlaps(cal, {"date": "2026-09-18",
                                           "start_time": "evening"}))

    def test_different_dates_never_overlap(self):
        cal = ev("Call", "2026-09-18T12:00", "2026-09-18T12:45")
        guide = {"date": "2026-09-19", "start_time": "12:15 PM"}
        self.assertFalse(cc.overlaps(cal, guide))

    def test_cal_event_missing_end_assumes_60_minutes(self):
        cal = ev("Quick call", "2026-09-18T12:00", None)
        self.assertTrue(cc.overlaps(cal, {"date": "2026-09-18",
                                          "start_time": "12:30 PM"}))
        self.assertFalse(cc.overlaps(cal, {"date": "2026-09-18",
                                           "start_time": "2:00 PM"}))

    def test_cal_event_crossing_midnight(self):
        cal = ev("Late show", "2026-09-18T23:00", "2026-09-19T01:00")
        # Guide window 10:30 PM + 120 min -> overlaps the cal event.
        self.assertTrue(cc.overlaps(cal, {"date": "2026-09-18",
                                          "start_time": "10:30 PM"}))


if __name__ == "__main__":
    unittest.main()
