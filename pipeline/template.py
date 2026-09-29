#!/usr/bin/env python3
"""Kiwi's Corner newsletter template (parameterized).

Promoted from runs/2026-09-17/render.py into the durable pipeline. Same
approved email design (inline styles only, mobile-friendly, 700px max-width);
all run-specific values are now parameters -- there are no date-suffixed
anything in this module.

Public API:
    render_newsletter(events, weekend, header_html, picks=None,
                      day_context_notes=None, weather_note="") -> str
        Renders the full HTML email. Raises ValueError if any event lacks a
        price (missing price is a hard FAIL per the settled requirements).

    subject_for(weekend) -> str
        "Kiwi's Weekend Guide — Friday, September 18 – Sunday, September 20"

    day_label(datestr) -> str
        "Friday, September 18"

Conventions (settled, do not regress):
    - Per-day events in chronological order (unparseable/absent start_time
      sorts last).
    - Price shown on EVERY event.
    - Multi-day events (same name on 2+ days) consolidate into "All Weekend".
    - Bespoke header_html is inserted verbatim (written fresh weekly by the
      worker; trusted content).
    - Footer carries the theme's footer_brand.

Theming: every family-specific string (newsletter name, footer brand, family
members, home area, kid badge) comes from a theme dict -- see
config.theme_from_config(). Pass theme= explicitly, or call set_theme()
once; render functions fall back to the module theme.
"""

import html
import re
from datetime import datetime

FOOTER_BRAND = "Kiwi's Weekend Guide"  # legacy alias; prefer theme["footer_brand"]
LEGACY_BUG_BRAND = "K2 Weekend Rundown"

DEFAULT_THEME = {
    "newsletter_name": "Weekend Guide",
    "footer_brand": "Weekend Guide",
    "family_label": "the family",
    "home_label": "home",
    "kid_field": "kid_friendly",
    "kid_label": None,
}

_THEME = dict(DEFAULT_THEME)


def set_theme(theme):
    """Set the module theme (family branding). Merged over DEFAULT_THEME."""
    _THEME.clear()
    _THEME.update(DEFAULT_THEME)
    _THEME.update(theme or {})


def get_theme():
    return _THEME


def _resolve_theme(theme):
    if theme is None:
        return _THEME
    merged = dict(DEFAULT_THEME)
    merged.update(theme)
    return merged

TAG_EMOJI = {"family-friendly": "\U0001F468\u200D\U0001F469\u200D\U0001F467",
             "date-night": "\U0001F377", "cant-miss": "\u2B50"}
TAG_COLOR = {"family-friendly": "#4CAF50", "date-night": "#9b59b6",
             "cant-miss": "#e67e22"}

_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([AP])\.?M\.?\.?$", re.I)


def esc(s):
    return html.escape(str(s)) if s is not None else ""


def time_key(t):
    """'7:30 PM' -> minutes since midnight; unparseable -> sorts last."""
    m = _TIME_RE.match((t or "").strip())
    if not m:
        return 24 * 60
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).upper()
    if not (1 <= h <= 12 and mi < 60):
        return 24 * 60
    if ap == "P" and h != 12:
        h += 12
    if ap == "A" and h == 12:
        h = 0
    return h * 60 + mi


def day_label(datestr):
    """'2026-09-18' -> 'Friday, September 18' (deterministic)."""
    return datetime.strptime(datestr, "%Y-%m-%d").strftime("%A, %B %-d")


def subject_for(weekend, theme=None):
    theme = _resolve_theme(theme)
    fri, _, sun = weekend
    return (f"{theme['footer_brand']} \u2014 {day_label(fri)} "
            f"\u2013 {day_label(sun)}")


def group_events(events):
    """Split events into (by_day, multi).

    by_day: {date: [events sorted chronologically]} for single-day events.
    multi: {name: [events sorted by date]} for names appearing on 2+ days
    (rendered under "All Weekend").
    """
    by_name = {}
    for e in events:
        by_name.setdefault(e["name"], []).append(e)
    multi = {n: sorted(v, key=lambda e: e["date"])
             for n, v in by_name.items() if len(v) > 1}
    multi_names = set(multi)
    by_day = {}
    for e in events:
        if e["name"] in multi_names:
            continue
        by_day.setdefault(e["date"], []).append(e)
    for k in by_day:
        by_day[k].sort(key=lambda e: time_key(e.get("start_time")))
    return by_day, multi


