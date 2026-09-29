#!/usr/bin/env python3
"""Pure decision function for the Kiwi's Corner send approval watch.

This module mirrors the ~08:45 Thursday approval-watch cron BODY in
pipeline/approval_watch.md (the §1 design and the §2 cron spec) VERBATIM
as a pure, deterministic function, so the decision logic can be
unit-tested without Gmail access. The cron worker's body text is the
source of truth; this function is a line-for-line transcription of its
decision steps (NOT an independent design):

  1. SENT CHECK FIRST: search Gmail Sent for today's guide — in:sent,
     subject containing "Kiwi's Weekend Guide", dated today. Read the
     To/Subject/Date headers of candidates. The send counts ONLY if BOTH
     addresses (chris.rey001@gmail.com and kndufour@gmail.com) appear in
     the To header (K3 sole sender since Tasklet canceled 2026-09-26).
     If today's guide is in Sent with both recipients, reply ...
     "All clear" ... and STOP. Do not alert.
  2. If the send is NOT in Sent: list your pending approval/permission
     requests and look for a pending Kiwi's Corner Gmail-send approval
     (+send / users.messages.send for the newsletter). If one is pending
     AND nothing is in Sent, alert Chris immediately in chat ...
  3. If no send is in Sent AND no approval is pending, alert Chris in chat
     that the 08:00 run's send has not landed and no approval is waiting
     — the run needs attention. ...

IDEMPOTENCY RULE (from the doc): the watch must NEVER trigger a send
itself — alert only. This function encodes only the decision (which alert
applies), never any action.

Inputs:
  sent_today (bool): today's guide was found in Gmail Sent
      (subject "Kiwi's Weekend Guide", BOTH chris.rey001@gmail.com and
      kndufour@gmail.com in the To header, dated today).
  pending_kiwis_approval (bool): a pending Kiwi's Corner Gmail-send
      approval (+send / users.messages.send for the newsletter) exists.

Output: one of "all_clear" | "approval_alert" | "needs_attention".
"""

DECISIONS = ("all_clear", "approval_alert", "needs_attention")


def decide(sent_today: bool, pending_kiwis_approval: bool) -> str:
    """Decide the approval watch's outcome from two booleans.

    Mirrors the cron body verbatim:
      - sent_today=True          -> "all_clear"       (cron step 1: STOP)
      - sent_today=False,
        pending=True             -> "approval_alert"  (cron step 2)
      - sent_today=False,
        pending=False            -> "needs_attention" (cron step 3)
    """
    if sent_today:
        # Cron step 1: Sent check FIRST -- today's guide in Sent means
        # all-clear, one line, STOP. No alert, even if an approval is also
        # pending.
        return "all_clear"
    if pending_kiwis_approval:
        # Cron step 2: nothing in Sent + a Kiwi's Corner send approval is
        # pending -> alert Chris that the send is parked on his approval.
        return "approval_alert"
    # Cron step 3: nothing in Sent and no approval pending -> the 8:00 run
    # failed earlier; alert Chris that the run needs attention.
    return "needs_attention"


if __name__ == "__main__":
    # Ad-hoc truth table (not the test suite; see tests/).
    for s in (True, False):
        for p in (True, False):
            print(f"sent_today={s!s:5} pending_kiwis_approval={p!s:5} "
                  f"-> {decide(s, p)}")
