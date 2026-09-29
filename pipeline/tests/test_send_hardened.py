"""Tests for pipeline/send_hardened.py.

All Gmail interaction is mocked -- no real sends, no real CLI calls.
Run: python3 -m unittest discover -s ~/workspace/kiwis-corner/pipeline/tests -p "test_send_hardened.py" -v
(also runnable from the pipeline dir: python3 -m unittest tests.test_send_hardened -v)
"""

import re
import subprocess
import sys
import unittest
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from send_hardened import (  # noqa: E402
    CliGmailAdapter,
    GmailAdapterError,
    SendResult,
    _is_error_output,
    _is_same_send,
    _is_verified_send,
    _parse_date_header,
    _parse_send_output,
    _sent_query,
    _today_denver,
    _to_matches,
    send_newsletter,
)

SUBJECT = "Kiwi's Weekend Guide \u2014 Friday, September 25 \u2013 Sunday, September 27"
RECIPIENT = "you@example.com"
RUN_ID = "test-run-001"
HTML = "<html><body><p>test</p></body></html>"


def today_rfc() -> str:
    return format_datetime(datetime.now().astimezone())


def yesterday_rfc() -> str:
    from datetime import timedelta
    return format_datetime(datetime.now().astimezone() - timedelta(days=1))


def candidate(cid="msg_abc123", to=RECIPIENT, subject=SUBJECT,
              date=None) -> dict:
    return {"id": cid, "to": to, "subject": subject,
            "date": date if date is not None else today_rfc()}


class MockGmail:
    """Injectable adapter per the contract. Records calls; scripted."""

    def __init__(self, search_results=(), send_id="msg_new1",
                 send_exc=None, search_exc=None):
        self._search_results = list(search_results)
        self._send_id = send_id
        self._send_exc = send_exc
        self._search_exc = search_exc
        self.send_calls = 0
        self.search_calls = 0

    def search_sent(self, subject, recipient, date_str):
        self.search_calls += 1
        if self._search_exc:
            raise self._search_exc
        # contract: receives subject, recipient, date_str (YYYY-MM-DD)
        assert isinstance(date_str, str) and len(date_str) == 10, date_str
        return [dict(r) for r in self._search_results]

    def send(self, to, subject, html_body, sender=None):
        self.send_calls += 1
        self.last_sender = sender
        assert to == RECIPIENT, to
        assert subject == SUBJECT, subject
        assert html_body == HTML, html_body
        if self._send_exc:
            raise self._send_exc
        return self._send_id


class TestIdempotency(unittest.TestCase):
    def test_duplicate_prevents_send(self):
        g = MockGmail(search_results=[candidate()])
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "already_sent")
        self.assertEqual(res.message_id, "msg_abc123")
        self.assertIn("duplicate prevented", res.notes)
        self.assertEqual(g.send_calls, 0, "must never send when already sent")

    def test_dry_run_still_reports_already_sent(self):
        # Step 1 (idempotency) runs BEFORE the dry-run short-circuit.
        g = MockGmail(search_results=[candidate()])
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID,
                              dry_run=True, gmail=g)
        self.assertEqual(res.status, "already_sent")
        self.assertEqual(g.send_calls, 0)

    def test_yesterdays_guide_does_not_block(self):
        g = MockGmail(search_results=[candidate(date=yesterday_rfc())],
                      send_id="msg_new1")
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "failed")  # post-send search empty
        self.assertEqual(g.send_calls, 1)

    def test_different_recipient_does_not_block(self):
        g = MockGmail(
            search_results=[candidate(to="partner@example.com")],
            send_id="msg_new1")
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(g.send_calls, 1, "different recipient must not block")

    def test_search_failure_aborts_fail_closed(self):
        g = MockGmail(search_exc=GmailAdapterError("cli exploded"))
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "failed")
        self.assertIsNone(res.message_id)
        self.assertEqual(g.send_calls, 0, "never send blind on search failure")


class TestDryRun(unittest.TestCase):
    def test_dry_run_sends_nothing(self):
        g = MockGmail()
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID,
                              dry_run=True, gmail=g)
        self.assertEqual(res.status, "dry_run")
        self.assertIsNone(res.message_id)
        self.assertIn("dry run: no send attempted", res.notes)
        self.assertEqual(g.send_calls, 0)


