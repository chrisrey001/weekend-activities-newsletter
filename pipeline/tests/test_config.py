"""Tests for pipeline/config.py -- the family customization surface."""
import tempfile
import unittest
from pathlib import Path

import config


def write_cfg(text):
    d = Path(tempfile.mkdtemp())
    p = d / "config.yaml"
    p.write_text(text)
    return str(p)


BASE = """
email:
  recipients: ["a@example.com"]
  from_name: "Maple"
  from_email: "a@example.com"
location:
  timezone: "America/Chicago"
  metro: "Austin"
  home_area: "Round Rock"
"""


class TestLoadConfig(unittest.TestCase):
    def test_full_config_loads(self):
        cfg = config.load_config(write_cfg(BASE + """
newsletter:
  name: "ATX Weekend"
  footer_brand: "ATX Weekend Guide"
family:
  members: ["Jordan", "Casey"]
"""))
        self.assertEqual(cfg["newsletter"]["name"], "ATX Weekend")
        self.assertEqual(cfg["location"]["metro"], "Austin")

    def test_defaults_fill_gaps(self):
        cfg = config.load_config(write_cfg(BASE))
        self.assertEqual(cfg["newsletter"]["footer_brand"], "Weekend Guide")
        self.assertEqual(cfg["categories"],
                         ["family-friendly", "date-night", "cant-miss"])

    def test_missing_file_is_loud(self):
        with self.assertRaises(FileNotFoundError):
            config.load_config("/tmp/does-not-exist-config.yaml")

    def test_missing_recipients_rejected(self):
        with self.assertRaises(ValueError):
            config.load_config(write_cfg(
                "email: {recipients: []}\n"
                "location: {timezone: America/Chicago}"))

    def test_bad_timezone_rejected(self):
        with self.assertRaises(ValueError):
            config.load_config(write_cfg(
                BASE.replace("America/Chicago", "Mars/Olympus")))


class TestTheme(unittest.TestCase):
    def _cfg(self, extra=""):
        return config.load_config(write_cfg(BASE + extra))

    def test_family_label_join(self):
        cfg = self._cfg('family:\n  members: ["Jordan", "Casey", "Riley"]\n')
        self.assertEqual(config.family_label(cfg), "Jordan, Casey & Riley")

    def test_family_label_single(self):
        cfg = self._cfg('family:\n  members: ["Jordan"]\n')
        self.assertEqual(config.family_label(cfg), "Jordan")

    def test_kid_label_derived_from_name(self):
        cfg = self._cfg('family:\n  members: ["Jordan", "Riley"]\n'
                        '  kid_name: "Riley"\n')
        theme = config.theme_from_config(cfg)
        self.assertEqual(theme["kid_label"], "Riley-approved")

    def test_explicit_kid_label_wins(self):
        cfg = self._cfg('family:\n  kid_name: "Riley"\n'
                        '  kid_friendly_label: "Riley-pick"\n')
        theme = config.theme_from_config(cfg)
        self.assertEqual(theme["kid_label"], "Riley-pick")

    def test_no_kid_no_label(self):
        theme = config.theme_from_config(self._cfg())
        self.assertIsNone(theme["kid_label"])


if __name__ == "__main__":
    unittest.main()
