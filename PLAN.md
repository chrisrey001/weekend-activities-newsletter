# Kiwi's Weekend Guide — Project Plan (public template)

A parameterized pipeline that researches a weekend's events (Fri–Sun),
verifies them, and renders a branded HTML email in one or more editions.
Bring your own family profile, your own Gmail, your own cron.

## What this is

- **Research**: a 3-layer method (general sources → venue sweep → broad
  searches → targeted "big ticket" follow-ups) with a non-negotiable
  date-verification rule: every event's date, time, price, and URL is
  confirmed against its source before it ships.
- **Enrich**: calendar-conflict flags (optional, per-profile feature flag),
  weather-based indoor picks, drive-time annotations from your home area.
- **Render**: HTML email from a theme-driven template (inline styles only, dark design, mobile-first).
- **Send**: via Gmail with a verified-send gate — a send only counts with a
  concrete Gmail message ID, Sent-folder confirmation, the correct address
  in the To header, and a matching run-log entry. Never record "sent"
  without proof.
- **Source health**: a file-based source scorecard (`runs/index.json`)
  tracks per-source event counts across weeks and proposes replacements
  for stale sources in each research brief.

## Editions

One shared research pass can feed multiple editions from different configs:

- **Private edition** (`config.yaml`, git-ignored): your household profile,
  calendar integration on, drive times from your address.
- **Public/friends edition** (`config.public.yaml`): no names, no calendar
  access, neutral drive-time language, a `Reply STOP to unsubscribe.`
  footer line, and no private dashboard links. Each subscriber gets an
  **individual** message — never group subscriber addresses in one To line.

`features.calendar_integration` is the per-profile switch. When false, any
supplied `--calendar-json` is ignored and no conflict flags are applied.

## Pipeline stages (`pipeline/run.py`)

1. **Brief**: prints the research brief and stops (agent follows it).
2. **Research**: write `runs/<run-date>/events.json` (40+ verified events;
   every event needs a confirmed price and an event-specific URL).
3. **Header + picks**: a bespoke header paragraph written fresh each week
   (big tickets, weather-driven picks, kid-friendly standouts) — never a
   static template line — plus a can't-miss picks list.
4. **Enrich**: calendar-conflict flags (if enabled for the profile),
   weather facts.
5. **Render**: theme-driven HTML email (see `pipeline/template.py`).
6. **Pre-flight**: placeholder scan, link check, 40+ count floor, footer
   branding check, depersonalization check on public editions.
7. **Send**: Gmail, per-recipient approvals, Sent-first idempotency.
8. **Log**: run-log entry per send.

## Configuration

See `config.example.yaml` (a neutral sample family) and `SETUP.md`.
Key surfaces:

- `newsletter.public_edition`: true → neutral copy, STOP footer, no
  dashboard link.
- `features.calendar_integration`: false → calendar inputs ignored.
- `theme`: brand colors, fonts, footer text, labels.
- `recipients`: household recipients (private edition). Public editions
  take one `--recipient` per send.

## Testing

`python3 -m pytest pipeline/tests/` — the suite covers the send gate,
calendar tiers, config validation, template rendering (dark design),
the public/private footer split, and the calendar feature flag.

## Roadmap

See `ROADMAP.md` — from one family's newsletter toward a small platform
(onboarding, per-family runs, paid membership) when demand is proven.
