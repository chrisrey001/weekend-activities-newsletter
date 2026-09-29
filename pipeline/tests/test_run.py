"""Tests for pipeline/run.py stages that don't need the network.

Covers: weekend computation, brief mode, verify-gate halting, the
send-module contract validation (loud ImportError on missing/differing
module), and the run-log "sent"-word integrity rule.

A fake send_hardened module is injected into sys.modules where needed and
removed afterwards; no real send is ever attempted.
"""
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PIPE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("kiwi_run", PIPE / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


def make_events(path, weekend):
    data = {
        "weekend": weekend,
        "generated": "2026-09-17T09:30:00-06:00",
        "events": [{
            "name": "Good Event", "date": weekend[1], "venue": "Venue",
            "url": "https://example.com/good", "price": "$10",
            "description": "fine", "tags": [], "start_time": "7:00 PM",
            "recurring": False,
            "sources": [{"url": "https://example.com/good",
                         "retrieved_at": "2026-09-17"}],
        }],
        "dropped": [], "sources_ok": ["layer1:example.com"],
        "sources_failed": [],
    }
    Path(path).write_text(json.dumps(data))
    return path


class TestWeekend(unittest.TestCase):
    def test_thursday_run_covers_next_three_days(self):
        self.assertEqual(run.compute_weekend(
            __import__("datetime").date(2026, 9, 17)),
            ["2026-09-18", "2026-09-19", "2026-09-20"])

    def test_wednesday_run(self):
        self.assertEqual(run.compute_weekend(
            __import__("datetime").date(2026, 9, 16))[0], "2026-09-18")

    def test_friday_run_starts_today(self):
        self.assertEqual(run.compute_weekend(
            __import__("datetime").date(2026, 9, 18))[0], "2026-09-18")


class TestBriefMode(unittest.TestCase):
    def test_brief_emitted_and_stops(self):
        with tempfile.TemporaryDirectory() as td:
            rc = run.main(["--run-date", "2026-09-17", "--run-dir", td,
                           "--config", str(Path(__file__).parent /
                                           "fixtures" / "test-config.yaml")])
            self.assertEqual(rc, 0)
            brief = Path(td) / "research-brief.md"
            self.assertTrue(brief.exists())
            text = brief.read_text()
            for needle in ("3-layer", "date-verification", "stated_weekday",
                           "operator_confirmed", "operator_url",
                           "recurring", "sources", "floor 40", "events.json",
                           "--events-json"):
                self.assertIn(needle, text, needle)

    def test_brief_uses_real_run_weekday(self):
        # Regression: the brief once hardcoded "(Thursday)" even when the
        # run happened on another weekday. It must print the actual weekday.
        for run_date, weekday in (("2026-09-17", "Thursday"),
                                  ("2026-09-29", "Tuesday")):
            with tempfile.TemporaryDirectory() as td:
                rc = run.main(["--run-date", run_date, "--run-dir", td,
                               "--config", str(Path(__file__).parent /
                                               "fixtures" / "test-config.yaml")])
                self.assertEqual(rc, 0)
                text = (Path(td) / "research-brief.md").read_text()
                self.assertIn(f"Run date: {run_date} ({weekday})", text)

    def _brief_with_runs_dir(self, td, runs_dir):
        with patch.object(run, "RUNS_DIR", Path(runs_dir)):
            rc = run.main(["--run-date", "2026-09-17", "--run-dir", td,
                           "--config", str(Path(__file__).parent /
                                           "fixtures" / "test-config.yaml")])
        self.assertEqual(rc, 0)
        return (Path(td) / "research-brief.md").read_text()

    def test_brief_includes_source_health_when_index_exists(self):
        with tempfile.TemporaryDirectory() as td, \
                tempfile.TemporaryDirectory() as rd:
            (Path(rd) / "index.json").write_text(json.dumps({
                "version": 1, "updated": "2026-09-29T00:00:00+00:00",
                "sources": [{
                    "_id": "steady.com", "layer": "layer1", "name": "steady.com",
                    "ok": 3, "partial": 0, "failed": 0, "runs_seen": 3,
                    "first_seen": "2026-09-11", "last_seen": "2026-09-24",
                    "last_ok": "2026-09-24", "last_status": "ok",
                    "streak_failed": 0, "layers": ["layer1"]}],
                "featured": [],
            }))
            text = self._brief_with_runs_dir(td, rd)
            self.assertIn("## Source health (from prior runs)", text)
            self.assertIn("steady.com", text)

    def test_brief_omits_source_health_without_index(self):
        # Fresh install: no runs/index.json yet -> no health section, and
        # the brief must still render (no crash on missing index).
        with tempfile.TemporaryDirectory() as td, \
                tempfile.TemporaryDirectory() as rd:
            text = self._brief_with_runs_dir(td, rd)
            self.assertNotIn("## Source health", text)
            self.assertIn("## Date-verification rule", text)


class TestVerifyGate(unittest.TestCase):
    def test_verify_failure_halts(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.json"
            # Bad date format -> verify.py FAIL -> PipelineHalt.
            p.write_text(json.dumps({
                "weekend": ["2026-09-18", "2026-09-19", "2026-09-20"],
                "events": [{"name": "Bad", "date": "not-a-date",
                            "venue": "V", "url": "https://example.com",
                            "sources": [{"url": "https://example.com",
                                         "retrieved_at": "2026-09-17"}]}]}))
            with self.assertRaises(run.PipelineHalt):
                run.stage_verify(str(p), ["2026-09-18", "2026-09-19",
                                          "2026-09-20"], [])

    def test_verify_passes_good_events(self):
        with tempfile.TemporaryDirectory() as td:
            p = make_events(Path(td) / "events.json",
                            ["2026-09-18", "2026-09-19", "2026-09-20"])
            out = []
            run.stage_verify(str(p), ["2026-09-18", "2026-09-19",
                                     "2026-09-20"], out)
            self.assertTrue(any("failed checks: 0" in line for line in out))


class TestSendContract(unittest.TestCase):
    def setUp(self):
        self._saved = sys.modules.pop("send_hardened", None)

    def tearDown(self):
        sys.modules.pop("send_hardened", None)
        if self._saved is not None:
            sys.modules["send_hardened"] = self._saved

    def test_missing_module_raises_importerror(self):
        # send_hardened.py exists on disk, so simulate "missing" by making
        # the import itself fail. (Popping sys.modules no longer works:
        # load_send_module() re-imports the real file from disk.)
        with patch("importlib.import_module",
                   side_effect=ImportError("No module named 'send_hardened'")):
            with self.assertRaises(ImportError) as ctx:
                run.load_send_module()
        self.assertIn("MISSING", str(ctx.exception))

    def test_wrong_interface_raises_importerror(self):
        sys.modules["send_hardened"] = SimpleNamespace()  # no SendResult
        with self.assertRaises(ImportError):
            run.load_send_module()

    def test_wrong_fields_raise_importerror(self):
        import dataclasses

        @dataclasses.dataclass
        class Wrong:
            status: str

        def fake_send(**kw):
            return SimpleNamespace(status="sent", message_id="x", notes="")

        sys.modules["send_hardened"] = SimpleNamespace(
            SendResult=Wrong, send_newsletter=fake_send)
        with self.assertRaises(ImportError):
            run.load_send_module()

    def test_unknown_status_halts(self):
        import dataclasses

        @dataclasses.dataclass
        class SendResult:
            status: str
            message_id: object = None
            notes: str = ""

        def fake_send(*, html_body, subject, recipient, run_id,
                      dry_run=False, sender=None, gmail=None):
            return SendResult(status="mystery", message_id=None,
                              notes="weird")

        sys.modules["send_hardened"] = SimpleNamespace(
            SendResult=SendResult, send_newsletter=fake_send)
        with self.assertRaises(run.PipelineHalt):
            run.stage_send("<html>", "subj", "r@x.com", "rid", False, [])

    def test_dry_run_returns_dry_run_status(self):
        import dataclasses

        @dataclasses.dataclass
        class SendResult:
            status: str
            message_id: object = None
            notes: str = ""

        seen = {}

        def fake_send(*, html_body, subject, recipient, run_id,
                      dry_run=False, sender=None, gmail=None):
            seen.update(html_body=html_body, dry_run=dry_run)
            return SendResult(status="dry_run", message_id=None,
                              notes="dry run")

        sys.modules["send_hardened"] = SimpleNamespace(
            SendResult=SendResult, send_newsletter=fake_send)
        res = run.stage_send("<html>", "subj", "r@x.com", "rid", True, [])
        self.assertEqual(res.status, "dry_run")
        self.assertTrue(seen["dry_run"])

    def test_stage_send_forwards_sender(self):
        import dataclasses

        @dataclasses.dataclass
        class SendResult:
            status: str
            message_id: object = None
            notes: str = ""

        seen = {}

        def fake_send(*, html_body, subject, recipient, run_id,
                      dry_run=False, sender=None, gmail=None):
            seen["sender"] = sender
            return SendResult(status="dry_run", message_id=None,
                              notes="dry run")

        sys.modules["send_hardened"] = SimpleNamespace(
            SendResult=SendResult, send_newsletter=fake_send)
        run.stage_send("<html>", "subj", "r@x.com", "rid", True, [],
                       sender="Kiwi <you@example.com>")
        self.assertEqual(seen["sender"], "Kiwi <you@example.com>")


class TestLogIntegrity(unittest.TestCase):
    """The 2026-09-17 incident rule: the standalone word 'sent' may appear
    in run-log.md ONLY when SendResult.status == 'sent'."""

    def _log(self, status):
        ev = {"name": "Good Event", "date": "2026-09-19", "price": "$10"}
        return run.build_run_log(
            run_date=__import__("datetime").date(2026, 9, 17),
            weekend=["2026-09-18", "2026-09-19", "2026-09-20"],
            recipient="you@example.com", dry_run=(status == "dry_run"),
            run_id="2026-09-17::you@example.com", events=[ev],
            data={"sources_ok": [], "sources_failed": [], "dropped": []},
            verify_out="events: 1, failed checks: 0, warnings: 0",
            calendar_note="calendar not provided",
            weather={"note": "", "status": "not_provided", "rain_days": []},
            preflight={"urls_checked": 1, "bot403_accepted": 0},
            send_result=SimpleNamespace(status=status, message_id="mid123",
                                        notes="notes here"))

    def test_word_sent_only_when_status_sent(self):
        for status in ("dry_run", "already_sent", "failed"):
            log = self._log(status)
            self.assertIsNone(re.search(r"(?i)\bsent\b", log),
                              f"word 'sent' leaked into log for {status}")
        log = self._log("sent")
        self.assertIsNotNone(re.search(r"(?i)\bsent\b", log))

    def test_failed_log_names_no_dispatch(self):
        log = self._log("failed")
        self.assertIn("`failed`", log)
        self.assertIn("notes here", log)  # verbatim notes

    def test_log_includes_replacement_proposals(self):
        ev = {"name": "Good Event", "date": "2026-09-19", "price": "$10"}
        log = run.build_run_log(
            run_date=__import__("datetime").date(2026, 9, 17),
            weekend=["2026-09-18", "2026-09-19", "2026-09-20"],
            recipient="you@example.com", dry_run=True,
            run_id="2026-09-17::you@example.com", events=[ev],
            data={"sources_ok": [], "sources_failed": [],
                  "dropped": [],
                  "source_proposals": [
                      {"replaces": "dead.com", "candidate": "New Weekly",
                       "url": "https://new.example.com",
                       "why": "current weekend roundup"}]},
            verify_out="events: 1, failed checks: 0, warnings: 0",
            calendar_note="calendar not provided",
            weather={"note": "", "status": "not_provided", "rain_days": []},
            preflight={"urls_checked": 1, "bot403_accepted": 0},
            send_result=SimpleNamespace(status="dry_run", message_id=None,
                                        notes="dry run"))
        self.assertIn("Replacement proposals: 1", log)
        self.assertIn("New Weekly (https://new.example.com) replaces dead.com",
                      log)


if __name__ == "__main__":
    unittest.main()


class TestCalendarFeatureFlag(unittest.TestCase):
    """features.calendar_integration=false must ignore --calendar-json."""

    def _cal_json(self):
        import json
        import tempfile
        cal = {"events": [
            {"title": "Busy morning", "start": "2026-10-03T10:00:00",
             "end": "2026-10-03T12:00:00", "description": ""}]}
        p = Path(tempfile.mkdtemp()) / "cal.json"
        p.write_text(json.dumps(cal))
        return str(p)

    def _run_enrich(self, enabled):
        ev = {"name": "E", "date": "2026-10-03", "start_time": "11:00 AM",
              "price": "$5"}
        out = []
        day_notes, weather, ctx = run.stage_enrich(
            [ev], ["2026-10-02", "2026-10-03", "2026-10-04"],
            self._cal_json(), None, out, calendar_enabled=enabled)
        return ev, out, ctx

    def test_flag_off_ignores_calendar(self):
        ev, out, ctx = self._run_enrich(False)
        self.assertEqual(ctx["note"], "calendar not provided")
        self.assertNotIn("conflict", ev)
        self.assertTrue(any("calendar_integration=false" in l for l in out))

    def test_flag_on_applies_calendar(self):
        ev, out, ctx = self._run_enrich(True)
        self.assertIn("conflict", ev)
