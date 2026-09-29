"""End-to-end send-gate regression tests: properties (a) and (b).

These drive pipeline/run.py (the real module code) with a MOCKED Gmail
adapter only -- no real email, no real CLI, no inbox reads. The real
CliGmailAdapter subprocess path is never exercised: the mock is injected
by replacing send_hardened.CliGmailAdapter with a factory (run.py always
calls send_newsletter(..., gmail=None), which instantiates the adapter via
the module-global name).

Property (a): a run CANNOT claim "sent" without proof. run.py is driven
end-to-end (verify -> enrich -> render -> pre-flight -> send -> log) with
a mock whose send() returns a message ID but whose post-send Sent search
finds nothing. Expected: SendResult.status == "failed", the written
run-log.md contains no standalone "sent" claim, and run.py raises
PipelineHalt (non-zero exit) instead of exiting 0. A second variant has
send() return no ID at all.

Property (b): idempotency blocks a duplicate. The send stage is driven
twice with a mock whose Sent search returns today's guide on the second
call. Expected: the second call returns "already_sent" and the mock's
send() was invoked exactly once total. A dry-run variant must never call
send() at all.
"""
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

PIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPE))

import send_hardened  # noqa: E402 -- real module; adapter class is patched

spec = importlib.util.spec_from_file_location("kiwi_run_e2e", PIPE / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

RUN_DATE = "2026-09-17"
WEEKEND = ["2026-09-18", "2026-09-19", "2026-09-20"]
SUBJECT = "Kiwi's Weekend Guide \u2014 E2E Test"
RECIPIENT = "chris.rey001@gmail.com"


def today_rfc():
    return format_datetime(datetime.now().astimezone())


# ---------------------------------------------------------------- fixtures

def make_events_json(path):
    """40 one-off events that pass verify.py deterministically."""
    events = []
    for i in range(40):
        events.append({
            "name": f"Denver Show {i + 1:02d}",
            "date": WEEKEND[i % 3],
            "venue": f"Venue {i + 1:02d}",
            "url": f"https://example.com/e2e-event-{i + 1:02d}",
            "price": "$20",
            "description": f"Verified test event {i + 1:02d}.",
            "tags": ["family-friendly"] if i % 2 else [],
            "start_time": "7:00 PM" if i % 2 else "11:00 AM",
            "recurring": False,
            "sources": [{"url": f"https://example.com/e2e-event-{i + 1:02d}",
                         "retrieved_at": "2026-09-17"}],
            "drive_time_from_englewood": "~25 min",
            "address_or_area": "Denver",
        })
    data = {"weekend": WEEKEND, "generated": "2026-09-17T10:00:00-06:00",
            "events": events, "dropped": [],
            "sources_ok": ["e2e-fixture"], "sources_failed": []}
    Path(path).write_text(json.dumps(data))
    return path


class MockGmail:
    """Injectable adapter. Scripted search results; records send calls."""

    def __init__(self, search_results=(), send_id="msg_x"):
        self._search_results = list(search_results)
        self._send_id = send_id
        self.send_calls = 0

    def search_sent(self, subject, recipient, date_str):
        return [dict(r) for r in self._search_results]

    def send(self, to, subject, html_body, sender=None):
        self.send_calls += 1
        return self._send_id


def fake_curl_factory(seen):
    def fake(url):
        seen.append(url)
        return "200"  # all links resolve; pre-flight itself still runs
    return fake


class E2EBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        td = Path(self._td.name)
        self.events_json = make_events_json(td / "events.json")
        self.header = td / "header.html"
        self.header.write_text("<p>A big weekend of test events.</p>")
        self.picks = td / "picks.json"
        self.picks.write_text(json.dumps(
            {"picks": [{"name": "Pick One", "detail": "great",
                        "url": "https://example.com/pick"}]}))
        self.run_dir = td / "rundir"
        self.seen_urls = []
        self.curl_patch = patch.object(
            run, "_curl_status", fake_curl_factory(self.seen_urls))
        self.curl_patch.start()
        self.addCleanup(self.curl_patch.stop)

    def _run_main_capture(self, mock):
        """Run run.main end-to-end; return (PipelineHalt|None, SendResult)."""
        captured = {}
        orig = run.stage_send

        def spy(html_doc, subject, recipient, run_id, dry_run, out_lines,
                sender=None):
            res = orig(html_doc, subject, recipient, run_id, dry_run,
                       out_lines, sender=sender)
            captured["result"] = res
            captured["sender"] = sender
            return res

        adapter_patch = patch.object(
            send_hardened, "CliGmailAdapter",
            lambda *a, **k: mock)  # mock injected; real CLI never runs
        adapter_patch.start()
        self.addCleanup(adapter_patch.stop)
        halt = None
        try:
            with patch.object(run, "stage_send", spy):
                run.main(["--run-date", RUN_DATE,
                          "--run-dir", str(self.run_dir),
                          "--events-json", str(self.events_json),
                          "--header-html", str(self.header),
                          "--picks-json", str(self.picks),
                          "--recipient", RECIPIENT])
        except run.PipelineHalt as e:
            halt = e
        return halt, captured.get("result")

    def _assert_failed_log(self, halt, result):
        self.assertIsNotNone(halt, "run.py must halt on an unverified send")
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "failed")
        log_path = self.run_dir / "run-log.md"
        self.assertTrue(log_path.exists(),
                        "the run log must still be written on failure")
        log = log_path.read_text()
        self.assertIn("Status: `failed`", log)
        self.assertIsNone(
            re.search(r"(?i)\bsent\b", log),
            "run-log.md must contain no standalone 'sent' claim when the "
            "send was not verified")


