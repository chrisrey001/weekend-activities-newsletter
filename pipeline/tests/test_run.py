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
            rc = run.main(["--run-date", "2026-09-17", "--run-dir", td])
            self.assertEqual(rc, 0)
            brief = Path(td) / "research-brief.md"
            self.assertTrue(brief.exists())
            text = brief.read_text()
            for needle in ("3-layer", "date-verification", "stated_weekday",
                           "operator_confirmed", "operator_url",
                           "recurring", "sources", "40-65", "events.json",
                           "--events-json"):
                self.assertIn(needle, text, needle)


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
                       sender="Kiwi <chris.rey001@gmail.com>")
        self.assertEqual(seen["sender"], "Kiwi <chris.rey001@gmail.com>")


class TestLogIntegrity(unittest.TestCase):
    """The 2026-09-17 incident rule: the standalone word 'sent' may appear
    in run-log.md ONLY when SendResult.status == 'sent'."""

    def _log(self, status):
        ev = {"name": "Good Event", "date": "2026-09-19", "price": "$10"}
        return run.build_run_log(
            run_date=__import__("datetime").date(2026, 9, 17),
            weekend=["2026-09-18", "2026-09-19", "2026-09-20"],
            recipient="chris.rey001@gmail.com", dry_run=(status == "dry_run"),
            run_id="2026-09-17::chris.rey001@gmail.com", events=[ev],
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


if __name__ == "__main__":
    unittest.main()
