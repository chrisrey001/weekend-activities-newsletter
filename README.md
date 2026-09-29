# Weekend Activities Newsletter

A personalized weekend newsletter, researched and sent by an AI agent — and
a reusable template for any family to run their own.

This repo has **two purposes**:

1. **The production framework** for one family (the Reys — Englewood, CO,
   newsletter name "Kiwi's Weekend Guide"). Every Thursday morning an agent
   researches the upcoming Friday–Sunday, verifies 40–65 local events, and
   sends one personalized HTML email to the family: drive times from home,
   confirmed prices, calendar-aware scheduling notes, weather-aware picks,
   and a fresh weekend summary written for that exact lineup.
2. **A template you can copy.** The whole thing is config-driven: copy
   `config.example.yaml` to `config.yaml`, edit your family / city / sources
   (no Python changes), and your agent — Meta Muse, your own personal agent,
   or another AI tool — can send your family's first edition within a week.
   See [SETUP.md](SETUP.md).

## How it works

```
Thursday 8:00 AM            Thursday 8:45 AM          Thursday 9:30 AM
production run ──────► approval watch ──────► watchdog
(research → verify           (did the send            (did the watch
 → render → send)             actually land?)           run? alert if not)
```

### The pipeline (`pipeline/run.py`)

1. **Config** — everything family-specific lives in `config.yaml`:
   newsletter name, recipients, sender identity, timezone, metro, home area,
   family members (and kid age for kid-friendly labels), research sources,
   venue list, broad-search topics, event categories. The example config is
   a neutral sample; the private config is git-ignored and never committed.
2. **Research brief** — `run.py --config config.yaml` emits a brief for this
   week's Fri–Sun, then stops. The agent researches in three layers:
   - Layer 1: general sources (local press, event roundups, ticketing sites)
   - Layer 2: a venue sweep (per-venue health; stale/cancelled/dupes dropped
     with documented reasons)
   - Layer 3: broad searches + big-ticket follow-ups to pin exact dates,
     times, prices, URLs
3. **Deterministic event records** — every event becomes a structured record.
   `verify.py` enforces the non-negotiable gates:
   - date verified against an explicit *current* listing for that exact weekend
   - a **confirmed price** — no price, no event
   - event-specific URLs only; 403/404/DNS stubs rejected
   - recurring events must carry `operator_confirmed=true` + `operator_url`
4. **Calendar context** (`calendar_context.py`) — three tiers:
   - hard timed conflicts (blocks an event)
   - useful family/context notes
   - all-day events (informational only)
5. **Render + pre-flight** (`template.py`) — theme-driven branding: the
   newsletter name, footer brand, family label, home area, and kid badge all
   come from config. Pre-flight scans for placeholders, curl-checks every
   link, enforces the 40-event floor and the footer brand.
6. **Send** (`send_hardened.py`) — Sent-first idempotency: one message to all
   configured recipients; a `sent` status is only ever recorded with a real
   Gmail message ID confirmed in Sent.

### Editions — one research pass, multiple audiences

- **Private edition** (`config.yaml`): your household. Calendar integration
  on, drive times from your address, your private dashboard link in the
  footer.
- **Public/friends edition** (`config.public.yaml`): set
  `newsletter.public_edition: true` and `features.calendar_integration:
  false`. No names, no calendar access, neutral drive-time language, a
  `Reply STOP to unsubscribe.` footer line, and no internal links. Send one
  message per subscriber with `--recipient` — never group subscriber
  addresses in one To line.

### Operator docs

- [`SETUP.md`](SETUP.md) — adapt the template for your own family
- [`pipeline/RUNBOOK.md`](pipeline/RUNBOOK.md) — the Thursday operator
  procedure (for the agent running production)
- [`PLAN.md`](PLAN.md) — architecture spec
- [`ROADMAP.md`](ROADMAP.md) — where this could go next

## Tests

```bash
python3 -m pytest pipeline/tests/ -q   # full suite
python3 verify.py                       # the deterministic gates, standalone
```

Tests never send live email.

## What this is not

- Not a SaaS, not a hosted service — it's a framework you run yourself.
- No credentials in the repo: Gmail/Calendar access lives in your agent's
  credential store; `config.yaml` holds settings, not secrets, and stays out
  of Git.