# ------------------------------------------------------- property (a)

class TestCannotClaimSent(E2EBase):
    """A run CANNOT claim 'sent': the 2026-09-17 incident regression."""

    def test_send_with_id_but_no_post_send_proof_fails(self):
        # The incident shape: send() returned a message ID, but the
        # post-send Sent search finds nothing. The run must NOT claim sent.
        mock = MockGmail(search_results=[], send_id="msg_unverified1")
        halt, result = self._run_main_capture(mock)
        self.assertEqual(mock.send_calls, 1)
        self.assertEqual(result.message_id, "msg_unverified1",
                         "the ID is preserved for diagnosis")
        self.assertIn("DO NOT retry", result.notes)
        self._assert_failed_log(halt, result)
        # The pre-flight link check really ran (41 unique hrefs) --
        # this is an end-to-end run, not a stage skip.
        self.assertGreaterEqual(len(self.seen_urls), 40)

    def test_send_returning_no_id_fails(self):
        # Variant: send() returns no ID at all.
        mock = MockGmail(search_results=[], send_id=None)
        halt, result = self._run_main_capture(mock)
        self.assertIsNone(result.message_id)
        self.assertIn("no message ID", result.notes)
        self._assert_failed_log(halt, result)

    def test_verified_send_still_succeeds(self):
        # Sanity: the gate is not a blanket failure -- a truly verified
        # send still returns "sent" and the run exits 0.
        mid = "msg_verified1"
        mock = MockGmail(send_id=mid)
        calls = {"n": 0}

        def scripted(subject, recipient, date_str):
            calls["n"] += 1
            if calls["n"] == 1:
                return []  # pre-send: nothing in Sent yet
            return [{"id": mid, "to": recipient, "subject": subject,
                     "date": today_rfc()}]  # post-send: confirmed
        mock.search_sent = scripted
        halt, result = self._run_main_capture(mock)
        self.assertIsNone(halt, "a verified send must not halt the run")
        self.assertEqual(result.status, "sent")
        self.assertEqual(result.message_id, mid)
        log = (self.run_dir / "run-log.md").read_text()
        self.assertIsNotNone(re.search(r"(?i)\bsent\b", log))
        self.assertIn(f"`{mid}`", log)


# ------------------------------------------------------- property (b)

class IdempotentMock:
    """First send_newsletter call: sends and verifies. Second call: the
    pre-send search finds today's guide -> already_sent."""

    def __init__(self):
        self.send_calls = 0
        self._searches = 0
        self._mid = "msg_dup1"

    def search_sent(self, subject, recipient, date_str):
        self._searches += 1
        if self._searches == 1:
            return []
        return [{"id": self._mid, "to": recipient, "subject": subject,
                 "date": today_rfc()}]

    def send(self, to, subject, html_body, sender=None):
        self.send_calls += 1
        return self._mid


class TestIdempotencyEndToEnd(unittest.TestCase):
    """Drive run.py's send stage twice; the second must be blocked."""

    def _stage_send(self, mock, dry_run=False):
        adapter_patch = patch.object(
            send_hardened, "CliGmailAdapter", lambda *a, **k: mock)
        with adapter_patch:
            return run.stage_send("<html>test</html>", SUBJECT, RECIPIENT,
                                  "e2e::dup", dry_run, [])

    def test_second_send_blocked_as_already_sent(self):
        mock = IdempotentMock()
        first = self._stage_send(mock)
        self.assertEqual(first.status, "sent")
        self.assertEqual(first.message_id, "msg_dup1")
        second = self._stage_send(mock)
        self.assertEqual(second.status, "already_sent")
        self.assertEqual(mock.send_calls, 1,
                         "send() must be invoked exactly once total")

    def test_dry_run_never_calls_send(self):
        mock = IdempotentMock()
        res = self._stage_send(mock, dry_run=True)
        self.assertEqual(res.status, "dry_run")
        self.assertEqual(mock.send_calls, 0,
                         "a dry run must never touch the send adapter")


if __name__ == "__main__":
    unittest.main()
