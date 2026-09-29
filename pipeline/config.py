"""Family/newsletter configuration -- the entire customization surface.

One YAML file describes the family, the place, and the newsletter. Everything
family-specific in the pipeline (research brief sources, timezone, sender
identity, template branding) is derived from it, so adapting the newsletter
for a new family is a config edit, not a code edit.

Copy ``config.example.yaml`` to ``config.yaml`` and fill it in.
"""
import yaml
from pathlib import Path
from zoneinfo import ZoneInfo

REQUIRED = [
    ("email", "recipients"),
    ("location", "timezone"),
]

DEFAULTS = {
    "newsletter": {
        "name": "Weekend Guide",
        "footer_brand": "Weekend Guide",
        "target_events": 50,
        "public_edition": False,
    },
    "family": {
        "members": [],
        "kid_name": None,
        "kid_age_years": None,
        "kid_friendly_label": None,
    },
    "location": {
        "timezone": "America/Denver",
        "metro": "Denver",
        "home_area": "home",
    },
    "email": {
        "recipients": [],
        "from_name": "",
        "from_email": "",
    },
    "sources": {
        "layer1": [],
        "venues": [],
        "layer3": [],
    },
    "categories": ["family-friendly", "date-night", "cant-miss"],
    "features": {
        # Per-profile feature flags. calendar_integration=True means the
        # Thursday worker fetches this profile's Google Calendar and passes
        # --calendar-json; False (default) means no calendar is ever read for
        # this profile, even if a --calendar-json path is supplied.
        "calendar_integration": False,
    },
}


def _deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path):
    """Load and validate a config file. Returns a plain dict."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"config not found: {path} -- copy config.example.yaml to "
            f"config.yaml and fill in your family's details.")
    with open(path) as fh:
        user = yaml.safe_load(fh) or {}
    cfg = _deep_merge(DEFAULTS, user)
    for section, key in REQUIRED:
        if not cfg.get(section, {}).get(key):
            # Public editions take recipients per-run via --recipient, so an
            # empty recipients list is legal in their config file.
            if ((section, key) == ("email", "recipients")
                    and cfg["newsletter"].get("public_edition")):
                continue
            raise ValueError(
                f"config {path}: [{section}] {key} is required.")
    try:
        ZoneInfo(cfg["location"]["timezone"])
    except Exception as exc:
        raise ValueError(
            f"config {path}: unknown timezone "
            f"{cfg['location']['timezone']!r}") from exc
    return cfg


def family_label(cfg):
    """'Jordan, Casey & Riley' style label from the members list."""
    members = cfg["family"]["members"]
    if not members:
        return "the family"
    if len(members) == 1:
        return members[0]
    return ", ".join(members[:-1]) + " & " + members[-1]


def theme_from_config(cfg):
    """Branding bundle consumed by template.py."""
    fam = cfg["family"]
    kid_label = fam.get("kid_friendly_label")
    if fam.get("kid_name") and not kid_label:
        kid_label = f"{fam['kid_name']}-approved"
    return {
        "newsletter_name": cfg["newsletter"]["name"],
        "footer_brand": cfg["newsletter"]["footer_brand"],
        "family_label": family_label(cfg),
        "home_label": cfg["location"]["home_area"],
        "kid_field": "kid_friendly",
        "kid_label": kid_label,
        "public_edition": cfg["newsletter"].get("public_edition", False),
        "dashboard_url": cfg["newsletter"].get("dashboard_url"),
        "dashboard_label": cfg["newsletter"].get("dashboard_label", "Command Center"),
    }
