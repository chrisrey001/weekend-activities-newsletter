# Maple Street Weekend -- Research Brief
Run date: 2026-09-29 (Tuesday) · Weekend: Friday, October 2 - Sunday, October 4
Weekend dates: 2026-10-02 (Fri), 2026-10-03 (Sat), 2026-10-04 (Sun)

You are the research worker for this week's Maple Street Weekend newsletter. The
downstream pipeline (verify -> render -> send) is fully deterministic; your
job is to produce a correct, complete `events.json`. Follow the 3-layer
method and the non-negotiable date-verification rule exactly.

## Layer 1 -- general sources
Sweep each source's Chicago weekend coverage for the 2026-10-02-2026-10-04 window:
timeout.com/chicago (weekend roundup), choosechicago.com, chicagoreader.com, do312.com, eventbrite.com (Chicago), allevents.in (Chicago), secretchicago.com, axs.com (Chicago), ticketmaster.com (Chicago).
Keep the established source list; expand only where the data shows a
real gap. Log per-source health: ok / partial / failed + one-line note.

## Layer 2 -- venue sweep
Check the direct calendars of the established venue list:
Chicago Theatre, House of Blues Chicago, Thalia Hall, Metro Chicago, Millennium Park, Lincoln Park Zoo.
Record per-venue health (ok / partial / failed + note);
document every stale/cancelled/duplicate drop with its reason.

## Layer 3 -- broad searches + big-ticket follow-ups
Broad searches (pro sports schedules (Bears, Bulls, Blackhawks, Cubs, White Sox), major concerts and festivals, free events, family events, date-night options), then targeted follow-ups on big-ticket items
(major concerts, festivals) to pin down exact dates, times, prices, and
event-specific URLs.

## Date-verification rule (NON-NEGOTIABLE)
Born from a real wrong-date incident. Every event's date must be verified
against an explicit, current listing before it enters events.json:
- The listing must state the date (or the weekday + date range the record
  falls in) for THIS weekend -- never infer from a generic "Saturdays" page
  without confirming this specific date.
- Recurring events (markets, festivals, series) MUST record `stated_weekday`
  exactly as the source states it, and MUST be confirmed on the operator's
  own site or official social for the current season: `operator_confirmed=true`
  plus `operator_url`. Stale-only sourcing (old blog posts, defunct markets)
  is a hard reject.
- Content-aware validation: reject 403/404/DNS-stub pages as sources. A 403
  from a known bot-protection host (AXS, SeatGeek, CPR) is acceptable ONLY
  when the page was content-verified another way (search index, official
  social, operator page) and the verification is recorded in the sources.

## Event record schema (ALL fields; the verifier enforces the marked ones)
Each event is a JSON object with:
- name (str, required by verifier) -- canonical display name.
- date ("YYYY-MM-DD", required) -- must fall within 2026-10-02..2026-10-04.
- date_end ("YYYY-MM-DD", optional) -- multi-day events only.
- venue (str, required) -- venue or neighborhood name.
- address_or_area (str) -- street/area, e.g. "Larimer Street, downtown Chicago".
- start_time (str, e.g. "7:30 PM"; null -> rendered as "See listing").
- url (str, required) -- the EVENT-SPECIFIC page (never a generic discover
  page). Verified accurate: pre-flight curls every unique URL.
- price (str, REQUIRED -- missing/blank price is a hard pipeline FAIL).
  Use "Free" for free events. If no price can be confirmed, DROP the event
  and record it in `dropped` with the reason.
- description (str) -- one line, factual.
- tags ([]) -- subset of ["family-friendly", "date-night", "cant-miss"].
- kid_friendly (bool) -- true for toddler-suitable events (Riley is ~2).
- drive_time_from_home (str, e.g. "~25 min") -- driving time from Logan Square.
- recurring (bool, required) -- true for markets/festivals/series.
- stated_weekday (str, required when recurring) -- the weekday the SOURCE
  claims, e.g. "Saturday". The verifier fails the event if the assigned
  date's weekday does not match.
- sources (list, required, non-empty): [{"url", "retrieved_at"
  ("YYYY-MM-DD"), "published_at" (optional), "excerpt" (optional)}].
- operator_confirmed (bool; required true when recurring).
- operator_url (str; required when recurring) -- operator's own page.

## Output contract
Write runs/2026-09-29/events.json:
{
  "weekend": ["2026-10-02", "2026-10-03", "2026-10-04"],
  "generated": "<ISO timestamp>",
  "events": [ ...records above... ],
  "dropped": ["<name> (<date>, <layer>) -- <reason>", ...],
  "sources_ok": ["layer1:<a source>", ...],
  "sources_failed": ["layer1:<a source>", ...]
}
Target: 50 verified events (floor 40 -- the pipeline halts below it).

## Then resume the pipeline
After events.json is written, run:
    python3 /home/hatch/workspace/kiwis-corner-export/pipeline/run.py --config <your config.yaml> --events-json runs/2026-09-29/events.json         --header-html <bespoke-header.html> [--picks-json ...]         [--calendar-json ...] [--weather-json ...] [--dry-run]
The bespoke header paragraph (personalized, written fresh: big tickets,
weather-driven picks, Riley-friendly standouts, calendar context) and the
can't-miss picks list are written fresh each week and passed as files.
