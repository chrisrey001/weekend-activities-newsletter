#!/usr/bin/env python3
"""Kiwi's Weekend Guide deterministic event verifier.

Pipeline step 5 (see PLAN.md). Reads runs/<date>/events.json, runs a battery
of DETERMINISTIC checks (no LLM calls), and exits non-zero on any FAIL so the
pipeline halts before render/send. Warnings never block; failures always do.

Incident that motivated this (2026-09-13): the Sep 11-13 test newsletter listed
"City Park Esplanade Fresh Market" as a Sunday event. The cited source was a
March 2015 blog post about a market that has since gone dormant; the current
market at that location runs Saturdays. Honest audit note: the weekday check
alone would NOT have caught that incident (the stale source said "Sundays" and
the assigned date was a Sunday -- they agreed). The freshness and operator
gates are what catch that class of error. Code enforces procedure; it cannot
verify that a researcher's operator_confirmed=true claim is truthful, but it
forces the claim to be explicit and logged.

Usage:
    python3 verify.py runs/2026-09-11/events.json
    python3 verify.py --self-test        # runs the incident regression test
"""

import json
import re
import sys
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]

# A source counts as "fresh" for a recurring event if ANY of these hold:
#  - published within FRESH_DAYS of the run date, or
#  - its excerpt explicitly mentions the current season year.
FRESH_DAYS = 365


def fail(msg):
    return ("FAIL", msg)


def ok(msg=""):
    return ("PASS", msg)


def warn(msg):
    return ("WARN", msg)


# ---------------------------------------------------------------- checks

def check_required_fields(ev):
    missing = [f for f in ("name", "date", "venue", "url", "sources")
               if not ev.get(f)]
    if missing:
        return fail(f"missing required fields: {', '.join(missing)}")
    try:
        datetime.strptime(ev["date"], "%Y-%m-%d")
    except ValueError:
        return fail(f"date not YYYY-MM-DD: {ev.get('date')!r}")
    if ev.get("date_end"):
        try:
            datetime.strptime(ev["date_end"], "%Y-%m-%d")
        except ValueError:
            return fail(f"date_end not YYYY-MM-DD: {ev.get('date_end')!r}")
    if not isinstance(ev["sources"], list) or not ev["sources"]:
        return fail("sources must be a non-empty list")
    return ok()


def check_in_weekend(ev, weekend):
    """C1: event date falls within the run's Fri-Sun."""
    d = datetime.strptime(ev["date"], "%Y-%m-%d").date()
    fri = datetime.strptime(weekend[0], "%Y-%m-%d").date()
    sun = fri + timedelta(days=2)
    if not (fri <= d <= sun):
        return fail(f"date {ev['date']} outside weekend {fri}..{sun}")
    if ev.get("date_end"):
        d2 = datetime.strptime(ev["date_end"], "%Y-%m-%d").date()
        if d2 < d:
            return fail(f"date_end {ev['date_end']} before date {ev['date']}")
    return ok()


def weekday_of(datestr):
    return WEEKDAYS[datetime.strptime(datestr, "%Y-%m-%d").weekday()]


def check_weekday(ev):
    """C2: assigned date's weekday matches the source's stated weekday.

    Only runs when stated_weekday is present. For recurring events the
    research step MUST record what the source claims; a missing value
    is a FAIL, not a skip (otherwise the check is vacuous). For multi-day
    records (date_end set), the stated weekday must fall inside
    [date, date_end].
    """
    stated = ev.get("stated_weekday")
    if not stated:
        if ev.get("recurring"):
            return fail("recurring event without stated_weekday; record "
                        "the weekday the source claims")
        return ("SKIP", "no stated_weekday; nothing to assert")
    stated = stated.strip().capitalize()
    if stated not in WEEKDAYS:
        return fail(f"stated_weekday {stated!r} not a real weekday")
    d = datetime.strptime(ev["date"], "%Y-%m-%d").date()
    d2 = datetime.strptime(ev["date_end"], "%Y-%m-%d").date() \
        if ev.get("date_end") else d
    covered = {WEEKDAYS[(d + timedelta(days=i)).weekday()]
               for i in range((d2 - d).days + 1)}
    if stated not in covered:
        actual = weekday_of(ev["date"])
        extra = f" (range covers {sorted(covered)})" if ev.get("date_end") else ""
        return fail(f"source says {stated}s but date {ev['date']} "
                    f"is a {actual}{extra}")
    return ok(f"{stated} matches {ev['date']}")


