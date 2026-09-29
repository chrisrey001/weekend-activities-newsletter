# Kiwi's Weekend Guide — Send Approval Watch (design doc + cron specs)

Author: send-hardening agent, 2026-09-17. Context: production run #1's Gmail
send parked on an unanswered connector approval from ~08:50 MDT while the run
log falsely recorded "sent" with no message ID; the real send completed at
10:21 only after the owner approved. The verified-send gate
(`pipeline/send_hardened.py`) now makes a false "sent" impossible, but a
parked send still means the newsletter is late and nobody knows until the
9:30 watchdog fires. This watch closes that 45-minute blind window.

## 1. Approval-wait alert — design

### Mechanism
A standing cron worker (tool access: Gmail search + pending-permission /
approval-request listing) fires Thursdays ~08:45 America/Denver — after the
08:00 production run's send step, before the 09:30 watchdog. It does exactly
three things:

1. **Check Sent first.** Search Gmail Sent for today's guide:
   `in:sent` + subject containing "Kiwi's Weekend Guide", addressed to
   `you@example.com`, dated today. Read the To/Subject/Date headers of
   candidates. If a match is present → report all-clear (one line) and STOP.
   No alert, no further action.
2. **If the send is NOT in Sent, list pending approval/permission requests.**
   Look for a pending Kiwi's Weekend Guide Gmail-send approval (the `+send` /
   `users.messages.send` write for the newsletter). If such an approval is
   pending AND the send is not in Sent → **alert the owner immediately in chat**:
   - what is parked (the Kiwi's Weekend Guide send for this week's Fri–Sun),
   - that the newsletter is built and waiting on their approval,
   - that tapping approve releases it,
   - that the 9:30 watchdog will confirm delivery afterward.
   Include the pending approval's identifying detail (connector/tool/method).
3. **If no send in Sent AND no approval pending** → the 8:00 run failed
   earlier in a way the gate already reported as "failed". Alert the owner in
   chat that the send has not landed, no approval is pending, and the run
   needs attention; quote the run details you can find (run log status,
   notes). Do not speculate about causes beyond what the evidence shows.

### Idempotency rule (hard)
The watch **never triggers a send itself** — alert only. No `+send`, no
retries, no draft-creation. Its only outputs are its final chat report (and
nothing at all beyond the one-line all-clear).

### Alert copy (template for the worker)
> ⚠️ Kiwi's Weekend Guide send is parked: this week's guide is built but its Gmail
> send is waiting on your approval. Tap approve to release it — I'll confirm
> delivery at the 9:30 check.

### Notes for the worker
- "Today" = the Thursday the cron fires, America/Denver. The subject changes
  weekly ("Kiwi's Weekend Guide — Friday, <Month D> – Sunday, <Month D>");
  match on subject *containing* "Kiwi's Weekend Guide" + today's date + the
  recipient, not on the full literal subject.
- The only authorized recipient is `you@example.com` (parallel-run
  policy: the second recipient gets nothing from this sender until the legacy sender is retired).
- An expired approval means Gmail performed NO send (observed 2026-09-17:
  "approval_expired … The action was not performed") — so a parked send is
  safe to alert on; there is no duplicate-send risk from the alert itself.
- Report through your final message to the chat. On all-clear, keep it to
  one line.

## 2. Cron spec — approval watch (coordinator: CREATE this)