class TestSendAndVerify(unittest.TestCase):
    def _mock_verified(self, send_id):
        g = MockGmail(send_id=send_id)
        g._search_results = []  # first call: nothing sent yet
        calls = {"n": 0}

        orig = g.search_sent

        def scripted(subject, recipient, date_str):
            calls["n"] += 1
            if calls["n"] == 1:
                return []
            return [candidate(cid=send_id)]  # post-send: confirmed
        g.search_sent = scripted
        return g

    def test_happy_path_returns_sent(self):
        g = self._mock_verified("msg_live1")
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "sent")
        self.assertEqual(res.message_id, "msg_live1")
        self.assertIn("verified in Sent", res.notes)
        self.assertEqual(g.send_calls, 1)

    def test_no_message_id_is_failed(self):
        g = MockGmail(send_id=None)
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "failed")
        self.assertIsNone(res.message_id)
        self.assertIn("send returned no message ID", res.notes)

    def test_empty_message_id_is_failed(self):
        g = MockGmail(send_id="   ")
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "failed")
        self.assertIn("send returned no message ID", res.notes)

    def test_unverified_send_is_failed_with_id_preserved(self):
        # Post-send search finds nothing: FAILED, keeps ID for the run log,
        # and carries the DO-NOT-RETRY note.
        g = MockGmail(send_id="msg_maybe1")
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.message_id, "msg_maybe1")
        self.assertIn("DO NOT retry without checking the mailbox", res.notes)
        self.assertIn("manual review required", res.notes)

    def test_wrong_id_in_sent_is_failed(self):
        # Step 1 must see nothing; only the Step-4 re-search finds a message
        # whose id does NOT match what send() returned.
        g = MockGmail(send_id="msg_maybe1")
        calls = {"n": 0}

        def scripted(subject, recipient, date_str):
            calls["n"] += 1
            if calls["n"] == 1:
                return []
            return [candidate(cid="msg_other9")]
        g.search_sent = scripted
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.message_id, "msg_maybe1")
        self.assertIn("DO NOT retry without checking the mailbox", res.notes)

    def test_send_exception_is_failed(self):
        g = MockGmail(send_exc=GmailAdapterError("approval_expired"))
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "failed")
        self.assertIsNone(res.message_id)
        self.assertIn("approval_expired", res.notes)

    def test_research_failure_is_failed_with_id(self):
        g = MockGmail(send_id="msg_live2")
        calls = {"n": 0}

        def scripted(subject, recipient, date_str):
            calls["n"] += 1
            if calls["n"] == 1:
                return []  # Step 1: nothing in Sent yet
            raise GmailAdapterError("list exploded")  # Step 4: re-search dies
        g.search_sent = scripted
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.message_id, "msg_live2")
        self.assertIn("DO NOT retry without checking the mailbox", res.notes)

    def test_to_case_insensitive(self):
        g = MockGmail(search_results=[candidate(to="You@Example.com")])
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "already_sent")

    def test_to_display_name_form(self):
        g = MockGmail(search_results=[candidate(to="Jordan Smith <you@example.com>")])
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "already_sent")


class TestSentIsImpossibleWithoutEvidence(unittest.TestCase):
    """Regression guard for the 2026-09-17 incident: 'sent' must be returned
    from exactly one code path -- the Step-4 post-send verification."""

    def test_single_sent_return_in_source(self):
        src = Path(__file__).resolve().parents[1].joinpath(
            "send_hardened.py").read_text()
        occurrences = re.findall(r'SendResult\(\s*"sent"', src)
        self.assertEqual(len(occurrences), 1,
                         f"expected exactly one 'sent' return path, found "
                         f"{len(occurrences)}")

    def test_every_path_without_verification_is_not_sent(self):
        # Fuzz the candidate space: any candidate that is NOT an exact
        # verified match must never yield "sent".
        bad_candidates = [
            candidate(cid=""),                                   # no id
            candidate(subject="Other subject"),                  # wrong subj
            candidate(to="someone@else.com"),                    # wrong recip
            candidate(date=yesterday_rfc()),                      # wrong date
            candidate(date="not a date"),                        # unparseable
            "not-a-dict",                                        # wrong type
            {},                                                  # empty
        ]
        for bad in bad_candidates:
            g = MockGmail(search_results=[bad], send_id="msg_x")
            res = send_newsletter(html_body=HTML, subject=SUBJECT,
                                  recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
            # Either already_sent is correctly avoided (send attempted) or,
            # if send "succeeds", verification must fail.
            self.assertNotEqual(res.status, "already_sent",
                                f"false idempotency hit for {bad!r}")
            self.assertNotEqual(res.status, "sent",
                                f"false 'sent' for {bad!r}")

    def test_status_enum_closed(self):
        g = MockGmail()
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID,
                              dry_run=True, gmail=g)
        self.assertIn(res.status, ("sent", "already_sent", "failed", "dry_run"))