def _source_is_fresh(src, ev, run_date, season_year):
    pub = src.get("published_at")
    if pub:
        try:
            p = datetime.strptime(pub, "%Y-%m-%d").date()
            if 0 <= (run_date - p).days <= FRESH_DAYS:
                return True
        except ValueError:
            pass
    excerpt = src.get("excerpt") or ""
    if re.search(rf"\b{season_year}\b", excerpt):
        return True
    # The operator's own page, retrieved this week, is inherently
    # current-season -- but only when the research step attests the
    # operator confirmation explicitly.
    if ev.get("operator_confirmed") and ev.get("operator_url"):
        try:
            op_netloc = urlparse(ev["operator_url"]).netloc.lower()
            src_netloc = urlparse(src.get("url") or "").netloc.lower()
            if op_netloc and op_netloc == src_netloc:
                return True
        except Exception:
            pass
    return False


def check_freshness(ev, run_date):
    """C3: recurring events need at least one current-season source.

    Stale-only sourcing (e.g. an 11-year-old blog post) is a hard FAIL.
    One-off events are exempt: their date comes from an explicit listing.
    """
    if not ev.get("recurring"):
        return ("SKIP", "one-off event; freshness gate not required")
    season_year = run_date.year
    fresh = [s for s in ev["sources"]
             if _source_is_fresh(s, ev, run_date, season_year)]
    if not fresh:
        oldest = min((s.get("published_at") or "unknown")
                     for s in ev["sources"])
        return fail(f"recurring event with no current-season source "
                    f"(oldest source: {oldest}); verify on operator site")
    return ok(f"{len(fresh)} current-season source(s)")


def check_operator(ev):
    """C4: recurring events must have operator-confirmed day/time.

    The research step must attest this explicitly (operator_confirmed=true
    plus operator_url). The code cannot verify the claim is truthful, but it
    makes the attestation mandatory and auditable.
    """
    if not ev.get("recurring"):
        return ("SKIP", "one-off event; operator gate not required")
    if not ev.get("operator_confirmed"):
        return fail("recurring event without operator_confirmed=true; "
                    "confirm day/time on the operator's own site or "
                    "official social before including")
    if not ev.get("operator_url"):
        return fail("operator_confirmed=true but no operator_url recorded")
    try:
        netloc = urlparse(ev["operator_url"]).netloc
    except Exception:
        netloc = ""
    if not netloc or "." not in netloc:
        return fail(f"operator_url not a valid URL: {ev['operator_url']!r}")
    return ok(f"confirmed via {netloc}")


def norm_name(n):
    n = (n or "").strip().lower()
    n = re.sub(r"\s+", " ", n)
    # strip trailing qualifiers like " — Ball Arena (Night 1)", " (Night 2)"
    n = re.split(r"\s+[\u2014\u2013\-:|]\s+", n)[0]
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n).strip()
    n = re.sub(r"^(the|a|an)\s+", "", n)
    return n


def check_dedupe(events):
    """C5: (a) the same source URL on the SAME date twice = duplicate;
    (b) the same normalized event name on the same date = the same event
    listed twice under different URLs (the 2026-09-17 Billy Strings case).
    Cross-date reuse of one listing URL (multi-day festivals) is
    legitimate and passes."""
    seen_url = {}
    seen_name = {}
    dupes = []
    for i, ev in enumerate(events):
        u = (ev.get("url") or "").strip().lower()
        d = (ev.get("date") or "").strip()
        if u:
            key = (u, d)
            if key in seen_url:
                dupes.append(
                    f"#{i} {ev.get('name')!r} reuses url of "
                    f"#{seen_url[key]} on {d}")
            else:
                seen_url[key] = i
        nm = norm_name(ev.get("name"))
        if nm:
            key = (nm, d)
            if key in seen_name:
                dupes.append(
                    f"#{i} {ev.get('name')!r} same event as "
                    f"#{seen_name[key]} on {d}")
            else:
                seen_name[key] = i
    if dupes:
        return fail("duplicates: " + "; ".join(dupes))
    return ok()


