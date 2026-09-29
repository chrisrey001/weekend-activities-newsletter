# SETUP — adapt Kiwi's Corner for your own family

This guide is for friends and family running Meta Muse. You'll copy this
repo, change the config surface, and have your agent send your first edition
within a week.

## Prerequisites

- **Meta Muse** (the agent runs the whole pipeline — research included)
- **Gmail connected** in Muse (the agent sends through your Gmail)
- **Google Calendar** (optional but recommended — powers the scheduling notes)
- The ability to create **scheduled jobs** (crons) in Muse

## Step 1 — Copy and configure

Clone this repo, then change the config surface. Everything family-specific
lives in a handful of places:

| What | Where | Example default |
|---|---|---|
| Home location (drive times) | research brief + `template.py` | Englewood, CO |
| Recipients | `--recipient` flag | `chris.rey001@gmail.com, kndufour@gmail.com` |
| Sender display name | `--from-name` / `--from-email` | `Kiwi` |
| Event categories | `template.py` | family-friendly / date night / can't-miss |
| Source list | research brief (Layer 1 + 2) | Denver publications, venues, ticketing sites |
| Kid labels | research brief | toddler-friendly ("Ava-friendly") labels |
| Footer | pre-flight gate | "Kiwi's Weekend Guide" |

Rename the newsletter itself in the subject line template and footer.

## Step 2 — Teach your agent the Thursday run

Your agent's Thursday job, in order:

1. `python3 pipeline/run.py --run-date YYYY-MM-DD` → prints the research
   brief, writes `runs/<date>/research-brief.md`, stops.
2. Research per the brief (three layers), writing structured event records.
3. Re-run with `--events-json <file>` → verifies, renders, pre-flights,
   sends through `send_hardened.py`.
4. Confirm: status `sent` **with a message ID**, verified in Sent.

The full operator procedure is `docs/RUNBOOK.md`. The agent should read it
before the first production run.

## Step 3 — Dry run first

```bash
python3 pipeline/run.py --run-date YYYY-MM-DD --events-json events.json --dry-run
```

Dry runs exercise every gate except the actual dispatch. **Never send real
email during testing.**

## Step 4 — Schedule the three jobs (America/Denver shown; use yours)

| Job | When | Does |
|---|---|---|
| Production run | Thursday ~8:00 AM | research → verify → render → send |
| Approval watch | Thursday ~8:45 AM | confirms the send landed in Sent; alerts you if not |
| Watchdog | Thursday ~9:30 AM | confirms the watch ran; alerts if anything is off |

The watch jobs never send or retry — they only verify and alert.

## Step 5 — The first send and approvals

The first production send will surface a Gmail approval card. Approve it.
For a hands-free Thursday, grant the standing ("always allow") permission
for this workflow's recipients afterward — ask your agent how; it's in
Muse's Gmail connector settings.

## What "good" looks like

- 40–65 verified events (a smaller strong list beats padding)
- Every event: confirmed date for *that* weekend, confirmed price, working
  event-specific link
- Drive times from your home, concise descriptions, chronological within
  each day
- A fresh weekend summary grounded in the actual lineup, the weather, and
  your calendar — not a template paragraph
- One message, all recipients in To, one Gmail message ID, Sent-folder
  confirmation

## Troubleshooting

- **Run timed out** — the research phase is the long pole. Cap research
  time or shrink the source list; the one-hour execution window is real.
- **"failed", no message ID** — check Gmail Sent *first*. If the message is
  there, it sent (approval released late); if not, check the approval card.
  Never blind-retry.
- **Approval card every week** — the standing permission wasn't granted for
  all recipients, or expired. Re-grant it.
- **Stale events slipping through** — tighten Layer 3: every recurring event
  needs `operator_confirmed=true` + `operator_url` from the operator's own
  current listing.

Run the test suite after any change: `python3 -m unittest discover -s tests`
from `pipeline/`. 110 tests, all green is the bar.
