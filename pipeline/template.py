#!/usr/bin/env python3
"""Kiwi's Weekend Guide newsletter template (parameterized).

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
    "dashboard_url": None,          # private-edition footer link (optional)
    "dashboard_label": "Command Center",
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

# Kiwi's Weekend Guide brand tokens (from design/tokens.json, light palette).
# Email-safe rule: hex values only, inline styles; dark mode handled via a
# prefers-color-scheme <style> block with classes + !important (Apple Mail).
PALETTE = {
    "bg": "#FFF7EA", "surface": "#FFFFFF", "ink": "#2A2530",
    "muted": "#6B6270", "line": "#EADFCF", "cta": "#C8472B",
    "cta_ink": "#FFFFFF", "sun_soft": "#FFE7A3",
    "accent_text": "#B8401F",
}
PALETTE_DARK = {
    "bg": "#1E1A22", "surface": "#29232E", "ink": "#F3EDE4",
    "muted": "#B9AFB8", "line": "#3C3442", "cta": "#FF8A65",
    "cta_ink": "#1E1A22", "sun_soft": "#4A3C1C",
    "accent_text": "#FF9B7A",
}
FONT_HEADING = "'Fredoka', 'Trebuchet MS', Arial, sans-serif"
FONT_BODY = "'Nunito', 'Segoe UI', Arial, sans-serif"

# Pill tags (from tokens.json event_tags): label + light (text, bg) pair.
TAG_PILL = {
    "family-friendly": ("Family", "#1F7A55", "#D8F0E4"),
    "date-night": ("Date night", "#7C3E9E", "#EFE3F8"),
    "cant-miss": ("Can't miss", "#B8401F", "#FBDCD2"),
}
TAG_PILL_DARK = {
    "family-friendly": ("Family", "#7FD3AE", "#1D3A2E"),
    "date-night": ("Date night", "#D2A8F0", "#362640"),
    "cant-miss": ("Can't miss", "#FF9B7A", "#45281F"),
}
# Card left-accent color per primary tag (keeps the old color-coding).
TAG_ACCENT = {"family-friendly": "#1F7A55", "date-night": "#7C3E9E",
              "cant-miss": "#C8472B"}

# Dark-mode class overrides (Apple Mail). Gmail (non-app) ignores these and
# falls back to its own auto-darkening of the light palette.
_DARK_STYLE = """
<style>
@media (prefers-color-scheme: dark) {
  .kw-body { background-color: #1E1A22 !important; color: #F3EDE4 !important; }
  .kw-h1 { color: #F3EDE4 !important; border-bottom-color: #FF8A65 !important; }
  .kw-summary { background-color: #4A3C1C !important; color: #F3EDE4 !important; }
  .kw-muted { color: #B9AFB8 !important; }
  .kw-picks { background-color: #FF8A65 !important; color: #1E1A22 !important; }
  .kw-day { background-color: #29232E !important; color: #F3EDE4 !important; }
  .kw-card { background-color: #29232E !important; border-color: #3C3442 !important; }
  .kw-link { color: #F3EDE4 !important; }
  .kw-desc { color: #B9AFB8 !important; }
  .kw-kid { background-color: #1D3A2E !important; color: #7FD3AE !important; }
  .kw-conflict { color: #FF9B7A !important; }
  .kw-hr { border-top-color: #3C3442 !important; }
  .kw-tag-fam { background-color: #1D3A2E !important; color: #7FD3AE !important; }
  .kw-tag-date { background-color: #362640 !important; color: #D2A8F0 !important; }
  .kw-tag-miss { background-color: #45281F !important; color: #FF9B7A !important; }
  .kw-free { color: #7FD3AE !important; }
  .kw-ctx { color: #7FD3AE !important; }
}
</style>"""

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
        return ('<span class="kw-free" style="color: #1F7A55; '
                'font-weight: bold;">FREE</span>')
    if "not confirmed" in price.lower():
        return (f'<span class="kw-muted" style="color: '
                f'{PALETTE["muted"]};">{p}</span>')
    return p


TAG_PILL_CLASS = {"family-friendly": "kw-tag-fam", "date-night": "kw-tag-date",
                  "cant-miss": "kw-tag-miss"}


def _tags_html(e):
    pills = []
    for t in e.get("tags", []):
        if t not in TAG_PILL:
            continue
        label, fg, bg = TAG_PILL[t]
        pills.append(
            f'<span class="{TAG_PILL_CLASS[t]}" style="display:inline-block;'
            f'background:{bg};color:{fg};font-size:11px;font-weight:bold;'
            f'padding:3px 10px;border-radius:999px;'
            f'font-family:{FONT_BODY};">{esc(label)}</span>')
    return " ".join(pills)


def _kid_html(e, theme):
    label = theme["kid_label"]
    if label and e.get(theme["kid_field"]):
        return (f' &nbsp;<span class="kw-kid" style="display:inline-block;'
                f'background:#D8F0E4;'
                f'color:#1F7A55;font-size:12px;font-weight:bold;'
                f'padding:3px 10px;border-radius:999px;'
                f'font-family:{FONT_BODY};">'
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
        return (f'<br><span class="kw-conflict" style="color:'
                f'{PALETTE["accent_text"]};font-size:12px;">'
                f"\u26A0\uFE0F {esc(e['conflict_with'])}</span>")
    return ""


def _area_short(e):
    area = e.get("address_or_area") or ""
    return area.split(",")[0] if "," in area else area


def _card_accent(e):
    tags = e.get("tags") or []
    if "cant-miss" in tags:
        return TAG_ACCENT["cant-miss"]
    if tags:
        return TAG_ACCENT.get(tags[0], PALETTE["muted"])
    return PALETTE["muted"]


def _card_style(accent):
    return (f"margin-bottom: 16px; padding: 14px 16px; "
            f"background: {PALETTE['surface']}; "
            f"border: 2px solid {PALETTE['line']}; "
            f"border-left: 4px solid {accent}; border-radius: 14px;")


def event_li(e, context_notes_html="", theme=None):
    theme = _resolve_theme(theme)
    require_price(e)  # hard FAIL before any rendering
    tags = _tags_html(e)
    accent = _card_accent(e)
    drive = esc(e.get("drive_time_from_home")
                or e.get("drive_time_from_englewood") or "")
    return f'''  <li class="kw-card" style="{_card_style(accent)}">
    {tags} <strong><a class="kw-link" href="{esc(e['url'])}" style="color: {PALETTE['ink']}; text-decoration: none; font-family: {FONT_HEADING}; font-size: 16px;">{esc(e['name'])}</a></strong>{_kid_html(e, theme)}<br>
    <span style="color: {PALETTE['ink']}; font-size: 14px;">\U0001F550 {esc(e.get('start_time') or 'See listing')} &nbsp;|&nbsp; \U0001F4CD {esc(e['venue'])}, {esc(_area_short(e))} ({drive}) &nbsp;|&nbsp; \U0001F4B0 {price_html(e['price'])}</span>{_conflict_html(e)}{context_notes_html}<br>
    <span class="kw-desc" style="color: {PALETTE['muted']}; font-size: 13px;">{esc(e.get('description') or '')}</span>
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
    return f'''  <li class="kw-card" style="{_card_style(TAG_ACCENT['cant-miss'])}">
    {tags} <strong><a class="kw-link" href="{esc(s['url'])}" style="color: {PALETTE['ink']}; text-decoration: none; font-family: {FONT_HEADING}; font-size: 16px;">{esc(s['name'])}</a></strong>{_kid_html(s, theme)}<br>
    <span style="color: {PALETTE['ink']}; font-size: 14px;">\U0001F550 {esc(", ".join(day_names))} &nbsp;|&nbsp; \U0001F4CD {esc(s['venue'])} ({drive}) &nbsp;|&nbsp; \U0001F4B0 {price_html(s['price'])}</span>{_conflict_html(s)}<br>
    <span class="kw-desc" style="color: {PALETTE['muted']}; font-size: 13px;">{esc(s.get('description') or '')}</span>
  </li>'''


def _footer_html(theme):
    """Footer block. Public editions drop the internal metrics link and
    carry the Reply-STOP unsubscribe line instead."""
    base = (f"{esc(theme['footer_brand'])} \u00B7 curated for "
            f"{esc(theme['family_label'])} \u00B7 drive times from "
            f"{esc(theme['home_label'])}<br>")
    if theme.get("public_edition"):
        return (f'<p class="kw-muted" style="color: {PALETTE["muted"]}; '
                f'font-size: 12px; text-align: center;">'
                f'{base}Reply STOP to unsubscribe.</p>')
    dashboard_url = theme.get("dashboard_url")
    if dashboard_url:
        label = esc(theme.get("dashboard_label") or "Command Center")
        link = (f'<a href="{esc(dashboard_url)}" '
                f'style="color: {PALETTE["muted"]}; text-decoration: underline;">'
                f'{label} \u2014 performance metrics</a>')
        return (f'<p class="kw-muted" style="color: {PALETTE["muted"]}; '
                f'font-size: 12px; text-align: center;">'
                f'{base}{link}</p>')
    return (f'<p class="kw-muted" style="color: {PALETTE["muted"]}; '
            f'font-size: 12px; text-align: center;">'
            f'{base.rstrip()}</p>')


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
        f'  <li style="margin-bottom: 8px;">\u2B50 <strong><a class="kw-link" href="{esc(p["url"])}" style="color:{PALETTE["ink"]};">{esc(p["name"])}</a></strong> <span class="kw-muted" style="color:{PALETTE["muted"]};font-size:13px;">\u2014 {esc(p["detail"])}</span></li>'
        for p in picks if p.get("url"))

    day_sections = ""
    for date in weekend:
        day_events = by_day.get(date, [])
        ctx = "".join(
            f'<br><span class="kw-ctx" style="color:#1F7A55;font-size:12px;">'
            f"\U0001F3E1 {esc(n)}</span>"
            for n in day_context_notes.get(date, []))
        lis = "\n".join(event_li(e, ctx if i == 0 else "", theme=theme)
                        for i, e in enumerate(day_events))
        day_sections += f'''
<h2 class="kw-day" style="font-family:{FONT_HEADING};background: {PALETTE["ink"]}; color: white; padding: 12px 16px; border-radius: 14px;">
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
<h2 class="kw-day" style="font-family:{FONT_HEADING};background: {PALETTE["ink"]}; color: white; padding: 12px 16px; border-radius: 14px;">
  \U0001F5D3\uFE0F All Weekend <span style="font-weight:normal;font-size:14px;">({len(aw_lis)} events)</span>
</h2>
<ul style="list-style: none; padding: 0;">
{chr(10).join(aw_lis)}
</ul>'''

    total = sum(len(v) for v in by_day.values()) + len(multi)
    weather_html = (f'<p class="kw-muted" style="color:{PALETTE["muted"]};'
                    f'font-size:13px;">'
                    f"\u2600\uFE0F {esc(weather_note)}</p>\n"
                    if weather_note else "")

    legend = ("<p class=\"kw-muted\" style=\"color:"
              f"{PALETTE['muted']};font-size:14px;\">"
              f"{total} verified events across Friday\u2013Sunday.<br>"
              f"{_tags_html({'tags': ['family-friendly', 'date-night', 'cant-miss']})} "
              f"\u00B7 \u26A0\uFE0F = calendar overlap</p>")

    doc = f"""<!DOCTYPE html>
<html>
<head><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
{_DARK_STYLE}
</head>
<body style="margin: 0; padding: 0; background-color: {PALETTE["bg"]};">
<div class="kw-body" style="font-family: {FONT_BODY}; max-width: 700px; margin: 0 auto; color: {PALETTE["ink"]}; background-color: {PALETTE["bg"]}; padding: 0 8px;">

<h1 class="kw-h1" style="font-family:{FONT_HEADING};color: {PALETTE["ink"]}; border-bottom: 3px solid {PALETTE["cta"]}; padding-bottom: 10px;">
  \U0001F389 {esc(subject_for(weekend, theme))}
</h1>

<p class="kw-summary" style="color: {PALETTE["ink"]}; font-size: 15px; line-height: 1.6; background: {PALETTE["sun_soft"]}; padding: 14px 16px; border-radius: 14px;">
  {header_html}
</p>

{legend}

{weather_html}
<h2 class="kw-picks" style="font-family:{FONT_HEADING};background: {PALETTE["cta"]}; color: {PALETTE["cta_ink"]}; padding: 12px 16px; border-radius: 14px;">
  \u2B50 Can't-Miss Picks
</h2>
<ul style="list-style: none; padding: 0;">
{picks_html}
</ul>

{day_sections}
{all_weekend}

<hr class="kw-hr" style="border: none; border-top: 1px solid {PALETTE["line"]}; margin: 30px 0 12px;">
{_footer_html(theme)}

</div>
</body>
</html>"""
    if LEGACY_BUG_BRAND in doc:
        raise AssertionError(f"template regression: {LEGACY_BUG_BRAND!r} "
                             "leaked into the render")
    return doc
