#!/usr/bin/env python3
"""Kiwi's Corner -- the single parameterized pipeline entrypoint.

One cron job, one script, no date-suffixed anything. The worker (an agent)
runs research first (see the research brief below), then invokes the
deterministic stages:

    # 1) Research briefing: emits what the cron worker must do, then stops.
    python3 run.py

    # 2) Deterministic pipeline after research produced events.json:
    python3 run.py --events-json runs/2026-09-17/events.json \\
                   --header-html /tmp/header.html \\
                   --picks-json runs/2026-09-17/picks.json \\
                   --calendar-json runs/2026-09-17/calendar.json \\
                   --weather-json runs/2026-09-17/weather.json \\
                   [--recipient chris.rey001@gmail.com] [--dry-run]

Stages: weekend dates -> verify (verify.py, deterministic) -> enrich
(calendar_context + weather hook) -> render (template.py) -> pre-flight
(placeholder scan, link check, 40+ floor, footer brand) -> send
(send_hardened, per sibling contract) -> run-log.md.

The send sibling's contract (pipeline/send_hardened.py) is imported LAZILY
at the send stage. If the module is missing or its interface differs, the
pipeline raises ImportError LOUDLY -- it never falls back to any other send
path and never records a send without a SendResult.status == "sent".

Log integrity rule (from the 2026-09-17 incident, where the log claimed a
send that never happened): the standalone word "sent" may appear in
run-log.md ONLY when SendResult.status == "sent". build_run_log()
self-checks this with a regex and raises rather than write a lying log.
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
VERIFY_PY = PROJECT / "verify.py"
RUNS_DIR = PROJECT / "runs"
DENVER = ZoneInfo("America/Denver")


sys.path.insert(0, str(HERE))
from config import load_config, theme_from_config

EVENT_FLOOR = 40  # settled requirement: ~40-65 verified Fri-Sun events

# 403s are accepted ONLY from these hosts (known bot-protection), and only
# because research-time content verification is recorded in each event's
# sources / operator attestation (see research brief, date-verification
# rule). Any other non-2xx/3xx response is a hard pre-flight FAIL.
KNOWN_BOT_PROTECTION_DOMAINS = ("axs.com", "seatgeek.com", "cpr.org")

CURL_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
           "AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/126.0 Safari/537.36")


class PipelineHalt(Exception):
    """A deterministic gate failed. The pipeline stops; nothing is rendered,
    sent, or logged as successful."""


# ------------------------------------------------------------------ dates

def compute_weekend(run_date):
    """Next Friday from run_date, then Sat/Sun. A Thursday run covers the
    next three days; generalized per the spec."""
    fri = run_date + timedelta(days=(4 - run_date.weekday()) % 7)
    return [(fri + timedelta(days=i)).isoformat() for i in range(3)]


# ---------------------------------------------------------- research brief

def research_brief_text(run_date, weekend, cfg):
    """Research brief, parameterized by the family config.

    All metro/venue/source/family specifics come from cfg (config.yaml);
    nothing city- or family-specific is hardcoded here.
    """
    fri, sat, sun = weekend
    run_weekday = run_date.strftime("%A")
    fri_l = datetime.strptime(fri, "%Y-%m-%d").strftime("%A, %B %-d")
    sun_l = datetime.strptime(sun, "%Y-%m-%d").strftime("%A, %B %-d")
    nl = cfg["newsletter"]
    loc = cfg["location"]
    fam = cfg["family"]
    src = cfg["sources"]
    target = nl.get("target_events") or 50

    layer1 = ", ".join(src["layer1"])
    venues = ", ".join(src["venues"])
    layer3 = ", ".join(src["layer3"])
    cat_list = ", ".join(f'"{c}"' for c in cfg["categories"])

    kid = fam.get("kid_name")
    if kid:
        age = fam.get("kid_age_years")
        age_note = f" ({kid} is ~{age})" if age else ""
        kid_schema = (f"- kid_friendly (bool) -- true for toddler-suitable "
                      f"events{age_note}.")
        kid_header = f"{kid}-friendly standouts"
    else:
        kid_schema = ("- kid_friendly (bool) -- true for events suitable for "
                      "young children.")
        kid_header = "kid-friendly standouts"

    return f"""# {nl['name']} -- Research Brief
