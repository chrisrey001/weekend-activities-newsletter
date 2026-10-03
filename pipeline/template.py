#!/usr/bin/env python3
"""Kiwi's Weekend Guide newsletter template (parameterized).

Promoted from runs/2026-09-17/render.py into the durable pipeline. The
design is copied exactly from the HTML design's DARK version
(workspace/user/files/kiwis-corner-preview.html, :root[data-theme="dark"]):
dark-purple page (#1E1A22), dark-purple cards (#29232E), coral CTA
(#FF8A65), Fredoka headings + Nunito body, rounded cards, category pills.
Email-safe rule: hex values only, inline styles. There is no light
variant -- the dark version is the design (per Chris, 2026-09-29).

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


# Dark-version design tokens, copied exactly from the HTML design
# (:root[data-theme="dark"]). No light variant.
PALETTE = {
    "bg": "#1E1A22",        # page background (dark purple)
    "surface": "#29232E",   # cards (dark purple)
    "ink": "#F3EDE4",       # primary text
    "muted": "#B9AFB8",     # secondary text
    "line": "#3C3442",      # borders
    "cta": "#FF8A65",       # CTA coral
    "cta_ink": "#1E1A22",   # text on CTA
    "accent_text": "#FF9B7A",  # links, eyebrows
    "sun": "#FFC94A",
    "sun_soft": "#4A3C1C",
    "sky_soft": "#1D3441",  # weather strip
    "leaf": "#7FD3AE",
    "leaf_soft": "#1D3A2E",
    "berry": "#D2A8F0",
    "berry_soft": "#362640",
    "tomato_soft": "#45281F",
}
FONT_HEADING = ("'Fredoka', 'Arial Rounded MT Bold', 'Trebuchet MS', "
                "Arial, sans-serif")
FONT_BODY = "'Nunito', 'Segoe UI', Arial, sans-serif"
# The design's exact Google Fonts URL. Email clients with webfont support
# (Apple Mail, iOS Mail) render the real Fredoka/Nunito; everyone else
# falls back to Arial Rounded MT Bold (macOS/iOS, very close to Fredoka).
FONTS_URL = ("https://fonts.googleapis.com/css2?family=Fredoka:wght@500;600;700"
             "&family=Nunito:wght@400;600;700;800&display=swap")

# Category pills, from the design's .tag.fam / .tag.date / .tag.must.
TAG_PILL = {
    "family-friendly": ("Family", "#7FD3AE", "#1D3A2E"),
    "date-night": ("Date night", "#D2A8F0", "#362640"),
    "cant-miss": ("Can't miss", "#FF9B7A", "#45281F"),
}

# Dog mascot, inlined from the HTML design's #dog symbol (SVG <use> does
# not work in email, so the shapes are inlined; degrades to brand text
# where SVG is stripped).
DOG_SVG = (
    '<svg width="38" height="38" viewBox="0 0 200 200" '
    'style="vertical-align:middle;" aria-hidden="true">'
    '<ellipse cx="46" cy="92" rx="26" ry="46" transform="rotate(18 46 92)" fill="#9C6B3E"/>'
    '<ellipse cx="154" cy="92" rx="26" ry="46" transform="rotate(-18 154 92)" fill="#9C6B3E"/>'
    '<circle cx="100" cy="106" r="66" fill="#E7B574"/>'
    '<ellipse cx="100" cy="134" rx="40" ry="30" fill="#F6DDB5"/>'
    '<circle cx="76" cy="96" r="8" fill="#2A2530"/>'
    '<circle cx="124" cy="96" r="8" fill="#2A2530"/>'
    '<circle cx="79" cy="93" r="2.6" fill="#fff"/>'
    '<circle cx="127" cy="93" r="2.6" fill="#fff"/>'
    '<ellipse cx="100" cy="120" rx="13" ry="9" fill="#2A2530"/>'
    '<path d="M86 136 Q100 148 114 136" stroke="#2A2530" stroke-width="4" fill="none" stroke-linecap="round"/>'
    '<path d="M94 142 Q100 162 106 142 Z" fill="#E4572E"/>'
    "</svg>"
)

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
        return (f'<span style="color: {PALETTE["leaf"]}; '
                'font-weight: bold;">FREE</span>')
    if "not confirmed" in price.lower():
        return (f'<span style="color: {PALETTE["muted"]};">{p}</span>')
    return p


def _tags_html(e):
    pills = []
    for t in e.get("tags", []):
        if t not in TAG_PILL:
            continue
        label, fg, bg = TAG_PILL[t]
        pills.append(
            f'<span style="display:inline-block;'
            f'background:{bg};color:{fg};font-size:13px;font-weight:800;'
            f'padding:3px 10px;border-radius:999px;'
            f'font-family:{FONT_BODY};">{esc(label)}</span>')
    return " ".join(pills)


def _kid_html(e, theme):
    label = theme["kid_label"]
    if label and e.get(theme["kid_field"]):
        return (f' &nbsp;<span style="display:inline-block;'
                f'background:{PALETTE["leaf_soft"]};'
                f'color:{PALETTE["leaf"]};font-size:13px;font-weight:800;'
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
        return (f'<br><span style="color:'
                f'{PALETTE["accent_text"]};font-size:13px;">'
                f"\u26A0\uFE0F {esc(e['conflict_with'])}</span>")
    return ""


def _area_short(e):
    area = e.get("address_or_area") or ""
    return area.split(",")[0] if "," in area else area


def event_card(e, context_notes_html="", theme=None):
    """One event card, matching the design's .event (sample edition).

    Single column, full width (the design's grid is `1fr` -- no side
    column): the event name is the header, with the time, location, and
    tags in the meta line just below it, followed by the description.
    """
    theme = _resolve_theme(theme)
    require_price(e)  # hard FAIL before any rendering
    tags = _tags_html(e)
    drive = esc(e.get("drive_time_from_home")
                or e.get("drive_time_from_englewood") or "")
    when = esc(e.get("start_time") or "See listing")
    return (
        f'<table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" style="padding: 16px; border-radius: 16px; '
        f'border: 2px solid {PALETTE["line"]}; '
        f'background: {PALETTE["bg"]}; margin-bottom: 12px;">'
        f"<tr><td>"
        f'<h3 style="margin: 0 0 8px; font-family: {FONT_HEADING}; '
        f'font-size: 20px; font-weight: 600; color: {PALETTE["ink"]};">'
        f'<a href="{esc(e["url"])}" style="color: {PALETTE["ink"]}; '
        f'text-decoration: none;">{esc(e["name"])}</a>'
        f"{_kid_html(e, theme)}</h3>"
        f'<p style="margin: 0; font-size: 14px; font-weight: 700; '
        f'color: {PALETTE["muted"]}; font-family: {FONT_BODY};">'
        f"{tags} "
        f"{when} &nbsp;\U0001F4CD {esc(e['venue'])}, {esc(_area_short(e))} ({drive}) "
        f"&nbsp;\U0001F4B0 {price_html(e['price'])}</p>"
        f"{_conflict_html(e)}{context_notes_html}"
        f'<p style="margin: 8px 0 0; color: {PALETTE["muted"]}; '
        f'font-size: 13px; font-family: {FONT_BODY};">'
        f'{esc(e.get("description") or "")}</p>'
        f"</td></tr></table>"
    )


def _all_weekend_card(name, recs, theme=None):
    theme = _resolve_theme(theme)
    s = recs[0]
    require_price(s)
    day_names = [datetime.strptime(r["date"], "%Y-%m-%d").strftime("%A")
                 for r in recs]
    tags = _tags_html(s)
    drive = esc(s.get("drive_time_from_home")
                or s.get("drive_time_from_englewood") or "")
    when = esc(", ".join(day_names))
    return (
        f'<table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" style="padding: 16px; border-radius: 16px; '
        f'border: 2px solid {PALETTE["line"]}; '
        f'background: {PALETTE["bg"]}; margin-bottom: 12px;">'
        f"<tr><td>"
        f'<h3 style="margin: 0 0 8px; font-family: {FONT_HEADING}; '
        f'font-size: 20px; font-weight: 600; color: {PALETTE["ink"]};">'
        f'<a href="{esc(s["url"])}" style="color: {PALETTE["ink"]}; '
        f'text-decoration: none;">{esc(s["name"])}</a>'
        f"{_kid_html(s, theme)}</h3>"
        f'<p style="margin: 0; font-size: 14px; font-weight: 700; '
        f'color: {PALETTE["muted"]}; font-family: {FONT_BODY};">'
        f"{tags} "
        f"{when} &nbsp;\U0001F4CD {esc(s['venue'])} ({drive}) "
        f"&nbsp;\U0001F4B0 {price_html(s['price'])}</p>"
        f"{_conflict_html(s)}"
        f'<p style="margin: 8px 0 0; color: {PALETTE["muted"]}; '
        f'font-size: 13px; font-family: {FONT_BODY};">'
        f'{esc(s.get("description") or "")}</p>'
        f"</td></tr></table>"
    )


def _footer_html(theme):
    """Footer block. Public editions drop the internal metrics link and
    carry the Reply-STOP unsubscribe line instead."""
    base = (f"{esc(theme['footer_brand'])} \u00B7 curated for "
            f"{esc(theme['family_label'])} \u00B7 drive times from "
            f"{esc(theme['home_label'])}<br>")
    if theme.get("public_edition"):
        return (f'<p style="color: {PALETTE["muted"]}; '
                f'font-size: 14px; text-align: center;">'
                f'{base}Reply STOP to unsubscribe.</p>')
    return (f'<p style="color: {PALETTE["muted"]}; '
            f'font-size: 14px; text-align: center;">'
            f'{base}<a href="https://muse.ai/s/command-center-mockup-kxm6dxvx0p1bxya#performance" '
            f'style="color: {PALETTE["muted"]}; text-decoration: underline;">'
            f'K3 Command Center \u2014 performance metrics</a></p>')


def check_mobile_layout(html_doc):
    """Fail loudly if any card constrains the mobile width.

    Event cards must be single-column, full-width tables: no <td> may
    carry a fixed pixel width (the old 150px time column squeezed card
    content into two-thirds of the phone screen), and every layout
    table must declare width="100%". Returns a list of violation
    strings; an empty list means the layout is mobile-clean.
    """
    violations = []
    for m in re.finditer(r'<td\b[^>]*\bwidth\s*=\s*"(\d+)"', html_doc, re.I):
        violations.append(
            f'<td width="{m.group(1)}">: fixed pixel column widths break '
            "event cards on mobile -- cards must be single-column, full width")
    for m in re.finditer(r'<table\b[^>]*>', html_doc, re.I):
        tag = m.group(0)
        if 'role="presentation"' in tag and 'width="100%"' not in tag:
            violations.append(
                "<table> without width=\"100%\": layout tables must span "
                "the full email width on mobile")
    return violations


def render_newsletter(events, weekend, header_html, picks=None,
                      day_context_notes=None, weather_note="", theme=None):
    """Render the full newsletter HTML.

    events: list of canonical event dicts (with optional "conflict" /
        "conflict_with" annotations from the enrich stage).
    weekend: [fri, sat, sun] ISO date strings.
    header_html: bespoke weekly header HTML (written fresh by the worker).
        Inserted verbatim inside the edition card's lede block -- must be
        non-blank; a static or empty header is a template regression.
    picks: [{"name", "detail", "url"}] can't-miss picks.
    day_context_notes: {date: [note, ...]} family/context notes rendered
        under that day's section (the 🏡 notes).
    weather_note: optional one-line weather summary rendered as the
        weather strip above the events.
    """
    theme = _resolve_theme(theme)
    if not (header_html or "").strip():
        raise ValueError("header_html is required -- the newsletter must "
                         "carry a bespoke, written-fresh header every week")
    picks = picks or []
    day_context_notes = day_context_notes or {}

    by_day, multi = group_events(events)

    picks_html = "".join(
        f'<p style="margin: 0 0 8px;">\u2B50 <strong>'
        f'<a href="{esc(p["url"])}" style="color:{PALETTE["accent_text"]};">'
        f'{esc(p["name"])}</a></strong> '
        f'<span style="color:{PALETTE["muted"]};font-size:13px;">'
        f'\u2014 {esc(p["detail"])}</span></p>'
        for p in picks if p.get("url"))

    day_sections = ""
    for date in weekend:
        day_events = by_day.get(date, [])
        ctx = "".join(
            f'<p style="margin: 6px 0 0; color:{PALETTE["leaf"]};'
            f'font-size:13px;">'
            f"\U0001F3E1 {esc(n)}</p>"
            for n in day_context_notes.get(date, []))
        cards = "".join(event_card(e, theme=theme) for e in day_events)
        day_sections += (
            f'<h2 style="font-family:{FONT_HEADING};color:{PALETTE["ink"]};'
            f'font-size:24px;font-weight:700;margin:26px 0 12px;">'
            f"\U0001F4C5 {esc(day_label(date))} "
            f'<span style="font-weight:normal;font-size:14px;'
            f'color:{PALETTE["muted"]};">({len(day_events)} events)</span>'
            f"</h2>"
            f"{cards}{ctx}"
        )

    aw_cards = "".join(_all_weekend_card(name, recs, theme=theme)
                       for name, recs in sorted(multi.items()))
    all_weekend = ""
    if aw_cards:
        all_weekend = (
            f'<h2 style="font-family:{FONT_HEADING};color:{PALETTE["ink"]};'
            f'font-size:24px;font-weight:700;margin:26px 0 12px;">'
            f"\U0001F5D3\uFE0F All Weekend "
            f'<span style="font-weight:normal;font-size:14px;'
            f'color:{PALETTE["muted"]};">({len(multi)} events)</span>'
            f"</h2>"
            f"{aw_cards}"
        )

    total = sum(len(v) for v in by_day.values()) + len(multi)
    weather_html = ""
    if weather_note:
        weather_html = (
            f'<div style="padding: 10px 6px; border-radius: 16px; '
            f'background: {PALETTE["sky_soft"]}; text-align: center; '
            f'font-weight: 800; font-size: 15px; color: {PALETTE["ink"]}; '
            f'margin: 18px 0;">'
            f"\u2600\uFE0F {esc(weather_note)}</div>"
        )

    # Edition tag: "Oct 2–4" style range, like the design's tag-sample.
    tag = (f"{datetime.strptime(weekend[0], '%Y-%m-%d').strftime('%b %-d')}"
           f"\u2013"
           f"{datetime.strptime(weekend[2], '%Y-%m-%d').strftime('%-d')}")

    doc = f"""<!DOCTYPE html>