class TestMatchingHelpers(unittest.TestCase):
    def test_parse_date_header(self):
        self.assertEqual(_parse_date_header(today_rfc()), _today_denver())

    def test_parse_date_header_garbage(self):
        self.assertIsNone(_parse_date_header("not a date"))
        self.assertIsNone(_parse_date_header(""))
        self.assertIsNone(_parse_date_header(None))

    def test_to_matches(self):
        self.assertTrue(_to_matches("you@example.com", RECIPIENT))
        self.assertTrue(_to_matches("YOU@EXAMPLE.COM", RECIPIENT))
        self.assertTrue(_to_matches("Jordan <you@example.com>, x@y.z",
                                    RECIPIENT))
        self.assertFalse(_to_matches("partner@example.com", RECIPIENT))
        self.assertFalse(_to_matches("", RECIPIENT))

    def test_sent_query_binds_single_day(self):
        q = _sent_query(SUBJECT, "2026-09-24")
        self.assertIn("in:sent", q)
        self.assertIn('subject:"', q)
        self.assertIn("after:2026/9/24", q)
        self.assertIn("before:2026/9/25", q)

    def test_is_error_output(self):
        self.assertTrue(_is_error_output('Error: {"error":"approval_expired"}'))
        self.assertTrue(_is_error_output('{"error":"x"}'))
        self.assertFalse(_is_error_output('{"id":"abc123"}'))
        self.assertFalse(_is_error_output(""))


class TestCliAdapterParsing(unittest.TestCase):
    def _proc(self, returncode, stdout, stderr=""):
        return subprocess.CompletedProcess(args=["x"], returncode=returncode,
                                           stdout=stdout, stderr=stderr)

    def test_success_id_parsed(self):
        proc = self._proc(0, '{"id": "19d2abc", "threadId": "19d2abc"}')
        self.assertEqual(_parse_send_output(proc), "19d2abc")

    def test_success_message_id_alias(self):
        proc = self._proc(0, '{"messageId": "zz99"}')
        self.assertEqual(_parse_send_output(proc), "zz99")

    def test_success_no_id_returns_none(self):
        proc = self._proc(0, '{"ok": true}')
        self.assertIsNone(_parse_send_output(proc))

    def test_success_garbage_returns_none(self):
        proc = self._proc(0, "not json at all")
        self.assertIsNone(_parse_send_output(proc))

    def test_nonzero_exit_raises(self):
        proc = self._proc(1, "", "boom")
        with self.assertRaises(GmailAdapterError):
            _parse_send_output(proc)

    def test_error_shaped_output_raises_even_on_exit_zero(self):
        # Observed 2026-09-17: approval_expired arrived with exit 0.
        proc = self._proc(
            0, 'Error: {"error":"approval_expired","message_id":"d991ad88"}')
        with self.assertRaises(GmailAdapterError) as ctx:
            _parse_send_output(proc)
        self.assertIn("approval_expired", str(ctx.exception))

    def test_send_timeout_raises_adapter_error(self):
        adapter = CliGmailAdapter(cli="true")  # cli value irrelevant; patched
        with patch("send_hardened.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("x", 1)):
            with self.assertRaises(GmailAdapterError) as ctx:
                adapter.send(to="a@b.c", subject="s", html_body="b")
        self.assertIn("timed out", str(ctx.exception))

    def test_never_invents_id(self):
        # No code path fabricates an ID: every non-error, ID-less output
        # yields None, and the orchestrator turns None into "failed".
        for body in ('{"ok":true}', "{}", "[]", "sent ok", ""):
            proc = self._proc(0, body)
            self.assertIsNone(_parse_send_output(proc), body)


class TestNotesNeverLeakSentWord(unittest.TestCase):
    """Contract with pipeline/run.py's build_run_log(): that builder raises
    rather than write a log containing the standalone word "sent" unless
    SendResult.status == "sent". So every notes string this module produces
    for a NON-"sent" status must avoid the standalone word "sent"
    (case-insensitive) -- otherwise a real failed run would raise at
    log-build time and NO run log would be written at all (found 2026-09-17
    during send-path hardening audit)."""

    def _all_non_sent_results(self):
        out = []
        # idempotency hit
        out.append(send_newsletter(
            html_body=HTML, subject=SUBJECT, recipient=RECIPIENT,
            run_id=RUN_ID, gmail=MockGmail(search_results=[candidate()])))
        # pre-send search failure
        out.append(send_newsletter(
            html_body=HTML, subject=SUBJECT, recipient=RECIPIENT,
            run_id=RUN_ID,
            gmail=MockGmail(search_exc=GmailAdapterError("x"))))
        # send exception
        out.append(send_newsletter(
            html_body=HTML, subject=SUBJECT, recipient=RECIPIENT,
            run_id=RUN_ID,
            gmail=MockGmail(send_exc=GmailAdapterError("approval_expired"))))
        # no message ID
        out.append(send_newsletter(
            html_body=HTML, subject=SUBJECT, recipient=RECIPIENT,
            run_id=RUN_ID, gmail=MockGmail(send_id=None)))
        # unverified post-send
        out.append(send_newsletter(
            html_body=HTML, subject=SUBJECT, recipient=RECIPIENT,
            run_id=RUN_ID, gmail=MockGmail(send_id="msg_maybe1")))
        # dry run
        out.append(send_newsletter(
            html_body=HTML, subject=SUBJECT, recipient=RECIPIENT,
            run_id=RUN_ID, dry_run=True, gmail=MockGmail()))
        return out

    def test_no_non_sent_notes_contain_standalone_sent(self):
        word = re.compile(r"(?i)\bsent\b")
        for res in self._all_non_sent_results():
            self.assertNotEqual(res.status, "sent")
            self.assertIsNone(
                word.search(res.notes),
                f"status {res.status!r} notes leak standalone 'sent': "
                f"{res.notes!r}")

    def test_sent_notes_may_say_sent(self):
        # Sanity: the one allowed exception -- status "sent" -- still does.
        g = MockGmail(send_id="msg_ok1")
        calls = {"n": 0}

        def scripted(subject, recipient, date_str):
            calls["n"] += 1
            return [] if calls["n"] == 1 else [candidate(cid="msg_ok1")]
        g.search_sent = scripted
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "sent")
        self.assertIsNotNone(re.search(r"(?i)\bsent\b", res.notes))