Run date: {run_date.isoformat()} ({run_weekday}) · Weekend: {fri_l} - {sun_l}
Weekend dates: {fri} (Fri), {sat} (Sat), {sun} (Sun)

You are the research worker for this week's {nl['name']} newsletter. The
downstream pipeline (verify -> render -> send) is fully deterministic; your
job is to produce a correct, complete `events.json`. Follow the 3-layer
method and the non-negotiable date-verification rule exactly.

## Layer 1 -- general sources
Sweep each source's {loc['metro']} weekend coverage for the {fri}-{sun} window:
{layer1}.
Keep the established source list; expand only where the data shows a
real gap. Log per-source health: ok / partial / failed + one-line note.

## Layer 2 -- venue sweep
Check the direct calendars of the established venue list:
{venues}.
Record per-venue health (ok / partial / failed + note);
document every stale/cancelled/duplicate drop with its reason.

## Layer 3 -- broad searches + big-ticket follow-ups
Broad searches ({layer3}), then targeted follow-ups on big-ticket items
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
- date ("YYYY-MM-DD", required) -- must fall within {fri}..{sun}.
- date_end ("YYYY-MM-DD", optional) -- multi-day events only.
- venue (str, required) -- venue or neighborhood name.
- address_or_area (str) -- street/area, e.g. "Larimer Street, downtown {loc['metro']}".
- start_time (str, e.g. "7:30 PM"; null -> rendered as "See listing").
- url (str, required) -- the EVENT-SPECIFIC page (never a generic discover
  page). Verified accurate: pre-flight curls every unique URL.
- price (str, REQUIRED -- missing/blank price is a hard pipeline FAIL).
  Use "Free" for free events. If no price can be confirmed, DROP the event
  and record it in `dropped` with the reason.
- description (str) -- one line, factual.
- tags ([]) -- subset of [{cat_list}].
{kid_schema}
- drive_time_from_home (str, e.g. "~25 min") -- driving time from {loc['home_area']}.
- recurring (bool, required) -- true for markets/festivals/series.
- stated_weekday (str, required when recurring) -- the weekday the SOURCE
  claims, e.g. "Saturday". The verifier fails the event if the assigned
  date's weekday does not match.
- sources (list, required, non-empty): [{{"url", "retrieved_at"
  ("YYYY-MM-DD"), "published_at" (optional), "excerpt" (optional)}}].
- operator_confirmed (bool; required true when recurring).
- operator_url (str; required when recurring) -- operator's own page.

## Output contract
Write runs/{run_date.isoformat()}/events.json:
{{
  "weekend": ["{fri}", "{sat}", "{sun}"],
  "generated": "<ISO timestamp>",
  "events": [ ...records above... ],
  "dropped": ["<name> (<date>, <layer>) -- <reason>", ...],
  "sources_ok": ["layer1:<a source>", ...],
  "sources_failed": ["layer1:<a source>", ...]
}}
Target: {target} verified events (floor 40 -- the pipeline halts below it).

## Then resume the pipeline
After events.json is written, run:
    python3 {HERE}/run.py --config <your config.yaml> --events-json runs/{run_date.isoformat()}/events.json \
        --header-html <bespoke-header.html> [--picks-json ...] \
        [--calendar-json ...] [--weather-json ...] [--dry-run]
