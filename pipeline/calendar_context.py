#!/usr/bin/env python3
"""Deterministic 3-tier calendar classification for Kiwi's Weekend Guide.

Pure function, no network access. Takes the household's Google Calendar events for the
weekend window and sorts them into three tiers the render stage consumes:

  - "hard_conflicts": timed events that are NOT family/context notes. The
    render stage annotates only guide events whose time window actually
    overlaps one of these (see `overlaps`).
  - "context_notes": timed events matching family-context keywords. These are
    useful header/day context (nanny off, travel, visitors) but never flags.
  - "informational": all-day events. They MUST NOT invalidate the day and MUST
    NOT suppress events -- they are shown as plain context only.

Tier rules (evaluated in order; first match wins):

  1. all_day == True            -> informational
  2. timed + family keyword hit -> context_notes
  3. timed, no keyword hit      -> hard_conflicts

Family-context keywords (matched case-insensitively against title and
description, substring match):
    nanny, ava, travel, flight, visitor, visit, guest, birthday,
    anniversary, school, camp, daycare, potty
("visit" covers "visiting"/"visits" as well as "visitor".)

Input event dicts:
    {"title": str, "start": "YYYY-MM-DDTHH:MM" | "YYYY-MM-DD" | None,
     "end": same | None, "all_day": bool, "description": str}

The "weekend" argument is {"friday": "YYYY-MM-DD", "saturday": ...,
"sunday": ...} (a 3-item list [fri, sat, sun] is also accepted). Calendar
events that do not intersect the Fri-Sun window are excluded from the result
entirely (an event is included if any part of its [start, end] date range
touches the window; open-ended events are judged by their start date).

`overlaps(cal_ev, guide_ev, assume_minutes=120)` answers whether a timed
calendar event's window intersects a guide event's window. Guide events carry
{"date": "YYYY-MM-DD", "start_time": "7:30 PM" | "7 PM" | None}. A guide
event with no start_time returns False -- the function cannot assert an
overlap without a start time, and it refuses to guess (documented, not a
silent pass).
"""

import re
from datetime import datetime, timedelta

FAMILY_KEYWORDS = (
    "nanny", "ava", "travel", "flight", "visitor", "visit", "guest",
    "birthday", "anniversary", "school", "camp", "daycare", "potty",
)
# Note: "visit" is included alongside the specified "visitor" so that
# "visiting"/"visits" (the calendar's usual phrasing, e.g. "Jordan
# visiting") also classify as family context. Substring matching means
# "visitor" is covered by "visit" too.

_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([AP])\.?M\.?\.?$", re.I)
_GUIDE_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([AP])\.?M\.?\.?$", re.I)


def _weekend_dates(weekend):
    """Normalize the weekend argument to a set of ISO date strings."""
    if isinstance(weekend, dict):
        dates = [weekend["friday"], weekend["saturday"], weekend["sunday"]]
    else:  # assume an ordered [fri, sat, sun] sequence
        dates = list(weekend)
    if len(dates) != 3:
        raise ValueError(f"weekend must cover exactly 3 days, got {dates!r}")
    return set(dates)


def _date_part(dt_str):
    """Extract the YYYY-MM-DD part of a datetime string (or None)."""
    if not dt_str:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", str(dt_str).strip())
    return m.group(1) if m else None


def _minutes(dt_str):
    """Minutes since midnight for "YYYY-MM-DDTHH:MM[...]" (or None)."""
    if not dt_str:
        return None
    m = re.match(r"^\d{4}-\d{2}-\d{2}T(\d{1,2}):(\d{2})", str(dt_str).strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _guide_minutes(start_time):
    """Minutes since midnight for a guide event start_time like "7:30 PM"."""
    if not start_time:
        return None
    m = _GUIDE_TIME_RE.match(str(start_time).strip())
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).upper()
    if ap == "P" and h != 12:
        h += 12
    if ap == "A" and h == 12:
        h = 0
    if not (1 <= int(m.group(1)) <= 12 and mi < 60):
        return None
    return h * 60 + mi