# ---------------------------------------------------------------- runner

CHECKS = [
    ("required_fields", lambda ev, weekend, run_date: check_required_fields(ev)),
    ("in_weekend", lambda ev, weekend, run_date: check_in_weekend(ev, weekend)),
    ("weekday", lambda ev, weekend, run_date: check_weekday(ev)),
    ("freshness", lambda ev, weekend, run_date: check_freshness(ev, run_date)),
    ("operator", lambda ev, weekend, run_date: check_operator(ev)),
]


def verify_event(ev, weekend, run_date):
    results = []
    for name, fn in CHECKS:
        try:
            status, msg = fn(ev, weekend, run_date)
        except Exception as e:  # never let a checker crash the gate
            status, msg = "FAIL", f"checker {name} raised: {e}"
        results.append((name, status, msg))
    return results


def verify_file(path):
    data = json.load(open(path))
    weekend = data.get("weekend") or []
    if len(weekend) != 3:
        # derive Fri-Sun from the run directory date (a Thursday run)
        m = re.search(r"runs/(\d{4}-\d{2}-\d{2})", path)
        thu = datetime.strptime(m.group(1), "%Y-%m-%d").date() if m \
            else date.today()
        fri = thu + timedelta(days=(4 - thu.weekday()) % 7)
        weekend = [(fri + timedelta(days=i)).isoformat() for i in range(3)]
    run_date = datetime.strptime(
        re.search(r"runs/(\d{4}-\d{2}-\d{2})", path).group(1),
        "%Y-%m-%d").date() if re.search(r"runs/(\d{4}-\d{2}-\d{2})", path) \
        else date.today()
    events = data.get("events", [])

    report = {"file": path, "weekend": weekend,
              "events": [], "failed": 0, "warned": 0}
    for ev in events:
        res = verify_event(ev, weekend, run_date)
        failed = [r for r in res if r[1] == "FAIL"]
        report["events"].append({
            "name": ev.get("name"), "date": ev.get("date"),
            "checks": [{"check": n, "status": s, "detail": m}
                       for n, s, m in res],
        })
        report["failed"] += len(failed)
        report["warned"] += sum(1 for r in res if r[1] == "WARN")

    dup_status, dup_msg = check_dedupe(events)
    report["dedupe"] = {"status": dup_status, "detail": dup_msg}
    if dup_status == "FAIL":
        report["failed"] += 1
    return report


def print_report(report):
    print(f"Verifying {report['file']}  (weekend {report['weekend']})")
    for ev in report["events"]:
        bad = [c for c in ev["checks"] if c["status"] == "FAIL"]
        if not bad:
            continue
        print(f"\nFAIL  {ev['name']}  [{ev['date']}]")
        for c in bad:
            print(f"      [{c['check']}] {c['detail']}")
    if report["dedupe"]["status"] == "FAIL":
        print(f"\nFAIL  dedupe: {report['dedupe']['detail']}")
    print(f"\nevents: {len(report['events'])}, "
          f"failed checks: {report['failed']}, warnings: {report['warned']}")
    return 1 if report["failed"] else 0


# ---------------------------------------------------------------- self-test