The bespoke header paragraph (personalized, written fresh: big tickets,
weather-driven picks, {kid_header}, calendar context) and the
can't-miss picks list are written fresh each week and passed as files.
"""


# ------------------------------------------------------------------ stages

def stage_verify(events_json, weekend, out_lines):
    """Run verify.py; non-zero exit HALTS the pipeline."""
    out_lines.append(f"verify: {VERIFY_PY} {events_json}")
    if not VERIFY_PY.exists():
        raise PipelineHalt(f"verifier missing: {VERIFY_PY}")
    proc = subprocess.run(
        [sys.executable, str(VERIFY_PY), str(events_json)],
        capture_output=True, text=True, timeout=300)
    out_lines.append(proc.stdout.strip())
    if proc.stderr.strip():
        out_lines.append("verify stderr: " + proc.stderr.strip())
    if proc.returncode != 0:
        raise PipelineHalt(
            f"verify.py FAILED (exit {proc.returncode}) -- pipeline halted "
            f"before render/send. Output:\n{proc.stdout}")
    return proc.stdout


def stage_enrich(events, weekend, calendar_json, weather_json, out_lines):
    """Calendar tiers + weather facts; annotate hard time overlaps."""
    sys.path.insert(0, str(HERE))
    import calendar_context
    import weather_hook

    ctx = {"hard_conflicts": [], "context_notes": [],
           "informational": [], "note": "calendar not provided"}
    if calendar_json:
        raw = json.loads(Path(calendar_json).read_text())
        cal_events = raw["events"] if isinstance(raw, dict) else raw
        tiers = calendar_context.classify(cal_events, {
            "friday": weekend[0], "saturday": weekend[1], "sunday": weekend[2]})
        ctx.update(tiers)
        ctx["note"] = calendar_context.summarize(tiers)
        # Annotate ONLY guide events whose window actually overlaps a hard
        # conflict (3-tier rule: informational/context never flag events).
        flagged = 0
        for e in events:
            if not e.get("start_time"):
                continue
            for c in tiers["hard_conflicts"]:
                if calendar_context.overlaps(c, e):
                    e["conflict"] = True
                    e["conflict_with"] = (
                        f"Overlaps your {_cal_when(c)}: {c.get('title')}")
                    flagged += 1
                    break
        out_lines.append(f"calendar: {ctx['note']}; "
                         f"{flagged} guide event(s) overlap-flagged")
    else:
        out_lines.append("calendar: no --calendar-json supplied; "
                         "no conflict flags applied")

    day_notes = {}
    for c in ctx["context_notes"]:
        d = (c.get("start") or "")[:10]
        if d in weekend:
            day_notes.setdefault(d, []).append(_cal_note(c))

    weather = weather_hook.summarize(weather_json, weekend)
    out_lines.append(f"weather: status={weather['status']}"
                     + (f"; {weather['note']}" if weather["note"] else ""))
    return day_notes, weather, ctx


def _cal_when(c):
    s, e = (c.get("start") or "")[11:16], (c.get("end") or "")[11:16]
    def fmt(t):
        if not t:
            return ""
        h, m = int(t[:2]), t[3:]
        ap = "AM" if h < 12 else "PM"
        h = h % 12 or 12
        return f"{h}:{m} {ap}"
    return f"{fmt(s)}-{fmt(e)}" if e else fmt(s)


def _cal_note(c):
    when = _cal_when(c)
    desc = (c.get("description") or "").strip()
    base = c.get("title") or "Calendar event"
    return f"{base} ({when})" + (f" -- {desc}" if desc else "")


def stage_render(events, weekend, run_dir, header_html_path, picks_json,
                 day_notes, weather, out_lines, theme):
    """Render email.html via template.py. Missing price = hard FAIL."""
    sys.path.insert(0, str(HERE))
    import template

    header_html = Path(header_html_path).read_text().strip()
    picks = []
    if picks_json:
        picks = json.loads(Path(picks_json).read_text())
        picks = picks["picks"] if isinstance(picks, dict) else picks
    doc = template.render_newsletter(
        events, weekend, header_html, picks=picks,
        day_context_notes=day_notes,
        weather_note=weather.get("note") or "", theme=theme)
    out = run_dir / "email.html"
    out.write_text(doc)
    subject = template.subject_for(weekend, theme)
    out_lines.append(f"render: wrote {out} ({len(doc)} chars)")
    return doc, subject


# --------------------------------------------------------------- preflight

_PLACEHOLDER_RE = re.compile(
    r"\bTODO\b|\bXXX\b|\bFIXME\b|lorem|\{\{[^}]*\}\}", re.I)


def _curl_status(url):
    try:
        proc = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-L", "--max-time", "20", "-A", CURL_UA, url],
            capture_output=True, text=True, timeout=30)
        code = (proc.stdout or "").strip()
        return code if re.fullmatch(r"\d{3}", code) else "000"
    except Exception:
        return "000"


def _link_ok(url, code):
    if code in ("200", "201", "202", "203", "204",
                "301", "302", "303", "307", "308"):
        return True, "ok"
    host = urllib.parse.urlparse(url).netloc.lower()
    bot = any(host == d or host.endswith("." + d)
              for d in KNOWN_BOT_PROTECTION_DOMAINS)
    if code == "403" and bot:
        # Accepted ONLY here: research-time content verification for these
        # hosts is recorded in the event sources / operator attestation
        # (date-verification rule in the research brief).
        return True, "403 bot-protection (content-verified at research)"
    return False, f"HTTP {code}"


def stage_preflight(events, weekend, html_doc, out_lines, theme):
    """Placeholder scan, link check, event floor, footer brand. Any failure
    halts the pipeline."""
    failures = []

    # 1) event floor + price presence (data-level; template re-checks price)
    if len(events) < EVENT_FLOOR:
        failures.append(f"event floor: {len(events)} < {EVENT_FLOOR}")
    else:
        out_lines.append(f"preflight floor: {len(events)} >= {EVENT_FLOOR}")
    priceless = [f"{e.get('name')} [{e.get('date')}]" for e in events
                 if not (e.get("price") or "").strip()]
    if priceless:
        failures.append("missing price on: " + "; ".join(priceless))

    # 2) placeholder scan on the rendered email
    hits = sorted(set(_PLACEHOLDER_RE.findall(html_doc)))
    if hits:
        failures.append(f"placeholder tokens in email.html: {hits}")
    else:
        out_lines.append("preflight placeholders: none found")

    # 3) footer branding (check against the unescaped text: the template
    # HTML-escapes the brand, so e.g. "Kiwi's Weekend Guide" appears as
    # "Kiwi&#x27;s Weekend Guide" in the raw HTML)
    sys.path.insert(0, str(HERE))
    import html as _html
    import template as _t
    plain = _html.unescape(html_doc)
    brand = theme["footer_brand"]
    if brand not in plain:
        failures.append(f"footer brand {brand!r} missing")
    elif _t.LEGACY_BUG_BRAND in plain:
        failures.append(f"legacy brand bug present: {_t.LEGACY_BUG_BRAND!r}")
    else:
        out_lines.append(f"preflight footer: {brand!r} present")

    # 4) link check: every unique URL in the final email
    urls = sorted(set(re.findall(r'href="([^"]+)"', html_doc)))
    out_lines.append(f"preflight links: checking {len(urls)} unique URLs")
    results = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for url, code in zip(urls, ex.map(_curl_status, urls)):
            ok, note = _link_ok(url, code)
            results[url] = (code, ok, note)
    bad = {u: r for u, r in results.items() if not r[1]}
    bot403 = [u for u, r in results.items() if r[0] == "403" and r[1]]
    if bad:
        failures.append("link check FAIL: " + "; ".join(
            f"{u} ({r[0]})" for u, r in bad.items()))
    else:
        extra = f"; {len(bot403)} x 403 bot-protection accepted" if bot403 else ""
        out_lines.append(f"preflight links: {len(urls)} checked, "
                         f"all ok{extra}")
    if failures:
        raise PipelineHalt("PRE-FLIGHT FAILURES:\n- " + "\n- ".join(failures))
    return {"urls_checked": len(urls), "bot403_accepted": len(bot403)}


# ------------------------------------------------------------------- send

def load_send_module():
    """Import pipeline/send_hardened.py and validate the sibling contract.

    Raises ImportError LOUDLY if the module is absent or its interface
    differs from the contract. There is no fallback send path.
    """
    sys.path.insert(0, str(HERE))
    import importlib
    import dataclasses
    import inspect
    try:
        mod = importlib.import_module("send_hardened")
    except ImportError as e:
        raise ImportError(
            "pipeline/send_hardened.py is MISSING or not importable. "
            "The send sibling agent has not delivered it yet. The pipeline "
            "refuses to proceed: it never sends silently and has no "
            "fallback send path.") from e
    problems = []
    sr = getattr(mod, "SendResult", None)
    if sr is None:
        problems.append("SendResult dataclass missing")
    else:
        try:
            got = {f.name for f in dataclasses.fields(sr)}
            if got != {"status", "message_id", "notes"}:
                problems.append(f"SendResult fields {sorted(got)} != "
                                "{status, message_id, notes}")
        except Exception as ex:
            problems.append(f"SendResult is not a dataclass: {ex}")
    fn = getattr(mod, "send_newsletter", None)
    if not callable(fn):
        problems.append("send_newsletter missing or not callable")
    else:
        params = inspect.signature(fn).parameters
        for name in ("html_body", "subject", "recipient",
                     "run_id", "dry_run", "gmail"):
            if name not in params:
                problems.append(f"send_newsletter missing parameter {name!r}")
    if problems:
        raise ImportError(
            "pipeline/send_hardened.py interface does not match the sibling "
            "contract -- refusing to proceed:\n- " + "\n- ".join(problems))
    return mod


def stage_send(html_doc, subject, recipient, run_id, dry_run, out_lines,
               sender=None):
    """Call the sibling send module. Returns the SendResult verbatim."""
    mod = load_send_module()
    result = mod.send_newsletter(
        html_body=html_doc, subject=subject, recipient=recipient,
        run_id=run_id, dry_run=dry_run, sender=sender, gmail=None)
    status = getattr(result, "status", None)
    if status not in ("sent", "already_sent", "failed", "dry_run"):
        raise PipelineHalt(
            f"send module returned unknown status {status!r} "
            f"(message_id={getattr(result, 'message_id', None)!r}) -- "
            "treating as a failed send; nothing is recorded as sent.")
    out_lines.append(f"send: status={status} "
                     f"message_id={getattr(result, 'message_id', None)!r}")
    return result


# --------------------------------------------------------------------- log

_SENT_WORD = re.compile(r"(?i)\bsent\b")


def build_run_log(*, run_date, weekend, recipient, dry_run, run_id, events,
                  data, verify_out, calendar_note, weather, preflight,
                  send_result, sender=None, theme=None):
    """Build run-log.md.

    LOG INTEGRITY RULE (2026-09-17 incident): the standalone word "sent"
    appears ONLY when SendResult.status == "sent". Enforced by the regex
    self-check below -- a violation raises instead of writing a lying log.
    Statuses other than "sent" are described with "dispatch" wording.
    """
    fri_l = datetime.strptime(weekend[0], "%Y-%m-%d").strftime("%a %Y-%m-%d")
    sun_l = datetime.strptime(weekend[2], "%Y-%m-%d").strftime("%a %Y-%m-%d")
    n = len(events)
    per_day = {}
    for e in events:
        per_day[e.get("date")] = per_day.get(e.get("date"), 0) + 1

    sys.path.insert(0, str(HERE))
    import template as _t
    by_day, multi = _t.group_events(events)
    displayed = sum(len(v) for v in by_day.values()) + len(multi)

    ok_list = data.get("sources_ok") or []
    fail_list = data.get("sources_failed") or []
    dropped = data.get("dropped") or []

    st = send_result.status
    if st == "sent":
        dispatch = (
            f"Status: `sent`.\n"
            f"- Gmail API returned message ID "
            f"`{send_result.message_id}` and the post-dispatch Gmail search "
            f"confirmed that message ID with today's date and the correct "
            f"recipient.\n"
            f"- Subject: {subject_for_log(weekend)}\n"
            f"- Recipient: {recipient}")
    elif st == "already_sent":
        dispatch = (
            "Status: `already_sent`.\n"
            "- The send module found this run's newsletter already present "
            "in Gmail (subject + recipient + today matched) before dispatch; "
            "no duplicate was created and no new email left the mailbox.")
    elif st == "dry_run":
        dispatch = (
            "Status: `dry_run`.\n"
            "- The send module was invoked with dry_run=True; no email "
            "left the mailbox. Nothing was dispatched.")
    else:  # failed
        dispatch = (
            "Status: `failed`.\n"
            f"- No email left the mailbox. Send module notes (verbatim):\n"
            f"  {send_result.notes}")

    _theme = theme or {}
    log = f"""# {_theme.get("newsletter_name", "Weekend Guide")} -- Run Log
