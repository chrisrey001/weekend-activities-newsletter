# Kiwi's Corner — Operator RUNBOOK

**For the Thursday cron worker (agent).** The pipeline lives in
`~/workspace/kiwis-corner/pipeline/`; the architecture spec is
`~/workspace/kiwis-corner/PLAN.md`. One entrypoint, no date-suffixed files:

```
python3 ~/workspace/kiwis-corner/pipeline/run.py [flags]
```

**Config first.** Every run needs `--config <path>` pointing at your private
`config.yaml` (copy `config.example.yaml` → `config.yaml` on first setup —
see `SETUP.md`). Recipients, sender identity, timezone, metro, home area,
family names, research sources, venue list, broad-search topics, and event
categories all come from that file. Nothing family-specific should be typed
on the command line. `config.yaml` is git-ignored and must never be
committed; credentials never belong in it either — they live in your
agent's credential store, not in YAML or Git.

## 0. Prerequisites

- Python 3 with `verify.py` at `~/workspace/kiwis-corner/verify.py` (deterministic gates).
- `curl` on PATH (pre-flight link check).
- Gmail + Google Calendar connector access for the worker (the pipeline
  itself is pure/deterministic; the worker fetches calendar + weather and
  passes them as JSON files).
- **Do NOT send any real email during testing** — use `--dry-run` until the
  real Thursday run.