<html>
<head><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<link href="{FONTS_URL}" rel="stylesheet">
<style>
@import url('{FONTS_URL}');
</style>
</head>
<body style="margin: 0; padding: 0; background-color: {PALETTE["bg"]};">
<div style="font-family: {FONT_BODY}; max-width: 700px; margin: 0 auto; color: {PALETTE["ink"]}; background-color: {PALETTE["bg"]}; padding: 18px 8px; font-size: 17px; line-height: 1.6;">

<div style="display: flex; align-items: center; gap: 8px; padding: 6px 0 18px;">
  {DOG_SVG}
  <span style="font-family: {FONT_HEADING}; font-weight: 700; font-size: 22px; color: {PALETTE["ink"]};">{esc(theme["newsletter_name"])}</span>
</div>

<div style="background: {PALETTE["surface"]}; border-radius: 22px; border: 2px solid {PALETTE["line"]}; padding: 22px;">

<div style="display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 10px; padding-bottom: 16px; border-bottom: 2px dashed {PALETTE["line"]};">
  <strong style="font-family: {FONT_HEADING}; font-weight: 700; font-size: 22px; color: {PALETTE["ink"]};">This weekend in Denver</strong>
  <span style="padding: 4px 12px; border-radius: 999px; background: {PALETTE["berry_soft"]}; color: {PALETTE["berry"]}; font-weight: 800; font-size: 13px;">{esc(tag)}</span>
</div>

<div style="color: {PALETTE["muted"]}; font-size: 17px; margin: 14px 0 0;">
  {header_html}
</div>

{weather_html}

<h2 style="font-family:{FONT_HEADING};color:{PALETTE["ink"]};font-size:24px;font-weight:700;margin:26px 0 12px;">
  \u2B50 Can't-miss <span style="background-color:{PALETTE["sun"]};color:{PALETTE["cta_ink"]};border-radius:8px;padding:2px 10px;">picks</span>
</h2>
{picks_html}

{day_sections}
{all_weekend}

<p style="margin-top: 22px; color: {PALETTE["muted"]}; font-size: 14px; text-align: center;">
  {total} verified events across Friday\u2013Sunday.<br>
  {_tags_html({"tags": ["family-friendly", "date-night", "cant-miss"]})}
</p>

</div>

<hr style="border: none; border-top: 1px solid {PALETTE["line"]}; margin: 30px 0 12px;">
{_footer_html(theme)}

</div>
</body>
</html>"""
    if LEGACY_BUG_BRAND in doc:
        raise AssertionError(f"template regression: {LEGACY_BUG_BRAND!r} "
                             "leaked into the render")
    return doc