**Run date:** {run_date.isoformat()} · **Weekend:** {fri_l} - {sun_l}
**Recipient:** {recipient} (one message to all recipients)
**Sender:** {sender if sender else "(account default)"}
**Idempotency key:** {run_id}
**Mode:** {"DRY RUN (no dispatch)" if dry_run else "PRODUCTION"}

## Event counts
- Raw verified records: **{n}** (floor gate >= {EVENT_FLOOR}: {"PASS" if n >= EVENT_FLOOR else "FAIL"})
- Displayed in email: **{displayed}** ({n} raw, {len(multi)} consolidated into "All Weekend")
- Per-day raw: {", ".join(f"{d} {c}" for d, c in sorted(per_day.items()))}

## Verification (deterministic)
{verify_out.strip()}

## Source health
- Sources healthy: {len(ok_list)} -- {", ".join(ok_list) if ok_list else "none listed"}
- Sources failed: {len(fail_list)} -- {", ".join(fail_list) if fail_list else "none"}
- Dropped events: {len(dropped)}
{chr(10).join("- " + d for d in dropped) if dropped else ""}

## Calendar context
- {calendar_note}

## Weather
- {weather.get("note") or "no weather data supplied (" + weather.get("status", "") + ")"}
- Rain-risk days: {", ".join(weather.get("rain_days") or []) or "none"}

