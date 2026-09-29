# SETUP — run your own family's weekend newsletter

This guide takes you from zero to your first edition in about a week. It
works whether your agent is Meta Muse, your own personal agent, or another
AI tool — the pipeline is just Python plus an agent that can research the
web and send email.

## What you need

- **An AI agent** that can run shell commands, browse the web, and send
  email through your Gmail (or another mail provider your agent supports).
- **A calendar** the agent can read (optional but recommended — it powers
  the scheduling notes).
- **A way to schedule the agent** weekly (Muse scheduled jobs, a cron on
  your machine, etc.).
- Python 3 and `curl`.

## Step 1 — Copy the repo and make your config

```bash
git clone https://github.com/chrisrey001/weekend-activities-newsletter.git
cd weekend-activities-newsletter
cp config.example.yaml config.yaml
```

Now edit `config.yaml`. Everything family-specific lives here — you should
never need to touch Python:

| Section | What to change |
|---|---|
| `newsletter.name` / `newsletter.footer_brand` | Your newsletter's name and footer |
| `newsletter.target_event_count` | How many events to aim for (40–65 works well) |
| `family.members` / `family.child` | Names, and the child's age (drives kid-friendly labels) |
| `location.metro` / `location.home_area` / `location.timezone` | Your city, neighborhood, and IANA timezone (e.g. `America/Chicago`) |
| `recipients` / `sender` | Who gets the email, and the From name/address |
| `research.general_sources` | Local press, weekend roundups, ticketing sites for your city |
| `research.venues` | Venues you want checked every week (name + calendar URL) |
| `research.broad_search_topics` | Standing searches: pro sports teams, big annual festivals, etc. |
| `events.categories` | The sections of your newsletter (e.g. family-friendly / date night / can't-miss) |

**Finding good sources for your city:** start with your city's alt-weekly,
the local newspaper's events/weekend section, your city's "things to do this
weekend" roundups, and the ticketing sites that list local shows
(Eventbrite, AllEvents, AXS, Ticketmaster). Add the 10–30 venues you
actually go to — theaters, museums, music rooms, parks departments, sports
stadiums. Your agent can help you build this list: ask it to find your
city's best event roundups and venue calendars.

**Config vs. credentials:** `config.yaml` holds *settings*, never secrets.
Gmail/Calendar access stays in your agent's credential store. `config.yaml`
is git-ignored — never commit it. If you fork this repo, your private config
stays on your machine.

**Newer config knobs** (see `config.example.yaml`):

| Key | What it does |
|---|---|
| `newsletter.public_edition` | `true`: neutral copy, no internal footer links, adds `Reply STOP to unsubscribe.` Use for any audience beyond your household. |
| `features.calendar_integration` | `false`: any `--calendar-json` is ignored — no conflict flags, no calendar notes. Keep `false` unless the recipients are your own household. |
| `newsletter.dashboard_url` / `newsletter.dashboard_label` | Optional footer link (private editions only) to your metrics dashboard. Leave blank for no link. |

## Step 2 — Run the research brief

```bash
python3 pipeline/run.py --config config.yaml --run-date YYYY-MM-DD
```

This prints the research brief for that week's Friday–Sunday and stops. (Use
a Thursday date — the newsletter covers the weekend starting the next day.)

## Step 3 — Research (your agent does this)

Hand the brief to your agent. It researches in three layers — general
sources, the venue sweep, big-ticket follow-ups — and writes structured
event records (`events.json`). The non-negotiable rules:

- Every event's date verified against an explicit **current** listing.
- Every event has a **confirmed price** — no price, no event.
- Event-specific URLs only; broken/403/404/stub pages rejected.
- Recurring events need `operator_confirmed=true` + `operator_url`.

Target 40–65 verified events. Aim for quality over padding.

## Step 4 — Write the personal touches

Your agent writes two things fresh each week:

- **`header.html`** — a short personalized paragraph: the big tickets, the
  weather-driven picks, the kid-friendly standouts, what's on the family
  calendar.
- **`picks.json`** — the can't-miss picks, each with an event-specific URL.

## Step 5 — Dry run, then production

```bash
# Everything except the real send:
python3 pipeline/run.py --config config.yaml \
  --events-json events.json --header-html header.html \
  --picks-json picks.json --calendar-json calendar.json \
  --weather-json weather.json --dry-run

# The real thing (drop --dry-run):
python3 pipeline/run.py --config config.yaml \
  --events-json events.json --header-html header.html \
  --picks-json picks.json --calendar-json calendar.json \
  --weather-json weather.json
```

The pipeline verifies → renders → pre-flights (placeholder scan, link
check, 40-event floor, footer brand) → sends **one** message to all
recipients. A send only counts when the mail provider returns a message ID
and it's confirmed in Sent.

## Step 6 — Schedule it

Set your agent to run the full sequence every Thursday morning (8:00 AM in
your timezone is a good default — it gives you the day to react). Add two
follow-ups:

1. **Approval watch** (~45 min later): checks Sent for today's edition with
   all recipients; if the send is parked on an approval, tells you to tap
   approve. Verifies only — never sends or retries.
2. **Watchdog** (~90 min later): confirms the watch ran; alerts if the
   morning chain went quiet.

The full operator procedure your agent should follow is
[`pipeline/RUNBOOK.md`](pipeline/RUNBOOK.md).

## Migrating from an older version

- Event files using `ava_friendly` still render — the template falls back
  to it — but new research should write `kid_friendly` (driven by
  `family.child` in config).
- `drive_time_from_englewood` likewise falls back to the generic
  `drive_time_from_home`.

## Troubleshooting

- **Pipeline halts on verify** → read the gate output, fix `events.json`
  (drop bad records into `dropped` with reasons), re-run.
- **Pre-flight link failures** → replace with the operator's own event page
  or drop the event.
- **`already_sent`** → the edition is already out; stop, don't re-send.
- **Tests**: `python3 -m pytest pipeline/tests/ -q` — they never send email.