# ---------------------------------------------------------------------------
# Sender display identity ("Kiwi" for the newsletter only; the shared agent
# alias keeps its own display name for other workflows).


class TestSenderIdentity(unittest.TestCase):
    SENDER = "Kiwi <you@example.com>"

    def _mock_verified(self, send_id):
        g = MockGmail(send_id=send_id)

        def scripted(subject, recipient, date_str):
            scripted.n += 1
            if scripted.n == 1:
                return []
            return [candidate(cid=send_id)]

        scripted.n = 0
        g.search_sent = scripted
        return g

    def test_sender_forwarded_to_adapter(self):
        g = self._mock_verified("msg_kiwi1")
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID,
                              sender=self.SENDER, gmail=g)
        self.assertEqual(res.status, "sent")
        self.assertEqual(g.last_sender, self.SENDER)

    def test_no_sender_uses_account_default(self):
        g = self._mock_verified("msg_kiwi2")
        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=RECIPIENT, run_id=RUN_ID, gmail=g)
        self.assertEqual(res.status, "sent")
        self.assertIsNone(g.last_sender)

    def test_cli_adapter_includes_from_flag(self):
        import send_hardened as sh
        seen = {}

        def fake_run_cli(argv, timeout_s):
            seen["argv"] = argv
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout='{"id": "abc1"}', stderr="")

        with patch.object(sh, "_run_cli", fake_run_cli):
            mid = sh.CliGmailAdapter().send(
                to=RECIPIENT, subject=SUBJECT, html_body=HTML,
                sender=self.SENDER)
        self.assertEqual(mid, "abc1")
        argv = seen["argv"]
        self.assertIn("--from", argv)
        self.assertEqual(argv[argv.index("--from") + 1], self.SENDER)

    def test_cli_adapter_omits_from_when_no_sender(self):
        import send_hardened as sh
        seen = {}

        def fake_run_cli(argv, timeout_s):
            seen["argv"] = argv
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout='{"id": "abc2"}', stderr="")

        with patch.object(sh, "_run_cli", fake_run_cli):
            sh.CliGmailAdapter().send(
                to=RECIPIENT, subject=SUBJECT, html_body=HTML)
        self.assertNotIn("--from", seen["argv"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Multi-recipient policy (legacy sender retired 2026-09-26; single sender:
# ONE message to both recipients). Idempotency and post-send verification
# must require EVERY address present in the To header, never just one.


class TestMultiRecipient(unittest.TestCase):
    BOTH = "you@example.com, partner@example.com"

    def test_to_matches_both_present(self):
        self.assertTrue(
            _to_matches("you@example.com, partner@example.com",
                        self.BOTH))

    def test_to_matches_order_insensitive(self):
        self.assertTrue(
            _to_matches("partner@example.com, you@example.com",
                        self.BOTH))

    def test_to_matches_display_name_forms(self):
        self.assertTrue(
            _to_matches(
                "Jordan Smith <you@example.com>, "
                "Casey <partner@example.com>", self.BOTH))

    def test_to_matches_case_insensitive(self):
        self.assertTrue(
            _to_matches("YOU@EXAMPLE.COM, Partner@Example.com",
                        self.BOTH))

    def test_to_matches_partial_is_false(self):
        # A message to Jordan ONLY must NOT count as this week's
        # both-recipients send -- otherwise the gate could skip Casey.
        self.assertFalse(_to_matches("you@example.com", self.BOTH))

    def test_to_matches_empty_recipient_is_false(self):
        self.assertFalse(_to_matches("you@example.com", ""))

    def test_is_same_send_requires_both(self):
        both_cand = candidate(to="partner@example.com, you@example.com")
        self.assertTrue(
            _is_same_send(both_cand, subject=SUBJECT,
                          recipient=self.BOTH, today=_today_denver()))
        chris_only = candidate(to=RECIPIENT)
        self.assertFalse(
            _is_same_send(chris_only, subject=SUBJECT,
                          recipient=self.BOTH, today=_today_denver()))

    def test_is_verified_send_requires_both(self):
        both_cand = candidate(cid="msg_new1",
                              to="you@example.com, partner@example.com")
        self.assertTrue(
            _is_verified_send(both_cand, message_id="msg_new1",
                              recipient=self.BOTH, today=_today_denver()))
        chris_only = candidate(cid="msg_new1", to=RECIPIENT)
        self.assertFalse(
            _is_verified_send(chris_only, message_id="msg_new1",
                              recipient=self.BOTH, today=_today_denver()))

    def test_both_recipients_send_and_idempotency(self):
        sent_to = {}
        both = self.BOTH

        class BothMock:
            def __init__(self):
                self.results = []

            def search_sent(self, subject, recipient, date_str):
                return [dict(c) for c in self.results]

            def send(self, to, subject, html_body, sender=None):
                sent_to["to"] = to
                # simulate Gmail: the message lands in Sent with both in To
                self.results.append(
                    candidate(cid="msg_new2", to=to, subject=subject))
                return "msg_new2"

        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=both, run_id=RUN_ID,
                              gmail=BothMock())
        self.assertEqual(res.status, "sent")
        self.assertEqual(res.message_id, "msg_new2")
        # one message, both addresses in --to
        self.assertIn("you@example.com", sent_to["to"])
        self.assertIn("partner@example.com", sent_to["to"])

    def test_both_recipients_duplicate_blocks_retry(self):
        BothMock_results = [
            candidate(cid="msg_old",
                      to="partner@example.com, you@example.com")]

        class DupMock:
            send_calls = 0

            def search_sent(self, subject, recipient, date_str):
                return [dict(c) for c in BothMock_results]

            def send(self, to, subject, html_body, sender=None):
                DupMock.send_calls += 1
                return "msg_dup"

        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=self.BOTH, run_id=RUN_ID,
                              gmail=DupMock())
        self.assertEqual(res.status, "already_sent")
        self.assertEqual(res.message_id, "msg_old")
        self.assertEqual(DupMock.send_calls, 0)

    def test_chris_only_message_does_not_block_both_send(self):
        # Last week's Jordan-only sends must not suppress the new
        # both-recipients send.
        class NoDupMock:
            send_calls = 0

            def __init__(self):
                self.results = [
                    candidate(cid="msg_old", to=RECIPIENT)]

            def search_sent(self, subject, recipient, date_str):
                return [dict(c) for c in self.results]

            def send(self, to, subject, html_body, sender=None):
                NoDupMock.send_calls += 1
                self.results.append(
                    candidate(cid="msg_new3", to=to, subject=subject))
                return "msg_new3"

        res = send_newsletter(html_body=HTML, subject=SUBJECT,
                              recipient=self.BOTH, run_id=RUN_ID,
                              gmail=NoDupMock())
        self.assertEqual(res.status, "sent")
        self.assertEqual(NoDupMock.send_calls, 1)
