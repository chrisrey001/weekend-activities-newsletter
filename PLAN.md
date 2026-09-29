# Kiwi's Corner — Migration Plan (K3 takeover from Tasklet "Scout")

_Last updated: 2026-09-10. Status: plan approved in principle; mockups requested; build on hold pending mockup review._

## Objective
Migrate the Thursday-morning weekend-events newsletter from the Tasklet "Scout" agent to K3,
fix the known fragilities found in review, add the agreed upgrades, run 1–2 weeks in parallel,
then disable Tasklet and cancel the subscription.

## Current state (Tasklet)
- Agent "Scout" sends **Kiwi's Weekend Guide** every Thursday ~8:00 AM MT.
- Recipients: chris.rey001@gmail.com and kndufour@gmail.com (both stay).
- ~40–65 verified events for Fri–Sun, tagged 👨‍👩‍👧 family-friendly / 🍷 date night / ⭐ can't-miss,
  with drive times from Englewood and one-line descriptions. Personalized (mentions Ava).
- 23 logged runs (Apr–Sep 2026); pipeline generally healthy.
- Full Tasklet export + audit: `~/workspace/user/files/scout-review/`.

## Decisions (Chris, 2026-09-10)
- Keep both recipients. Kristen is an existing subscriber — not new outreach.
- No dedicated Gmail address (cosmetic nice-to-have only; skip unless trivial).
- Keep Chris's manually compiled source list; expand only where data shows a real gap.
- Email: evolve formatting, don't redesign. Fix footer branding bug ("K2 Weekend Rundown" → "Kiwi's Weekend Guide"),
  improve mobile rendering, add Ava-friendly callouts. Mockup requested.
- Command center: visual health dashboard mockup requested.
- Structured extraction: typed event records → template render → automated pre-send checks.
  Flat JSON files for now; graduate to MongoDB if/when it outgrows files (cross-week queries, API consumers).

## Review findings (what changes and why)
Keep:
- 3-layer research design: 11 general sources → 18-venue sweep → 14 broad searches → targeted "big ticket" follow-ups.
- Date-verification rule (non-negotiable; born from a real wrong-date incident).
- Inline-styles HTML email template (correct for email clients), tagging/color system.

Fix:
1. **Hand-authored build step** — all 53 events re-typed into the email script weekly; a single misread ships.
   → Structured event records first, template rendered from data.
2. **~57 copy-paste scripts** (date-suffixed); fixes never propagate (dead Marquis Theater URL failing for weeks).
   → One parameterized pipeline; dates computed from run time.
3. **Silent scrape failures** — 403/404/DNS-stub pages logged as OK; ~40% of venue scrapes returned stubs with no signal.
   → Content-aware validation; per-source health logged.
4. **Silent truncation** in consolidate step (25k-char slice, no marker).
   → Truncation markers + chunked processing.
5. **Footer branding bug** shipped weekly.
   → One-line fix in template.
6. **No failure alerting** — Gmail auth expired twice; newsletter silently never sent.
   → Watchdog check ~9:30 AM Thursdays verifies the send; alerts Chris in chat on failure.
   → Idempotency key per week prevents double-sends.

