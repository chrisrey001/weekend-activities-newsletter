"""Tests for pipeline/weather_hook.py and pipeline/template.py."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import template as t
import weather_hook as wh

WK = ["2026-09-18", "2026-09-19", "2026-09-20"]

KIWI_THEME = {
    "newsletter_name": "Kiwi's Weekend Guide",
    "footer_brand": "Kiwi's Weekend Guide",
    "family_label": "the family",
    "home_label": "Englewood",
    "kid_field": "kid_friendly",
    "kid_label": "Family pick",
}


def event(name="Test Event", date="2026-09-18", start_time="7:00 PM",
          price="$20", **kw):
    e = {"name": name, "date": date, "venue": "Test Venue",
         "address_or_area": "Denver, CO", "start_time": start_time,
         "url": "https://example.com/event", "price": price,
         "description": "A test event.", "tags": ["family-friendly"],
         "kid_friendly": False, "drive_time_from_home": "~25 min",
         "recurring": False, "sources": []}
    e.update(kw)
    return e


class TestWeatherHook(unittest.TestCase):
    def test_rain_days_detected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as f:
            json.dump({
                "2026-09-18": {"precip_prob": 65, "high_f": 82,
                               "summary": "Rain"},
                "2026-09-19": {"precip_prob": 20, "high_f": 84,
                               "summary": "Sunny"},
                "2026-09-20": {"precip_prob": 66, "high_f": 79,
                               "summary": "Overcast"},
            }, f)
            path = f.name
        w = wh.summarize(path, WK)
        self.assertEqual(w["status"], "ok")
        self.assertEqual(w["rain_days"], ["2026-09-18", "2026-09-20"])
        self.assertIn("65%", w["note"])
        Path(path).unlink()

    def test_no_file_is_not_provided(self):
        w = wh.summarize(None, WK)
        self.assertEqual(w["status"], "not_provided")
        self.assertEqual(w["rain_days"], [])

    def test_unreadable_file(self):
        w = wh.summarize("/tmp/does-not-exist-xyz.json", WK)
        self.assertEqual(w["status"], "unreadable")


class TestTemplate(unittest.TestCase):
    def test_subject_format(self):
        self.assertEqual(
            t.subject_for(WK, KIWI_THEME),
            "Kiwi's Weekend Guide \u2014 Friday, September 18 "
            "\u2013 Sunday, September 20")

    def test_day_label(self):
        self.assertEqual(t.day_label("2026-09-19"), "Saturday, September 19")

    def test_chronological_per_day_order(self):
        evs = [event("Late", start_time="9:00 PM"),
               event("Morning", start_time="9:00 AM"),
               event("NoTime", start_time=None),
               event("Mid", start_time="1:00 PM")]
        by_day, _ = t.group_events(evs)
        self.assertEqual([e["name"] for e in by_day["2026-09-18"]],
                         ["Morning", "Mid", "Late", "NoTime"])

    def test_multi_day_consolidation(self):
        evs = [event("Fest", date="2026-09-18"),
               event("Fest", date="2026-09-19"),
               event("Solo", date="2026-09-19")]
        by_day, multi = t.group_events(evs)
        self.assertEqual(list(multi.keys()), ["Fest"])
        self.assertNotIn("Fest", [e["name"]
                                  for v in by_day.values() for e in v])
        doc = t.render_newsletter(evs, WK, "<p>Header</p>")
        self.assertIn("All Weekend", doc)

    def test_missing_price_is_hard_fail(self):
        with self.assertRaises(ValueError):
            t.render_newsletter([event("Priceless", price="")], WK,
                                "<p>Header</p>")
        with self.assertRaises(ValueError):
            t.render_newsletter([event("Priceless", price=None)], WK,
                                "<p>Header</p>")

    def test_blank_header_rejected(self):
        with self.assertRaises(ValueError):
            t.render_newsletter([event()], WK, "   ")

    def test_footer_branding(self):
        import html as _html
        doc = t.render_newsletter([event()], WK, "<p>Header</p>",
                                  theme=KIWI_THEME)
        plain = _html.unescape(doc)
        self.assertIn("Kiwi's Weekend Guide", plain)
        self.assertIn("curated for the family", plain)
        self.assertIn("drive times from Englewood", plain)
        self.assertNotIn(t.LEGACY_BUG_BRAND, plain)

    def test_default_theme_is_neutral(self):
        import html as _html
        doc = t.render_newsletter([event()], WK, "<p>Header</p>")
        plain = _html.unescape(doc)
        self.assertIn("Weekend Guide", plain)
        self.assertIn("curated for the family", plain)
        self.assertNotIn("Kiwi", plain)

    def test_kid_badge_uses_theme_label(self):
        import html as _html
        e = event(kid_friendly=True)
        doc = t.render_newsletter([e], WK, "<p>Header</p>", theme=KIWI_THEME)
        self.assertIn("Family pick", _html.unescape(doc))
        # no badge without a kid label in the theme
        doc2 = t.render_newsletter([e], WK, "<p>Header</p>")
        self.assertNotIn("approved", _html.unescape(doc2))

    def test_drive_time_from_home_rendered(self):
        import html as _html
        doc = t.render_newsletter([event()], WK, "<p>Header</p>",
                                  theme=KIWI_THEME)
        self.assertIn("(~25 min)", _html.unescape(doc))

    def test_conflict_annotation_rendered(self):
        e = event(conflict=True,
                  conflict_with="Overlaps your 12:00 PM-12:45 PM: Crew call")
        doc = t.render_newsletter([e], WK, "<p>Header</p>")
        self.assertIn("Overlaps your", doc)

    def test_context_notes_rendered_on_first_event_of_day(self):
        e = event()
        doc = t.render_newsletter([e], WK, "<p>Header</p>",
                                  day_context_notes={
                                      "2026-09-18": ["Nanny's off"]})
        self.assertIn("Nanny&#x27;s off", doc)

    def test_free_price_styled(self):
        doc = t.render_newsletter([event(price="Free")], WK, "<p>Header</p>")
        self.assertIn(">FREE</span>", doc)


if __name__ == "__main__":
    unittest.main()


class TestPublicFooter(unittest.TestCase):
    def _render(self, public):
        theme = dict(KIWI_THEME)
        theme["public_edition"] = public
        return t.render_newsletter([event()], WK, "<p>Header</p>", theme=theme)

    def test_private_keeps_metrics_link(self):
        doc = self._render(False)
        self.assertIn("K3 Command Center", doc)
        self.assertNotIn("Reply STOP", doc)

    def test_public_drops_metrics_link_adds_stop(self):
        doc = self._render(True)
        self.assertNotIn("K3 Command Center", doc)
        self.assertIn("Reply STOP to unsubscribe", doc)

    def test_public_footer_has_no_names(self):
        doc = self._render(True)
        for name in ("Chris", "Kristen", "Ava"):
            self.assertNotIn(name, doc)


class TestDarkBrandTokens(unittest.TestCase):
    """The email copies the HTML design's DARK version exactly."""

    def setUp(self):
        self.doc = t.render_newsletter(
            [event(tags=["family-friendly", "cant-miss"], kid_friendly=True)],
            WK, "<p>Header</p>", theme=KIWI_THEME,
            weather_note="Sunny weekend, highs in the mid-60s")

    def test_dark_tokens_present(self):
        for token in ("#1E1A22", "#29232E", "#F3EDE4", "#B9AFB8",
                      "#3C3442", "#FF9B7A", "#7FD3AE",
                      "#1D3A2E", "#D2A8F0", "#362640", "#45281F",
                      "#1D3441"):
            self.assertIn(token, self.doc, token)

    def test_palette_matches_design_dark_tokens(self):
        # PALETTE must be the HTML design's :root[data-theme="dark"]
        # values exactly (the CTA coral is defined for future buttons;
        # the sample-edition layout has no button, so it need not render).
        self.assertEqual(t.PALETTE, {
            "bg": "#1E1A22", "surface": "#29232E", "ink": "#F3EDE4",
            "muted": "#B9AFB8", "line": "#3C3442", "cta": "#FF8A65",
            "cta_ink": "#1E1A22", "accent_text": "#FF9B7A",
            "sun": "#FFC94A", "sun_soft": "#4A3C1C",
            "sky_soft": "#1D3441", "leaf": "#7FD3AE",
            "leaf_soft": "#1D3A2E", "berry": "#D2A8F0",
            "berry_soft": "#362640", "tomato_soft": "#45281F",
        })

    def test_pill_tags(self):
        self.assertIn(">Family</span>", self.doc)
        self.assertIn("Can&#x27;t miss</span>", self.doc)
        # family pill: mint on dark green; date-night: lilac on dark
        # purple; can't-miss: coral on dark brown
        self.assertIn("#1D3A2E", self.doc)
        self.assertIn("#362640", self.doc)
        self.assertIn("#45281F", self.doc)

    def test_dark_only_no_light_variant(self):
        # Note: #2A2530 is excluded here because it appears inside the
        # design's own dog-mascot SVG (copied exactly), not as a light
        # theme color.
        for light in ("#FFF7EA", "#C8472B", "#FFE7A3",
                      "#6B6270", "#EADFCF", "#1F7A55", "#D8F0E4",
                      "#7C3E9E", "#EFE3F8", "#FBDCD2", "#B8401F"):
            self.assertNotIn(light, self.doc, light)
        self.assertNotIn("@media (prefers-color-scheme", self.doc)
        self.assertIn('name="color-scheme" content="dark"', self.doc)

    def test_rounded_cards(self):
        self.assertIn("border-radius: 16px", self.doc)   # event cards
        self.assertIn("border-radius: 22px", self.doc)   # edition card
        self.assertIn("border-radius: 999px", self.doc)  # pills

    def test_old_palette_gone(self):
        for old in ("#1a1a2e", "#e67e22", "#f9f9f9", "#fdf6ec", "#4CAF50"):
            self.assertNotIn(old, self.doc, old)

    def test_email_fonts(self):
        self.assertIn("Fredoka", self.doc)
        self.assertIn("Nunito", self.doc)
        # the design's exact Google Fonts URL, so clients with webfont
        # support render the real fonts
        self.assertIn("fonts.googleapis.com/css2?family=Fredoka", self.doc)
        # rounded fallback for Apple platforms (very close to Fredoka)
        self.assertIn("Arial Rounded MT Bold", self.doc)

    def test_event_card_single_column_layout(self):
        # matches the design's .event grid (1fr): one full-width cell --
        # event name header, then time/location/tags meta, then details.
        # No side column: the old 150px .when cell wasted a third of the
        # card on mobile.
        self.assertNotIn('width="150"', self.doc)
        self.assertIn('role="presentation"', self.doc)
        # the time now lives in the meta line under the header, not in a
        # separate column
        self.assertLess(self.doc.index("<h3"), self.doc.index("7:00 PM"))

    def test_sun_highlight_used(self):
        # the design's signature yellow marker (--sun) behind a key word
        self.assertIn("#FFC94A", self.doc)

    def test_header_not_nested_paragraphs(self):
        # header_html is inserted verbatim in the lede block, not wrapped
        # in a second <p> (nested paragraphs were a shipped bug).
        self.assertEqual(self.doc.count("<p>Header</p>"), 1)
        self.assertNotIn("<p><p>", self.doc)