## Pre-flight
- Placeholder scan: PASS (no TODO/XXX/FIXME/lorem/template tokens)
- Footer brand: "{_theme.get("footer_brand", "")}" present
- Link check: {preflight["urls_checked"]} unique URLs checked, all resolved; {preflight["bot403_accepted"]} x 403 accepted (known bot-protection hosts, content-verified at research time)
- Price on every event: PASS

## Dispatch result
{dispatch}

## Notes
- Pipeline: pipeline/run.py (single parameterized entrypoint; no date-suffixed files).
- Verify gates: required fields, in-weekend, weekday, freshness, operator, dedupe (`python3 verify.py --self-test` for the incident regression).
"""
    if st == "sent":
        if not _SENT_WORD.search(log):
            raise PipelineHalt("log integrity: status is 'sent' but the log "
                               "never says so -- refusing to write")
    else:
        if _SENT_WORD.search(log):
            raise PipelineHalt(
                "log integrity: the word 'sent' appears in the log but "
                f"SendResult.status == {st!r} -- refusing to write a log "
                "that could be read as a completed send (2026-09-17 "
                "incident rule)")
    return log


def subject_for_log(weekend):
    sys.path.insert(0, str(HERE))
    import template as _t
    return _t.subject_for(weekend)


# -------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Kiwi's Corner pipeline: research brief or full "
                    "deterministic run (verify -> enrich -> render -> "
                    "pre-flight -> send -> log).")
    ap.add_argument("--events-json",
                    help="Path to events.json from the research step. "
                         "Omit to emit the research brief and stop.")
    ap.add_argument("--header-html",
                    help="File with the bespoke weekly header paragraph "
                         "(HTML). Required for the deterministic stages.")
    ap.add_argument("--picks-json", help="Can't-miss picks JSON file.")
    ap.add_argument("--calendar-json",
                    help="Google Calendar events JSON for the weekend: "
                         "[{\"title\",\"start\",\"end\",\"all_day\","
                         "\"description\"}, ...] or {\"events\": [...]}.")
    ap.add_argument("--weather-json",
                    help="Injected weather JSON for the indoor-picks hook.")
    ap.add_argument("--config", default="config.yaml",
                    help="Path to config.yaml describing the family, place, "
                         "and newsletter (default: ./config.yaml). All "
                         "family-specific values come from here; CLI flags "
                         "override it.")
    ap.add_argument("--recipient", default=None,
                    help="Newsletter recipient(s), comma-separated. "
                         "Default: email.recipients from the config (one "
                         "message to all of them).")
    ap.add_argument("--from-name", default=None,
                    help="Sender display name for the newsletter only "
                         "(default: email.from_name from the config). Empty "
                         "string keeps the account default.")
    ap.add_argument("--from-email", default=None,
                    help="Sender address for the newsletter (default: "
                         "email.from_email from the config).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run everything except the real Gmail dispatch "
                         "(send module invoked in dry-run mode).")
    ap.add_argument("--run-date",
                    help="Override run date (YYYY-MM-DD). Default: today, "
                         "America/Denver.")
    ap.add_argument("--run-dir",
                    help="Override run directory. Default: "
                         "runs/<run-date>/")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    theme = theme_from_config(cfg)
    tz = ZoneInfo(cfg["location"]["timezone"])
    # The send sibling computes "today" from its module timezone; align it.
    import send_hardened as _sh
    _sh.configure_timezone(cfg["location"]["timezone"])

    recipient = args.recipient or ", ".join(cfg["email"]["recipients"])
    from_name = (args.from_name if args.from_name is not None
                 else cfg["email"]["from_name"])
    from_email = args.from_email or cfg["email"]["from_email"]

    run_date = (datetime.strptime(args.run_date, "%Y-%m-%d").date()
                if args.run_date else datetime.now(tz).date())
    weekend = compute_weekend(run_date)
    run_dir = Path(args.run_dir) if args.run_dir else RUNS_DIR / run_date.isoformat()
    run_dir.mkdir(parents=True, exist_ok=True)

    # ---- brief mode: no --events-json -> describe the research job, stop
    if not args.events_json:
        brief = research_brief_text(run_date, weekend, cfg)
        (run_dir / "research-brief.md").write_text(brief)
        print(brief)
        print(f"\nBrief written to {run_dir / 'research-brief.md'}. "
              "Run research, write events.json, then re-invoke with "
              "--events-json.")
        return 0

    out_lines = [f"{cfg['newsletter']['name']} pipeline run {run_date.isoformat()}",
                 f"weekend: {weekend[0]} .. {weekend[2]}",
                 f"recipient: {recipient}",
                 f"dry_run: {args.dry_run}"]

    # ---- load + floor gate
    data = json.loads(Path(args.events_json).read_text())
    events = data.get("events") or []
    if (data.get("weekend") or []) != weekend:
        raise PipelineHalt(
            f"events.json weekend {data.get('weekend')} does not match the "
            f"computed weekend {weekend} -- refusing to mix weeks.")
    out_lines.append(f"loaded {len(events)} events from {args.events_json}")
    if len(events) < EVENT_FLOOR:
        raise PipelineHalt(f"event floor: {len(events)} < {EVENT_FLOOR} -- "
                            "halting before verify/render/send.")

    # ---- deterministic verify gate
    verify_out = stage_verify(args.events_json, weekend, out_lines)

    # ---- enrich
    if not args.header_html:
        raise PipelineHalt("--header-html is required: the newsletter must "
                            "carry a bespoke, written-fresh header every "
                            "week.")
    day_notes, weather, cal = stage_enrich(
        events, weekend, args.calendar_json, args.weather_json, out_lines)

    # ---- render
    html_doc, subject = stage_render(
        events, weekend, run_dir, args.header_html, args.picks_json,
        day_notes, weather, out_lines, theme)

    # ---- pre-flight gates
    preflight = stage_preflight(events, weekend, html_doc, out_lines, theme)

    # ---- send (lazy import; missing/differing module -> ImportError, loud)
    run_id = f"{run_date.isoformat()}::{recipient}"
    sender = f"{from_name} <{from_email}>" if from_name else None
    send_result = stage_send(html_doc, subject, recipient, run_id,
                             args.dry_run, out_lines, sender=sender)

    # ---- log (integrity-guarded: "sent" only when status == "sent")
    log = build_run_log(
        run_date=run_date, weekend=weekend, recipient=recipient,
        dry_run=args.dry_run, run_id=run_id, events=events, data=data,
        verify_out=verify_out,
        calendar_note=cal.get("note", "calendar not provided"),
        weather=weather, preflight=preflight, send_result=send_result,
        sender=sender, theme=theme)
    log_path = run_dir / "run-log.md"
    log_path.write_text(log)
    out_lines.append(f"log: wrote {log_path}")
    out_lines.append(f"final dispatch status: {send_result.status}")

    print("\n".join(out_lines))

    # 2026-09-17 incident: a send that cannot be verified must HALT loudly
    # (non-zero exit) -- never exit 0 as if the run completed. The run log
    # is written first so the evidence (the verbatim SendResult notes)
    # survives; the cron worker then sees a failure, not a silent success.
    if send_result.status == "failed":
        raise PipelineHalt(
            f"dispatch FAILED: {send_result.notes} -- check Gmail Sent "
            f"before any retry; do NOT retry blindly. Run log: {log_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PipelineHalt as e:
        print(f"PIPELINE HALTED: {e}", file=sys.stderr)
        sys.exit(1)
    except ImportError:
        raise  # loud, with the message from load_send_module()