def _event_touches_weekend(ev, weekend_dates):
    """True if any calendar date of the event intersects the weekend."""
    start_d = _date_part(ev.get("start"))
    end_d = _date_part(ev.get("end"))
    if not start_d:
        return False
    try:
        d0 = datetime.strptime(start_d, "%Y-%m-%d").date()
        d1 = datetime.strptime(end_d, "%Y-%m-%d").date() if end_d else d0
    except ValueError:
        return False
    if d1 < d0:  # defensive: swap inverted ranges
        d0, d1 = d1, d0
    # All-day events from Google use an EXCLUSIVE end date; a timed event
    # that ends at midnight would otherwise be credited with a day it does
    # not occupy. Clamp: if the event is all-day and end > start, treat end
    # as exclusive.
    if ev.get("all_day") and end_d and d1 > d0:
        d1 = d1 - timedelta(days=1)
    day = d0
    while day <= d1:
        if day.isoformat() in weekend_dates:
            return True
        day += timedelta(days=1)
    return False


def _family_hit(ev):
    text = f"{ev.get('title') or ''} {ev.get('description') or ''}".lower()
    return any(kw in text for kw in FAMILY_KEYWORDS)


def classify(calendar_events, weekend):
    """Sort calendar events into the three deterministic tiers.

    Returns {"hard_conflicts": [...], "context_notes": [...],
    "informational": [...]}. Each tier holds the ORIGINAL event dicts
    (copies), in input order. Events outside the weekend window are dropped.
    """
    weekend_dates = _weekend_dates(weekend)
    tiers = {"hard_conflicts": [], "context_notes": [], "informational": []}
    for ev in calendar_events or []:
        if not _event_touches_weekend(ev, weekend_dates):
            continue
        ev = dict(ev)  # never mutate the caller's dicts
        if ev.get("all_day"):
            # All-day events are informational ONLY. They must not invalidate
            # the day or suppress any guide event -- this rule is the
            # 2026-09-14 calendar-conflict fix.
            tiers["informational"].append(ev)
        elif _family_hit(ev):
            tiers["context_notes"].append(ev)
        else:
            tiers["hard_conflicts"].append(ev)
    return tiers


def overlaps(cal_ev, guide_ev, assume_minutes=120):
    """True iff a timed calendar event overlaps a guide event's window.

    - Calendar window: [start, end] from "YYYY-MM-DDTHH:MM". A missing end
      is assumed to be 60 minutes after start. An end earlier than start is
      treated as crossing midnight (24h added).
    - Guide window: [start_time, start_time + assume_minutes] on the guide
      event's date. Guide events with no parseable start_time return False
      (no guessing).
    - Returns False when the dates differ.
    """
    cal_date = _date_part(cal_ev.get("start"))
    guide_date = (guide_ev or {}).get("date")
    if not cal_date or not guide_date or cal_date != guide_date:
        return False
    cal_start = _minutes(cal_ev.get("start"))
    cal_end = _minutes(cal_ev.get("end"))
    if cal_start is None:
        return False
    if cal_end is None:
        cal_end = cal_start + 60
    if cal_end < cal_start:  # crosses midnight
        cal_end += 24 * 60
    guide_start = _guide_minutes((guide_ev or {}).get("start_time"))
    if guide_start is None:
        return False
    guide_end = guide_start + assume_minutes
    return cal_start < guide_end and guide_start < cal_end


def summarize(tiers):
    """One-line counts for the run log."""
    return (f"calendar: {len(tiers['hard_conflicts'])} hard conflict(s), "
            f"{len(tiers['context_notes'])} context note(s), "
            f"{len(tiers['informational'])} informational (all-day)")


if __name__ == "__main__":
    # Ad-hoc smoke check (not the test suite; see tests/).
    wk = {"friday": "2026-09-18", "saturday": "2026-09-19", "sunday": "2026-09-20"}
    evs = [
        {"title": "Catchup with the Crew", "start": "2026-09-18T12:00",
         "end": "2026-09-18T12:45", "all_day": False, "description": ""},
        {"title": "Nanny off", "start": "2026-09-18T08:00",
         "end": "2026-09-18T17:00", "all_day": False,
         "description": "Riley day"},
        {"title": "Anniversary", "start": "2026-09-17", "end": "2026-09-18",
         "all_day": True, "description": "4th anniversary"},
        {"title": "Next Monday thing", "start": "2026-09-21T09:00",
         "end": "2026-09-21T10:00", "all_day": False, "description": ""},
    ]
    t = classify(evs, wk)
    print({k: [e["title"] for e in v] for k, v in t.items()})
    print(overlaps(evs[0], {"date": "2026-09-18", "start_time": "11:30 AM"}))
    print(overlaps(evs[0], {"date": "2026-09-18", "start_time": "6:00 PM"}))