- `pipeline/send_hardened.py` must exist (sibling agent's deliverable). If it
  is missing or its interface differs from the contract, `run.py` raises
  `ImportError` LOUDLY at the send stage and stops. There is no fallback
  send path. Never work around this.

## 1. The run, step by step

### Step 1 — Emit the research brief

```
python3 ~/workspace/kiwis-corner/pipeline/run.py --config ~/workspace/kiwis-corner/config.yaml [--run-date YYYY-MM-DD]
```

With no `--events-json`, run.py prints the research brief to stdout and
writes `runs/<run-date>/research-brief.md`, then stops. The brief tells you
exactly what to research for this week's Fri–Sun.

### Step 2 — Research (agent-executed, per the brief)

Three layers, from the brief (sources and venues come from your config —
the brief lists exactly which ones to check):

1. **Layer 1 — general sources** (local press, weekend roundups, event
   roundups, ticketing sites). Log each as ok / partial / failed with a
   one-line note.
2. **Layer 2 — venue sweep** (the configured venue list). Per-venue health +
   every stale/cancelled/duplicate drop documented with its reason.
3. **Layer 3 — broad searches + big-ticket follow-ups** (the configured
   broad-search topics; sports schedules, major concerts/festivals) to pin
   exact dates, times, prices, URLs.

**The date-verification rule is non-negotiable:** every event's date verified
against an explicit current listing; recurring events MUST record
`stated_weekday` as the source states it, and MUST carry
`operator_confirmed=true` + `operator_url` (confirmed on the operator's own
site/official social, current season). Stale-only sourcing is a hard reject.
Every event MUST have a confirmed price — drop it (with reason) if you can't
verify one. Use event-specific URLs only; reject 403/404/DNS-stub pages as
sources (403s from AXS/SeatGeek/CPR are acceptable only when content-verified
another way and the verification is recorded in the sources).

Write `runs/<run-date>/events.json` with the exact schema from the brief:
`{weekend: [fri, sat, sun], generated, events[], dropped[], sources_ok[],
sources_failed[]}`. Target 40–65 verified events (the pipeline halts below 40).

### Step 3 — Write the bespoke header + picks

- **Header** (`header.html`): a personalized paragraph written fresh each
  week — big tickets, weather-driven picks, Ava-friendly standouts, calendar
  context (nanny schedule, visitors, travel). Never a static template line.
- **Picks** (`picks.json`): `{"picks": [{"name", "detail", "url"}, ...]}` —
  the can't-miss picks, each URL event-specific.

### Step 4 — Fetch calendar + weather JSON (optional but recommended)

```bash
# Calendar events for the Fri-Sun window (primary calendar):
hatch_gws_cli calendar events list --params \
  '{"calendarId":"primary","timeMin":"<fri>T00:00:00-06:00","timeMax":"<sun>T23:59:59-06:00"}'
# Save as {"events":[{"title","start","end","all_day","description"}, ...]}
#  - timed:  start/end "YYYY-MM-DDTHH:MM"; all-day: start/end "YYYY-MM-DD", all_day true

# Weather: save the injected forecast as
# {"2026-09-18":{"precip_prob":65,"high_f":82,"summary":"Rain"}, ...}
```

The pipeline classifies calendar events deterministically into three tiers
(`calendar_context.py`): **all-day → informational** (never suppresses
anything), **family-keyword timed → context notes** (nanny, ava, travel,
flight, visitor(s), guest, birthday, anniversary, school, camp, daycare,
potty), **other timed → hard conflicts** (only these can flag a guide event,
and only when the time windows actually overlap). The weather hook
(`weather_hook.py`) only supplies deterministic facts (rain-risk days ≥50%
precip); curating indoor picks stays your judgment call.

### Step 5 — Run the deterministic pipeline

```bash
# Full dry run (everything except the real Gmail dispatch):
python3 ~/workspace/kiwis-corner/pipeline/run.py \
  --config ~/workspace/kiwis-corner/config.yaml \
  --events-json runs/<run-date>/events.json \
  --header-html /tmp/header.html \
  --picks-json /tmp/picks.json \
  --calendar-json /tmp/calendar.json \
  --weather-json /tmp/weather.json \
  --dry-run

# Production (drop --dry-run). Recipients and sender identity come from the
# config file — the send goes to every configured recipient in ONE message:
python3 ~/workspace/kiwis-corner/pipeline/run.py \
  --config ~/workspace/kiwis-corner/config.yaml \
  --events-json runs/<run-date>/events.json \
  --header-html /tmp/header.html \
  --picks-json /tmp/picks.json \
  --calendar-json /tmp/calendar.json \
  --weather-json /tmp/weather.json
```

Flags: `--config` (required; your `config.yaml`), `--recipient`,
`--from-name`, `--from-email` (rare overrides of the config values),
`--run-date` (override; default today in the configured timezone),
`--run-dir` (override run dir), `--dry-run`.

## 2. How the gates behave

| Stage | What it does | On failure |
|---|---|---|
| **verify** | Runs `verify.py` (6 deterministic gates: required fields, in-weekend, weekday, freshness, operator, dedupe). Non-zero exit → **halt**. | `PIPELINE HALTED` — fix `events.json` and re-run. Never hand-edit past it. |
| **floor** | ≥40 events or halt. | Research more; re-run. |
| **render** | `template.py` → `runs/<run-date>/email.html`. Missing/blank price on ANY event → hard FAIL. Blank header → hard FAIL. Footer must read the configured footer brand. | Fix the data/header; re-run. |
| **pre-flight** | Placeholder scan (no TODO/XXX/FIXME/lorem/`{{tokens}}`); curl link check of every unique URL (403 allowed only for AXS/SeatGeek/CPR, which must be content-verified at research time); ≥40 floor; footer brand check. | `PIPELINE HALTED: PRE-FLIGHT FAILURES` — fix and re-run. |
| **send** | Lazy-imports `send_hardened`; validates the contract; calls `send_newsletter(...)`. Statuses: `sent` / `already_sent` / `failed` / `dry_run`. Unknown status → halt. | See §3. A `failed` dispatch writes the run log, then the pipeline raises `PipelineHalt` (non-zero exit) — a failed send never exits 0. |
| **log** | Writes `runs/<run-date>/run-log.md` (per-source health, counts, verify output, calendar, weather, pre-flight, dispatch result verbatim, idempotency key = `<run-date>::<recipient>`). | The log self-checks the 2026-09-17 incident rule (below) and refuses to write a lying log. |

**The 2026-09-17 incident rule:** production run #1's log claimed a send that
never happened. The send module returns `sent` ONLY when the Gmail API
returned a message ID **and** a post-send search confirmed it. `run.py` marks
`sent` in the log **only** when `SendResult.status == "sent"`, and the log
builder regex-checks this: the standalone word "sent" appears in `run-log.md`
**only** for a true `sent`. For `failed`/`already_sent`/`dry_run` the log uses
"dispatch" wording and records the result verbatim. If the sibling's failure
`notes` ever contain the standalone word "sent", the builder raises rather
than write an ambiguous log — phrase failure notes accordingly.

## 3. Failure modes — what to do

- **verify FAIL** → read the gate output, fix `events.json` (drop bad records
  into `dropped` with reasons, or correct them), re-run the full command.
- **pre-flight FAIL** → fix placeholders / dead links / missing prices /
  footer, re-run. Link failures: replace the URL with the operator page or
  drop the event.
- **Approval-wait (send parked on a connector approval)** → the ~8:45
  approval-nudge check (PLAN.md incident fix) pings Chris; do not declare
  success until `SendResult.status == "sent"`.
- **send `failed`** → do NOT retry blindly. Check Gmail's sent mail first for
  the subject+recipient+today (the module already does this pre-send and
  returns `already_sent` if found). If nothing is there, diagnose from the
  verbatim notes, fix the cause, re-run.
- **`already_sent`** → the newsletter is already out; stop. Do not re-send.
- **Missing/differing `send_hardened.py`** → loud `ImportError`, pipeline
  stops before any send attempt. Escalate to the send-module owner; never
  route around it.

## 4. Sender policy

The send goes to **every recipient listed in the config file**, as ONE
message. K3 is the sole sender (the old Tasklet parallel-run arrangement
ended 2026-09-26 — it is retired; do not ask about it). A run counts as a
success only if Gmail Sent shows one message with all configured recipients
in To, a concrete message ID, and a matching run-log entry. `--recipient`
overrides the config for testing only; production runs use the config.

## 5. Tests

```
cd ~/workspace/kiwis-corner/pipeline && python3 -m unittest discover -s tests
python3 ../verify.py --self-test   # the City Park incident regression
```

Tests cover: the 3-tier calendar classification + overlap helper, the weather
hook, template invariants (chronological order, price hard-FAIL, footer
brand, multi-day consolidation), weekend computation, brief mode, verify-gate
halting, send-contract validation (missing/wrong module → loud ImportError;
unknown status → halt), and the run-log "sent"-word integrity rule.
