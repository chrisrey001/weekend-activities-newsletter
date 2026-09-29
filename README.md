# Kiwi's Corner — a personalized weekend newsletter, researched and sent by an AI agent

Every Thursday morning, an agent (Meta's Muse) researches the upcoming
Friday–Sunday, verifies 40–65 local events, and sends one personalized HTML
email to the family: drive times from home, confirmed prices, calendar-aware
scheduling notes, weather-aware picks, and a fresh weekend summary written for
that exact lineup.

This repo is the complete, working framework — pipeline code, verification
gates, send-integrity rules, operator docs, and the full test suite. It was
built for one family (Englewood, CO — two parents, a toddler, a dog named
Kiwi) and is designed to be re-skinned for any family, anywhere.

## How it works

```
Thursday 8:00 AM            Thursday 8:45 AM          Thursday 9:30 AM
production run ──────► approval watch ──────► watchdog
(research → verify           (did the send            (did the watch
 → render → send)             actually land?)           run? alert if not)
```

### The pipeline (`pipeline/run.py`)

1. **Research brief** — `run.py` emits a brief for this week's Fri–Sun, then
   stops. The agent researches in three layers:
   - Layer 1: general sources (local press, event roundups, ticketing sites)
   - Layer 2: a venue sweep (per-venue health; stale/cancelled/dupes dropped
     with documented reasons)
   - Layer 3: broad searches + big-ticket follow-ups to pin exact dates,
     times, prices, URLs
2. **Deterministic event records** — every event becomes a structured record.
   `verify.py` enforces the non-negotiable gates:
   - date verified against an explicit *current* listing for that exact weekend
   - a **confirmed price** — no price, no event
   - event-specific URLs only; 403/404/DNS stubs rejected
   - recurring events must carry `operator_confirmed=true` + `operator_url`
3. **Calendar context** (`calendar_context.py`) — three tiers:
   - hard timed conflicts (blocks an event)
   - useful family/context notes
   - informational all-day events (never invalidate a whole day)
4. **Weather hook** (`weather_hook.py`) — indoor alternatives when the
   forecast demands it.
5. **Template render** (`template.py`) — chronological within each day,
   categorized (family-friendly / date night / can't-miss), drive times from
   home, concise descriptions, kid-friendly labels where appropriate.
6. **Pre-flight gates** — every link resolved, every price present, footer
   intact.
7. **Send** (`send_hardened.py`) — see *Send integrity* below.

### Send integrity

The 2026-09-17 incident rule: a run once recorded "sent" while the email was
still parked on an unanswered approval. This module makes that impossible by
construction:

- **Sent-first idempotency** — today's Sent folder is searched *before* any
  send. Already there → `already_sent`, no second send, ever.
- **Message ID required** — no ID (timeout, approval pending, empty output)
  → `failed`, never "sent".
- **Post-send confirmation** — the returned ID must be found in Sent, dated
  today, addressed to every intended recipient. Only then → `sent`. This is
  the *only* code path that returns "sent".
- **DO-NOT-RETRY** — a failure after a possible send is never retried
  blindly; the message may already exist in Sent.

Multi-recipient sends go as **one message**; success requires every intended
address in the To header. A single-recipient message never counts as a
both-recipient send.

### Sender identity

The newsletter sends with its own display name — `From: Kiwi <address>` —
set per-send via `--from-name` / `--from-email`. This keeps the newsletter's
brand separate from the operator's other email identities. Verified
2026-09-28: the Gmail API preserves the display name in the From header for
the authenticated user's own address.

## Repo layout

```
pipeline/           the whole system: run.py, send_hardened.py, template.py,
                    calendar_context.py, weather_hook.py, approval_watch_logic.py
pipeline/tests/     110-test suite (run: python3 -m unittest discover -s tests)
verify.py           deterministic event gates
docs/               RUNBOOK.md (operator), AUDIT.md (design audit),
                    approval_watch.md (the verifier job)
README.md           this file
SETUP.md            adapt it for your own family
ROADMAP.md          where this goes next (website + paid membership)
```

## History

Built 2026-09 for the Rey family in Englewood, Colorado. Originally ran on
Tasklet.ai; migrated to Meta Muse (agent "K3") on 2026-09-26, which is now
the sole sender. Production record: 43 verified events, 44/44 links
resolved, zero verification failures on the first Muse-run edition.

## License

TBD — currently private. Shared with friends and family for personal use.
