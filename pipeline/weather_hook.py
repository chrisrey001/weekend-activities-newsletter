#!/usr/bin/env python3
"""Weather-based indoor-picks hook for Kiwi's Corner (documented stub).

Deterministic, no network. Reads an INJECTED weather JSON (supplied by the
cron worker via run.py --weather-json) and returns deterministic weather
facts the pipeline can log and surface. It does NOT curate indoor picks --
deciding which events are rain-safe/indoor and reordering the guide around
them stays a worker (agent) step; this hook only supplies the facts.

Expected weather JSON shape (per weekend date):
    {
      "2026-09-18": {"precip_prob": 65, "high_f": 82, "summary": "Rain"},
      "2026-09-19": {"precip_prob": 66, "high_f": 82, "summary": "Light drizzle"},
      "2026-09-20": {"precip_prob": 66, "high_f": 79, "summary": "Overcast"}
    }
Any subset of keys is tolerated; unknown dates are ignored.

A day is a "rain day" when precip_prob >= 50. Returns:
    {"status": "ok", "rain_days": [...ISO dates...], "note": "...", "days": {...}}
or, when no weather file was supplied:
    {"status": "not_provided", "rain_days": [], "note": "", "days": {}}

The "note" is a single factual line for the run log and (optionally) the
newsletter's weather line, e.g. "Rain likely Fri (65%, high 82F); overcast
Sun (66%, high 79F)".
"""

import json

RAIN_THRESHOLD = 50


def _day_label(datestr):
    from datetime import datetime
    return datetime.strptime(datestr, "%Y-%m-%d").strftime("%a")


def summarize(weather_json_path, weekend):
    """Build deterministic weather facts for the weekend.

    weekend: [fri, sat, sun] ISO date strings. Returns the dict described
    in the module docstring. Never raises on malformed input -- returns
    status "unreadable" with the parse note logged.
    """
    empty = {"status": "not_provided", "rain_days": [], "note": "", "days": {}}
    if not weather_json_path:
        return empty
    try:
        with open(weather_json_path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return {"status": "unreadable", "rain_days": [], "note": "",
                "days": {}, "parse_note": str(e)}
    days = {}
    rain_days = []
    parts = []
    for d in weekend:
        info = (data.get(d) or {}) if isinstance(data, dict) else {}
        try:
            prob = int(info.get("precip_prob")) if info.get("precip_prob") is not None else None
        except (TypeError, ValueError):
            prob = None
        try:
            high = int(info.get("high_f")) if info.get("high_f") is not None else None
        except (TypeError, ValueError):
            high = None
        summary = (info.get("summary") or "").strip()
        days[d] = {"precip_prob": prob, "high_f": high, "summary": summary}
        if prob is not None and prob >= RAIN_THRESHOLD:
            rain_days.append(d)
        bits = []
        if summary:
            bits.append(summary.lower())
        if prob is not None:
            bits.append(f"{prob}%")
        if high is not None:
            bits.append(f"high {high}F")
        if bits:
            parts.append(f"{_day_label(d)} ({', '.join(bits)})")
    note = ("; ".join(parts) + ("." if parts else "")).strip()
    return {"status": "ok", "rain_days": rain_days, "note": note, "days": days}


if __name__ == "__main__":
    import sys
    wk = sys.argv[2:5] if len(sys.argv) >= 5 else ["2026-09-18", "2026-09-19", "2026-09-20"]
    print(json.dumps(summarize(sys.argv[1] if len(sys.argv) > 1 else None, wk), indent=1))