def self_test():
    """Regression test for the 2026-09-13 City Park incident.

    Reconstructs the event record as the research step SHOULD have produced
    it (2015 Escoffier source, no operator confirmation) and asserts the
    verifier rejects it -- and shows WHICH gates fire. Also asserts a
    correctly-sourced record passes.
    """
    weekend = ["2026-09-11", "2026-09-12", "2026-09-13"]
    run_date = date(2026, 9, 10)

    incident = {
        "name": "City Park Esplanade Fresh Market",
        "date": "2026-09-13",
        "venue": "City Park Esplanade",
        "url": "https://www.escoffier.edu/blog/culinary-arts/"
               "the-best-farmers-markets-in-denver/",
        "description": "Sunday-morning ritual at the Sullivan Fountain",
        "recurring": True,
        "stated_weekday": "Sunday",   # the 2015 source said Sundays...
        "sources": [{
            "url": "https://www.escoffier.edu/blog/culinary-arts/"
                   "the-best-farmers-markets-in-denver/",
            "published_at": "2015-03-30",
            "retrieved_at": "2026-09-10",
            "excerpt": "The market runs from 9 AM to 1 PM on Sundays "
                       "between early June and the end of October.",
        }],
        "operator_confirmed": False,
        "operator_url": None,
    }

    good = {
        "name": "South Pearl Street Farmers Market",
        "date": "2026-09-13",
        "venue": "South Pearl Street",
        "url": "https://southpearlstreet.com/farmers-market/",
        "description": "Sunday-morning market ritual on South Pearl",
        "recurring": True,
        "stated_weekday": "Sunday",
        "sources": [{
            "url": "https://southpearlstreet.com/farmers-market/",
            "published_at": "2026-04-15",
            "retrieved_at": "2026-09-10",
            "excerpt": "Sundays, May 3 - November 8, 2026, 9am-1pm.",
        }],
        "operator_confirmed": True,
        "operator_url": "https://southpearlstreet.com/farmers-market/",
    }

    res_incident = dict((n, (s, m))
                        for n, s, m in verify_event(incident, weekend, run_date))
    res_good = dict((n, (s, m))
                    for n, s, m in verify_event(good, weekend, run_date))

    print("Incident record (City Park, 2015 source, no operator check):")
    for n, (s, m) in res_incident.items():
        print(f"  [{s:4}] {n}: {m}")

    print("\nCorrectly-sourced record (South Pearl, 2026 operator source):")
    for n, (s, m) in res_good.items():
        print(f"  [{s:4}] {n}: {m}")

    # Assertions: the incident must FAIL on freshness AND operator...
    assert res_incident["freshness"][0] == "FAIL", \
        "freshness gate must reject the 2015-only source"
    assert res_incident["operator"][0] == "FAIL", \
        "operator gate must reject unconfirmed recurring event"
    # ...the weekday check alone would NOT have caught it (honest):
    assert res_incident["weekday"][0] == "PASS", \
        "weekday check passes here -- source and date agreed (both Sunday)"
    # ...and the good record must pass everything (no FAILs).
    assert all(s != "FAIL" for s, _ in res_good.values()), \
        "good record must pass all gates"

    print("\nSELF-TEST PASSED: incident record rejected "
          "(freshness + operator gates); good record accepted.")

    # Dedupe regression (2026-09-17 Billy Strings case): the same event
    # listed twice on one date under different URLs must FAIL; one listing
    # URL reused across different dates (multi-day festival) must PASS.
    billy_a = dict(good, name="Billy Strings", date="2026-09-18",
                   url="https://www.ticketmaster.com/billy-strings-tickets/artist/2253625")
    billy_b = dict(good, name="Billy Strings \u2014 Ball Arena (Night 1)",
                   date="2026-09-18",
                   url="https://www.jambase.com/article/billy-strings-tour-dates-fall-2026")
    st, msg = check_dedupe([billy_a, billy_b])
    assert st == "FAIL", f"same event twice on one date must fail dedupe: {msg}"
    fest_a = dict(good, name="Denver Oktoberfest", date="2026-09-18",
                  url="https://example.com/oktoberfest")
    fest_b = dict(good, name="Denver Oktoberfest", date="2026-09-19",
                  url="https://example.com/oktoberfest")
    st, msg = check_dedupe([fest_a, fest_b])
    assert st == "PASS", f"cross-date URL reuse must pass dedupe: {msg}"
    print("DEDUPE SELF-TEST PASSED: same-date double listing rejected; "
          "cross-date URL reuse accepted.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        sys.exit(self_test())
    if len(sys.argv) != 2:
        print("usage: verify.py runs/<date>/events.json | verify.py --self-test")
        sys.exit(2)
    sys.exit(print_report(verify_file(sys.argv[1])))
