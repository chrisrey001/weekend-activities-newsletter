"""Tests for pipeline/approval_watch_logic.py -- property (c).

The approval-watch cron body (pipeline/approval_watch.md §1/§2) is
worker instructions; this module is a pure transcription of its decision
steps, and these tests pin all four input combinations:

  sent_today=True                  -> "all_clear"      (cron step 1: STOP)
  sent_today=False, pending=True   -> "approval_alert" (cron step 2)
  sent_today=False, pending=False  -> "needs_attention" (cron step 3)

The module docstring quotes the cron body it mirrors; a test below guards
that the transcription still references the source steps.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import approval_watch_logic as awl  # noqa: E402
from approval_watch_logic import DECISIONS, decide  # noqa: E402


class TestApprovalWatchDecisions(unittest.TestCase):
    def test_all_four_input_combinations(self):
        self.assertEqual(decide(True, True), "all_clear")
        self.assertEqual(decide(True, False), "all_clear")
        self.assertEqual(decide(False, True), "approval_alert")
        self.assertEqual(decide(False, False), "needs_attention")

    def test_decisions_are_a_closed_set(self):
        self.assertEqual(set(DECISIONS),
                         {"all_clear", "approval_alert", "needs_attention"})
        for sent in (True, False):
            for pending in (True, False):
                self.assertIn(decide(sent, pending), DECISIONS)

    def test_sent_in_sent_always_wins(self):
        # Cron step 1 is the FIRST check: today's guide in Sent means
        # all-clear and STOP -- even if an approval also happens to be
        # pending, the watch must not raise a spurious approval alert.
        self.assertEqual(
            decide(sent_today=True, pending_kiwis_approval=True), "all_clear")

    def test_module_documents_its_source(self):
        # The transcription must keep pointing at the cron body it mirrors
        # (approval_watch.md), so a future edit to the doc doesn't silently
        # drift from the worker instructions.
        text = (awl.__doc__ or "") + (decide.__doc__ or "")
        for needle in ("approval_watch.md", "SENT CHECK FIRST",
                       "approval_alert", "needs_attention", "all_clear",
                       "NEVER trigger a send"):
            self.assertIn(needle, text, needle)


if __name__ == "__main__":
    unittest.main()