def require_price(e):
    price = (e.get("price") or "").strip()
    if not price:
        raise ValueError(
            f"event {e.get('name')!r} [{e.get('date')}] has no price -- "
            "missing price is a hard FAIL")
    return price


def price_html(price):
    p = esc(price)
    if price.strip().lower() == "free":
        return '<span style="color: green; font-weight: bold;">FREE</span>'
    if "not confirmed" in price.lower():
        return f'<span style="color: #999;">{p}</span>'
    return p


def _tags_html(e):
    return " ".join(TAG_EMOJI[t] for t in e.get("tags", []) if t in TAG_EMOJI)


def _kid_html(e, theme):
    label = theme["kid_label"]
    if label and e.get(theme["kid_field"]):
        return (' &nbsp;<span style="background:#e8f5e9;color:#2e7d32;'
                'font-size:12px;padding:2px 8px;border-radius:10px;">'
                "\U0001F476 " + esc(label) + "</span>")
    return ""


# _ava_html kept as a thin alias for backward compatibility.
def _ava_html(e, theme=None):
    return _kid_html(e, _resolve_theme(theme))


def _conflict_html(e):
    # Calendar overlap annotation: written by run.py's enrich stage from
    # calendar_context.overlaps(). Only HARD overlaps are flagged here;
    # context notes arrive separately via day_context_notes.
    if e.get("conflict") and e.get("conflict_with"):
        return (f'<br><span style="color:#b26a00;font-size:12px;">'
                f"\u26A0\uFE0F {esc(e['conflict_with'])}</span>")
    return ""


def _area_short(e):
    area = e.get("address_or_area") or ""
    return area.split(",")[0] if "," in area else area


def event_li(e, context_notes_html="", theme=None):
    theme = _resolve_theme(theme)
    require_price(e)  # hard FAIL before any rendering
    tags = _tags_html(e)
    border = "#e67e22" if "cant-miss" in e.get("tags", []) else (
        TAG_COLOR.get((e.get("tags") or [""])[0], "#9b59b6"))
    drive = esc(e.get("drive_time_from_home")
                or e.get("drive_time_from_englewood") or "")
    return f'''  <li style="margin-bottom: 18px; padding: 12px; background: #f9f9f9; border-left: 4px solid {border}; border-radius: 4px;">
    {tags} <strong><a href="{esc(e['url'])}" style="color: #1a1a2e; text-decoration: none;">{esc(e['name'])}</a></strong>{_kid_html(e, theme)}<br>
    \U0001F550 {esc(e.get('start_time') or 'See listing')} &nbsp;|&nbsp; \U0001F4CD {esc(e['venue'])}, {esc(_area_short(e))} ({drive}) &nbsp;|&nbsp; \U0001F4B0 {price_html(e['price'])}{_conflict_html(e)}{context_notes_html}<br>
    <span style="color: #555; font-size: 13px;">{esc(e.get('description') or '')}</span>
  </li>'''


def _all_weekend_li(name, recs, weekend, theme=None):
    theme = _resolve_theme(theme)
    s = recs[0]
    require_price(s)
    day_names = [datetime.strptime(r["date"], "%Y-%m-%d").strftime("%A")
                 for r in recs]
    tags = _tags_html(s)
    drive = esc(s.get("drive_time_from_home")
                or s.get("drive_time_from_englewood") or "")
    return f'''  <li style="margin-bottom: 18px; padding: 12px; background: #f9f9f9; border-left: 4px solid #e67e22; border-radius: 4px;">
    {tags} <strong><a href="{esc(s['url'])}" style="color: #1a1a2e; text-decoration: none;">{esc(s['name'])}</a></strong>{_kid_html(s, theme)}<br>
    \U0001F550 {esc(", ".join(day_names))} &nbsp;|&nbsp; \U0001F4CD {esc(s['venue'])} ({drive}) &nbsp;|&nbsp; \U0001F4B0 {price_html(s['price'])}{_conflict_html(s)}<br>
    <span style="color: #555; font-size: 13px;">{esc(s.get('description') or '')}</span>
  </li>'''


