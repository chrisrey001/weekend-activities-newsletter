"""Source health scorecard + cross-run index for the newsletter pipeline.

Every research run already logs per-source health (``sources_ok`` /
``sources_failed`` in ``events.json``), but nothing ever read those notes
back. This module tallies them across runs into one index file so that:

1. the research brief can check historically reliable sources first and
   skip dead ones (brief_section),
2. the pipeline can raise its hand when a source has failed several weeks
   in a row (stale_sources), and
3. the agent can check whether an event was featured recently
   (recently_featured) -- the cross-week dedupe input.

Storage: a single JSON file at ``runs/index.json`` (INDEX_NAME), rebuilt
from scratch on every update by scanning ``runs/*/events.json``. Rebuilds
are idempotent and deterministic -- there is no incremental state to drift.

Document-store friendly by design (2026-09-29): each entry in
``index["sources"]`` maps 1:1 to a future document in a ``sources``
collection, and each entry in ``index["featured"]`` maps 1:1 to a document
in an ``events`` collection. Migrating later is inserting these JSON docs
-- no reshaping.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

INDEX_NAME = "index.json"
INDEX_VERSION = 1

#: Consecutive failed runs after which a source is flagged as stale.
STALE_THRESHOLD = 3

#: Junk source names that carry no information (agent logged "None").
_JUNK_NAMES = {"none", "n/a", "unknown", ""}

_LAYER_RE = re.compile(r"^(layer[123])\s*:\s*(.+)$", re.IGNORECASE)
_PAREN_RE = re.compile(r"\s*\([^)]*\)")


def _strip_notes(raw: str) -> str:
    """Cut the free-text health note off a verbose entry.

    ``"layer2:Ball Arena -- ok -- Avalanche vs. Jets ..."`` -> the part
    before the first ``" -- "``.
    """
    return raw.split(" -- ", 1)[0].strip()


def normalize_entry(raw: str) -> tuple[str, str] | None:
    """Normalize one raw sources_ok/sources_failed entry.

    Returns ``(key, layer)`` where key is the canonical source name, or
    None for junk entries. Handles the formats seen across runs:

    - ``"layer1:303magazine.com"`` -> (\"303magazine.com\", \"layer1\")
    - ``"layer2:Ball Arena -- ok -- ..."`` -> (\"ball arena\", \"layer2\")
    - ``"layer1:axs.com (partial) -- Jerry Seinfeld ..."`` -> (\"axs.com\", \"layer1\")
    - ``"https://axs.com/events/1480582/jerry-seinfeld-tickets"`` -> (\"axs.com\", \"general\")
    - ``"axs.com"`` -> (\"axs.com\", \"general\")
    - ``"layer2:None"`` -> None
    """
    if not raw or not isinstance(raw, str):
        return None
    text = _strip_notes(raw).strip()
    # Parenthetical status hints like "(partial)" belong to classification,
    # not to the name.
    text = _PAREN_RE.sub("", text).strip()

    layer = "general"
    m = _LAYER_RE.match(text)
    if m:
        layer = m.group(1).lower()
        text = m.group(2).strip()

    # Full URLs -> bare host.
    if "://" in text or text.startswith("www."):
        host = urlparse(text if "://" in text else "https://" + text).hostname or ""
        host = host.lower().removeprefix("www.")
        text = host

    name = text.strip().lower().rstrip("/")
    if name in _JUNK_NAMES:
        return None
    return (name, layer)


def classify(raw: str, in_ok_list: bool) -> str:
    """Classify one raw entry as ok / partial / failed.

    An explicit "partial" marker in the text wins over list membership;
    otherwise membership in sources_ok vs sources_failed decides.
    """
    lowered = raw.lower()
    if "partial" in lowered:
        return "partial"
    return "ok" if in_ok_list else "failed"


def _run_date_from_dir(run_dir: Path) -> str | None:
    name = run_dir.name
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
        return name
    return None


def rebuild(runs_dir: Path | str) -> dict:
    """Rebuild the full index by scanning runs/*/events.json oldest-first."""
    runs_dir = Path(runs_dir)
    sources: dict[str, dict] = {}
    featured: list[dict] = []
    seen_featured: set[str] = set()

    run_dirs = sorted(
        (d for d in runs_dir.iterdir()
         if d.is_dir() and _run_date_from_dir(d)),
        key=lambda d: d.name,
    )
    for run_dir in run_dirs:
        run_date = run_dir.name
        events_path = run_dir / "events.json"
        if not events_path.exists():
            continue
        try:
            data = json.loads(events_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        ok_list = data.get("sources_ok") or []
        fail_list = data.get("sources_failed") or []

        # One observation per source per run (dedupe messy repeats).
        observed: dict[str, str] = {}
        for raw in ok_list:
            norm = normalize_entry(raw)
            if norm and norm[0] not in observed:
                observed[norm[0]] = (classify(raw, True), norm[1])
        for raw in fail_list:
            norm = normalize_entry(raw)
            if norm and norm[0] not in observed:
                observed[norm[0]] = (classify(raw, False), norm[1])

        for key, (status, layer) in observed.items():
            s = sources.get(key)
            if s is None:
                s = {
                    "_id": key,
                    "layer": layer,
                    "name": key,
                    "ok": 0,
                    "partial": 0,
                    "failed": 0,
                    "runs_seen": 0,
                    "first_seen": run_date,
                    "last_seen": run_date,
                    "last_ok": None,
                    "last_status": status,
                    "streak_failed": 0,
                    "layers": [],
                }
                sources[key] = s
            s["runs_seen"] += 1
            s["last_seen"] = run_date
            s["last_status"] = status
            if layer not in s["layers"]:
                s["layers"].append(layer)
            if status == "ok":
                s["ok"] += 1
                s["last_ok"] = run_date
                s["streak_failed"] = 0
            elif status == "partial":
                s["partial"] += 1
                s["streak_failed"] = 0
            else:
                s["failed"] += 1
                s["streak_failed"] += 1

        for e in data.get("events") or []:
            name = (e.get("name") or "").strip()
            if not name:
                continue
            fid = f"{run_date}|{name.lower()}|{(e.get('date') or '')}"
            if fid in seen_featured:
                continue
            seen_featured.add(fid)
            featured.append({
                "_id": fid,
                "run": run_date,
                "name": name,
                "venue": (e.get("venue") or "").strip(),
                "date": e.get("date"),
                "tags": e.get("tags") or [],
            })

    return {
        "version": INDEX_VERSION,
        "updated": datetime.now(timezone.utc).isoformat(),
        "sources": [sources[k] for k in sorted(sources)],
        "featured": featured,
    }


def save(index: dict, path: Path | str) -> Path:
    path = Path(path)
    path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    return path


def load(path: Path | str) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def stale_sources(index: dict, threshold: int = STALE_THRESHOLD) -> list[dict]:
    """Sources whose failed streak reached the threshold, worst first."""
    stale = [s for s in index.get("sources", [])
             if s.get("streak_failed", 0) >= threshold]
    return sorted(stale, key=lambda s: (-s["streak_failed"], s["name"]))


def _reliability(s: dict) -> float:
    seen = s.get("runs_seen", 0)
    if not seen:
        return 0.0
    return (s.get("ok", 0) + 0.5 * s.get("partial", 0)) / seen


def reliable_sources(index: dict, top_n: int = 10) -> list[dict]:
    """Top sources by historical reliability (ok-weighted hit rate)."""
    ranked = sorted(index.get("sources", []),
                    key=lambda s: (-_reliability(s), -s.get("runs_seen", 0),
                                   s["name"]))
    return ranked[:top_n]


def brief_section(index: dict | None, top_n: int = 10) -> str:
    """Compact markdown for the research brief's source-health section."""
    if not index or not index.get("sources"):
        return ""
    lines = ["## Source health (from prior runs)",
             "Check the reliable sources first; skip the stale ones unless",
             "you have reason to believe they recovered.", ""]
    lines.append("Reliable lately:")
    for s in reliable_sources(index, top_n):
        n = s['runs_seen']
        lines.append(
            f"- {s['name']} ({s['layer']}) -- "
            f"{s['ok']} ok / {s['partial']} partial / {s['failed']} failed "
            f"over {n} run{'s' if n != 1 else ''}")
    stale = stale_sources(index)
    if stale:
        lines.append("")
        lines.append(f"Stale -- failed {STALE_THRESHOLD}+ weeks running, skip:")
        for s in stale:
            lines.append(
                f"- {s['name']} ({s['layer']}) -- "
                f"{s['streak_failed']} consecutive failed runs "
                f"(last seen {s['last_seen']})")
    hunt = replacement_section(index)
    if hunt:
        lines.extend(["", hunt])
    return "\n".join(lines)


def replacement_section(index: dict | None) -> str:
    """Brief instructions: hunt replacements for each stale source.

    Detection is deterministic (stale_sources); the discovery itself is
    the research agent's job -- it has the web access the pipeline lacks.
    Candidates come back via the ``source_proposals`` field of the
    events.json output contract.
    """
    if not index:
        return ""
    stale = stale_sources(index)
    if not stale:
        return ""
    lines = ["## Replacement hunt (stale sources)",
             "These sources have failed repeatedly. For each one, find 1-2",
             "alternative sources covering the same beat (same kind of",
             "coverage: weekend roundup, venue calendar, ticketing, etc.) and",
             "report them in `source_proposals` (see Output contract). Only",
             "propose sources you actually verified carry current content --",
             "never invent URLs.",
             ""]
    for s in stale:
        lines.append(
            f"- FIND A REPLACEMENT for {s['name']} ({s['layer']}): "
            f"{s['streak_failed']} consecutive failed runs "
            f"(last seen {s['last_seen']}).")
    return "\n".join(lines)


def format_proposals(proposals) -> list[str]:
    """Render ``source_proposals`` entries for run output / run log.

    Advisory data: malformed entries are skipped, never fatal.
    """
    lines = []
    for p in proposals or []:
        if not isinstance(p, dict):
            continue
        candidate = str(p.get("candidate") or "").strip()
        if not candidate:
            continue
        url = str(p.get("url") or "").strip()
        replaces = str(p.get("replaces") or "a stale source").strip()
        why = str(p.get("why") or "").strip()
        line = f"{candidate}" + (f" ({url})" if url else "")
        line += f" replaces {replaces}"
        if why:
            line += f" -- {why}"
        lines.append(line)
    return lines


def recently_featured(index: dict | None, name: str, venue: str = "",
                       last_n_runs: int = 4) -> list[dict]:
    """Return recent featured events matching name (and venue, if given).

    Used by the agent/research step for cross-week dedupe: if this exact
    event ran in the last few newsletters, prefer rotating it out.
    """
    if not index:
        return []
    runs = sorted({f["run"] for f in index.get("featured", [])}, reverse=True)
    recent = set(runs[:last_n_runs])
    needle = name.strip().lower()
    vneedle = venue.strip().lower()
    hits = []
    for f in index.get("featured", []):
        if f["run"] not in recent:
            continue
        if needle and needle not in f["name"].lower():
            continue
        if vneedle and vneedle not in f["venue"].lower():
            continue
        hits.append(f)
    return hits