```yaml
id: kiwis-corner-send-approval-watch
title: "Kiwi's Weekend Guide send approval watch"
mode: task
owner: goal:k3-migration-proposal
schedule:
  frequency: weekly
  day: Thursday
  time: "08:45"
  timezone: America/Denver
body: |
  You are the Kiwi's Weekend Guide send approval watch. Today is Thursday (America/Denver);
  the 08:00 production run should have sent this week's "Kiwi's Weekend Guide"
  (Fri–Sun weekend starting tomorrow) to you@example.com ONLY.

  1. SENT CHECK FIRST: search Gmail Sent for today's guide — in:sent, subject
     containing "Kiwi's Weekend Guide", to you@example.com, dated today.
     Read the To/Subject/Date headers of candidates. If today's guide is in
     Sent, reply with exactly one line: "All clear: Kiwi's Weekend Guide send verified
     in Sent; no action needed." and STOP. Do not alert.
  2. If the send is NOT in Sent: list your pending approval/permission requests
     and look for a pending Kiwi's Weekend Guide Gmail-send approval (+send /
     users.messages.send for the newsletter). If one is pending AND nothing is
     in Sent, alert the owner immediately in chat: say this week's guide is built
     and parked waiting on their approval, that tapping approve releases it, and
     that the 9:30 watchdog will confirm delivery. Include the pending
     approval's identifying detail.
  3. If no send is in Sent AND no approval is pending, alert the owner in chat that
     the 08:00 run's send has not landed and no approval is waiting — the run
     needs attention. Quote the run-log status/notes you can find. Do not
     speculate beyond the evidence.

  IDEMPOTENCY RULE: you must NEVER trigger a send yourself — alert only. No
  +send, no retries, no drafts. Report through your final message to the chat;
  on all-clear keep it to one line.
```

## 3. Cron specs — Thu 2026-09-24 production run + watchdog (coordinator: CREATE these)

The 9/17 runonce jobs are consumed and no longer exist; these are fresh adds.

### 3a. Production run

```yaml
id: kiwis-corner-production-run
title: "Kiwi's Weekend Guide production run"
mode: task
owner: goal:k3-migration-proposal
schedule:
  kind: runonce
  at: "2026-09-24T08:00"
  timezone: America/Denver
body: |
  Execute the durable Kiwi's Weekend Guide pipeline: run
  `python3 ~/workspace/kiwis-corner/pipeline/run.py` per
  ~/workspace/kiwis-corner/pipeline/RUNBOOK.md for the weekend
  Fri 2026-09-25 – Sun 2026-09-27.

  SEND POLICY (parallel run): send ONLY to you@example.com.
  partner@example.com is EXCLUDED — the legacy sender still covers both as
  the safety net until it is explicitly retired. Do not add any other
  recipient.

  SEND INTEGRITY (2026-09-17 incident): the send step MUST go through
  pipeline/send_hardened.py. Record "sent" ONLY when SendResult.status ==
  "sent" (requires a Gmail message ID verified in Sent). A "failed",
  "already_sent", or "dry_run" result must be reported as-is — never
  reworded into "sent".

  Final report (to chat) must state: sent / not-sent with the message ID as
  evidence, event count, and any failures/fallbacks.
```

### 3b. Watchdog

```yaml
id: kiwis-corner-watchdog
title: "Kiwi's Weekend Guide send watchdog"
mode: task
owner: goal:k3-migration-proposal
schedule:
  kind: runonce
  at: "2026-09-24T09:30"
  timezone: America/Denver
body: |
  Verify the Kiwi's Weekend Guide production send in Gmail Sent: look for today's
  "Kiwi's Weekend Guide" (Fri 2026-09-25 – Sun 2026-09-27) addressed to
  you@example.com, dated today. Read the To/Subject/Date headers of
  candidates to confirm.

  - If verified: report one all-clear line to chat. Done.
  - If NOT found: alert the owner immediately in chat with the failure details
    (what the 08:00 run reported, its SendResult status/notes, message ID if
    any). Explicitly do NOT resend and do NOT trigger any send — report only.
```

## 4. Assumptions / open questions

- Cron schema field names (`kind: runonce`, `at:`, weekly day/time keys)
  follow the coordinator's existing conventions — the coordinator owns the
  exact `cron.add` payload; bodies above are the source of truth for content.
- The worker's "pending approval/permission request" listing is assumed to be
  a capability of the cron worker's toolset (per the task brief). If the
  platform exposes it under a specific tool name, the coordinator should pin
  that name in the body at creation time.
- The 9/24 production run is still parallel-run (second recipient excluded)
  because the 9/17 run did NOT land clean. If the owner retires the legacy
  sender on/after 9/17 per the original plan, the coordinator may widen the
  9/24 recipient list — that is the owner's call, not this doc's.