## K3 architecture
- **One cron job**: `kiwis-corner-weekly`, Thursdays ~8:00 AM America/Denver.
- **Single parameterized pipeline** (no date-suffixed anything):
  1. Compute Fri–Sun dates from run time.
  2. Fetch: 11 general sources + 18-venue sweep + 14 broad searches + targeted big-ticket searches.
  3. Validate: content-aware checks (reject 403/404/DNS stubs); log per-source health.
  4. Extract: typed event records → `runs/YYYY-MM-DD/events.json`.
  5. Verify: `python3 verify.py runs/<date>/events.json` — DETERMINISTIC gate, no LLM
     judgment. Exits non-zero on any FAIL and the pipeline halts before
     render/send. Enforced by code (added 2026-09-13, regression-tested
     against the City Park incident):
     - C1 in_weekend: event date falls within the run's Fri–Sun.
     - C2 weekday: computed weekday of the assigned date must match the
       source's stated weekday (e.g. source says "Saturdays" → date must be
       a Saturday); mismatches drop the event. Recurring events MUST record
       `stated_weekday` — a missing value is a FAIL, not a skip.
     - C3 freshness: recurring events (markets, festivals, series) need at
       least one current-season source — published within 365 days, excerpt
       mentions the season year, or the source is the operator's own domain
       (with operator confirmation attested). Stale-only sourcing is a hard
       FAIL. (Audit note: this gate, not the weekday check, is what catches
       the City Park class of error — there the stale source and the date
       agreed on Sunday.)
     - C4 operator: recurring events require `operator_confirmed=true` plus
       `operator_url` — day/time confirmed on the operator's own site or
       official social. The code enforces that the attestation exists and is
       logged; it cannot verify the researcher's claim is truthful.
     - C5 dedupe: duplicate source URLs collapse to the first record.
     New required event-record fields: `recurring` (bool), `stated_weekday`,
     `sources[]` ({url, retrieved_at, published_at?, excerpt?}),
     `operator_confirmed` + `operator_url` (recurring only), `date_end?`
     (multi-day events). Self-test: `python3 verify.py --self-test`.
  6. Enrich: calendar-conflict flags (from Chris's Google Calendar), weather-based indoor picks.
  7. Render: HTML email from template (inline styles only).
  8. Pre-flight: placeholder scan, link check, 40+ count floor, footer branding check.
  9. Send via Gmail to both recipients.
  10. Log run + refresh command-center data.
- **Watchdog**: separate check ~9:30 AM Thursdays; on failure, message Chris in the Kiwi's Corner chat.
- **Command center**: web dashboard (status, event-count trend, per-source health grid, error log, coverage %),
  data refreshed each run. Mockup built 2026-09-10 (artifact slug: `command-center-mockup`).
- **Run log schema**: `{ week, event_count, status, coverage_pct, sources_ok, sources_failed, notes }`.
- **Storage**: `~/workspace/kiwis-corner/runs/` (JSON). MongoDB later if justified.

## Email restyle direction
- Keep structure: Friday / Saturday / Sunday / All Weekend / Can't-Miss Picks.
- Mobile-first responsive tweaks (most reading happens on phones); keep inline styles for client compatibility.
- Fix footer branding. Add "Ava-friendly" callout on toddler-suitable events.
- Mockup built 2026-09-10 (artifact slug: `email-restyle-mockup`) — Chris approved, no changes.
- **Per-day events in chronological order; every event shows price; links verified accurate (link check in pre-flight).**
- **Bespoke header paragraph every week: a personalized, written-fresh summary of that weekend** (big tickets, weather-driven picks, Ava-friendly standouts) — never a static template line.

## Migration sequence
1. ✅ Review delivered (2026-09-10).
2. Mockups: restyled email sample + command center dashboard (both approved 2026-09-10).
3. **Test run for the weekend of Fri Sep 11 – Sun Sep 13, requested 2026-09-10**: one-off dry run of the full research → render path, delivered to Chris in chat only (no email send; Tasklet still owns the real Thursday send).
   - ✅ 2026-09-10: Chris asked for a full end-to-end test send; approved sending to **chris.rey001@gmail.com only** (Kristen already had Tasklet's real send that morning). Test email sent successfully; Gmail send scope granted. Chris: "It looks great."
4. Build durable parameterized pipeline on K3 stack (week of Sep 14); fix calendar-conflict rule (distinguish hard time conflicts vs family/context notes vs all-day informational events).
5. **Production run #1 — Thu Sep 17** (LOCKED 2026-09-10): K3 runs the full production pipeline ~8:00 AM MDT and sends the real newsletter to **Chris only**; Tasklet continues its normal send to both recipients as the safety net (avoids double-sending Kristen). Watchdog verifies the K3 send ~9:30 AM and alerts Chris in chat on failure.
   - Crons (owner: goal:k3-migration-proposal): `kiwis-corner-production-run` (runonce 2026-09-17T08:00 America/Denver), `kiwis-corner-watchdog` (runonce 2026-09-17T09:30 America/Denver).
6. If the Sep 17 run lands clean, Chris cancels Tasklet the same day; from Thu Sep 24 K3 is the sole sender to both recipients.
   - **Incident 2026-09-17:** production run #1 completed research/render (72 verified events) but the Gmail send never executed — it parked on an unanswered connector approval from 08:50 MDT while the run log falsely recorded "sent once" (no message ID). Watchdog caught it at 9:30; Tasklet's send covered the family. Root cause: no verified-send gate. **Fix required before Sep 24:** (a) send step must capture the Gmail API message ID and re-verify in sent mail before the run may report success — never record "sent" without proof; (b) add an ~8:45 approval-nudge check that pings Chris if the send is parked on an approval, instead of discovering it at 9:30.

## Open items
- [x] Approve restyled email mockup (approved 2026-09-10; bespoke weekly header paragraph required).
- [x] Approve command center mockup (approved 2026-09-10).
- [x] Confirm Thursday 8:00 AM MT send time stays (confirmed 2026-09-10 as part of the locked Sep 17 plan).
- [ ] Decide: keep "Kiwi's Weekend Guide" name (yes, per branding fix).