def render_newsletter(events, weekend, header_html, picks=None,
                      day_context_notes=None, weather_note="", theme=None):
    """Render the full newsletter HTML.

    events: list of canonical event dicts (with optional "conflict" /
        "conflict_with" annotations from the enrich stage).
    weekend: [fri, sat, sun] ISO date strings.
    header_html: bespoke weekly header paragraph (HTML, written fresh by
        the worker). Must be non-blank -- a static or empty header is a
        template regression.
    picks: [{"name", "detail", "url"}] can't-miss picks.
    day_context_notes: {date: [note, ...]} family/context notes rendered
        under that day's section (the 🏡 notes).
    weather_note: optional one-line weather summary rendered above the
        day sections.
    """
    theme = _resolve_theme(theme)
    if not (header_html or "").strip():
        raise ValueError("header_html is required -- the newsletter must "
                         "carry a bespoke, written-fresh header every week")
    picks = picks or []
    day_context_notes = day_context_notes or {}

    by_day, multi = group_events(events)

    picks_html = "\n".join(
        f'  <li style="margin-bottom: 8px;">\u2B50 <strong><a href="{esc(p["url"])}" style="color:#1a1a2e;">{esc(p["name"])}</a></strong> <span style="color:#555;font-size:13px;">\u2014 {esc(p["detail"])}</span></li>'
        for p in picks if p.get("url"))

    day_sections = ""
    for date in weekend:
        day_events = by_day.get(date, [])
        ctx = "".join(
            f'<br><span style="color:#2e7d32;font-size:12px;">'
            f"\U0001F3E1 {esc(n)}</span>"
            for n in day_context_notes.get(date, []))
        lis = "\n".join(event_li(e, ctx if i == 0 else "", theme=theme)
                        for i, e in enumerate(day_events))
        day_sections += f'''
<h2 style="background: #1a1a2e; color: white; padding: 10px 14px; border-radius: 6px;">
  \U0001F4C5 {esc(day_label(date))} <span style="font-weight:normal;font-size:14px;">({len(day_events)} events)</span>
</h2>
<ul style="list-style: none; padding: 0;">
{lis}
</ul>'''

    aw_lis = [_all_weekend_li(name, recs, weekend, theme=theme)
              for name, recs in sorted(multi.items())]
    all_weekend = ""
    if aw_lis:
        all_weekend = f'''
<h2 style="background: #1a1a2e; color: white; padding: 10px 14px; border-radius: 6px;">
  \U0001F5D3\uFE0F All Weekend <span style="font-weight:normal;font-size:14px;">({len(aw_lis)} events)</span>
</h2>
<ul style="list-style: none; padding: 0;">
{chr(10).join(aw_lis)}
</ul>'''

    total = sum(len(v) for v in by_day.values()) + len(multi)
    weather_html = (f'<p style="color:#555;font-size:13px;">'
                    f"\u2600\uFE0F {esc(weather_note)}</p>\n"
                    if weather_note else "")

    doc = f"""<!DOCTYPE html>
<html>
<head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto; color: #222; padding: 0 8px;">

<h1 style="color: #1a1a2e; border-bottom: 3px solid #e63946; padding-bottom: 10px;">
  \U0001F389 {esc(subject_for(weekend, theme))}
</h1>

<p style="color: #444; font-size: 15px; line-height: 1.6; background: #fdf6ec; padding: 14px; border-radius: 6px; border-left: 4px solid #e67e22;">
  {header_html}
</p>

<p style="color: #555; font-size: 14px;">{total} verified events across Friday\u2013Sunday. \U0001F468\u200D\U0001F469\u200D\U0001F467 = family-friendly \u00B7 \U0001F377 = date night \u00B7 \u2B50 = can't-miss \u00B7 \u26A0\uFE0F = calendar overlap</p>

{weather_html}
<h2 style="background: #e67e22; color: white; padding: 10px 14px; border-radius: 6px;">
  \u2B50 Can't-Miss Picks
</h2>
<ul style="list-style: none; padding: 0;">
{picks_html}
</ul>

{day_sections}
{all_weekend}

<hr style="border: none; border-top: 1px solid #ddd; margin: 30px 0 12px;">
<p style="color: #999; font-size: 12px; text-align: center;">
  {esc(theme['footer_brand'])} \u00B7 curated for {esc(theme['family_label'])} \u00B7 drive times from {esc(theme['home_label'])}<br>
  <a href="https://muse.ai/s/command-center-mockup-kxm6dxvx0p1bxya#performance" style="color: #999; text-decoration: underline;">K3 Command Center \u2014 performance metrics</a>
</p>

</body>
</html>"""
    if LEGACY_BUG_BRAND in doc:
        raise AssertionError(f"template regression: {LEGACY_BUG_BRAND!r} "
                             "leaked into the render")
    return doc
