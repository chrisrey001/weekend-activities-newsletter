"""Tests for pipeline/source_scorecard.py."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import source_scorecard as sc


# ---------------------------------------------------------------- normalize


@pytest.mark.parametrize("raw,expected", [
    ("layer1:303magazine.com", ("303magazine.com", "layer1")),
    ("layer2:Ball Arena -- ok -- Avalanche vs. Jets verified",
     ("ball arena", "layer2")),
    ("layer1:axs.com (partial) -- Jerry Seinfeld page verified via search",
     ("axs.com", "layer1")),
    ("https://axs.com/events/1480582/jerry-seinfeld-tickets",
     ("axs.com", "general")),
    ("http://www.fireflyhandmade.com/markets/fall-market-denver",
     ("fireflyhandmade.com", "general")),
    ("axs.com", ("axs.com", "general")),
    ("  Layer3:Free Events  ", ("free events", "layer3")),
    ("layer2:None", None),
    ("layer3:None", None),
    ("None", None),
    ("", None),
    (None, None),
])
def test_normalize_entry(raw, expected):
    assert sc.normalize_entry(raw) == expected


@pytest.mark.parametrize("raw,in_ok,expected", [
    ("layer1:axs.com", True, "ok"),
    ("layer1:westword.com", False, "failed"),
    ("layer1:axs.com (partial) -- verified via search", True, "partial"),
    ("layer2:Gothic -- partial -- no prices", False, "partial"),
])
def test_classify(raw, in_ok, expected):
    assert sc.classify(raw, in_ok) == expected


# ---------------------------------------------------------------- rebuild


def _write_run(tmp_path, run_date, ok, failed, events):
    d = tmp_path / run_date
    d.mkdir()
    (d / "events.json").write_text(json.dumps({
        "weekend": [run_date],
        "sources_ok": ok,
        "sources_failed": failed,
        "events": events,
    }))


def _sample_runs(tmp_path):
    _write_run(tmp_path, "2026-10-01",
               ["layer1:good.com", "layer1:flaky.com (partial) -- slow"],
               ["layer1:dead.com"],
               [{"name": "Panda Fest", "venue": "Civic Center",
                 "date": "2026-10-02", "tags": ["family-friendly"]}])
    _write_run(tmp_path, "2026-10-08",
               ["layer1:good.com"],
               ["layer1:dead.com", "layer1:flaky.com"],
               [{"name": "Panda Fest", "venue": "Civic Center",
                 "date": "2026-10-09", "tags": ["family-friendly"]}])


def test_rebuild_counts_and_streaks(tmp_path):
    _sample_runs(tmp_path)
    index = sc.rebuild(tmp_path)
    by_id = {s["_id"]: s for s in index["sources"]}

    good = by_id["good.com"]
    assert (good["ok"], good["partial"], good["failed"]) == (2, 0, 0)
    assert good["runs_seen"] == 2
    assert good["streak_failed"] == 0
    assert good["last_ok"] == "2026-10-08"

    flaky = by_id["flaky.com"]
    assert (flaky["ok"], flaky["partial"], flaky["failed"]) == (0, 1, 1)
    assert flaky["streak_failed"] == 1  # failed run resets nothing else

    dead = by_id["dead.com"]
    assert dead["failed"] == 2
    assert dead["streak_failed"] == 2

    assert len(index["featured"]) == 2
    assert index["version"] == sc.INDEX_VERSION


def test_rebuild_dedupes_repeats_within_a_run(tmp_path):
    _write_run(tmp_path, "2026-10-01",
               ["layer1:dup.com", "https://dup.com/page", "layer1:dup.com"],
               [], [])
    index = sc.rebuild(tmp_path)
    dup = [s for s in index["sources"] if s["_id"] == "dup.com"]
    assert len(dup) == 1
    # layer1 entry seen first; the bare-URL twin is a different layer key.
    assert dup[0]["runs_seen"] == 1


def test_rebuild_skips_junk_and_bad_files(tmp_path):
    _write_run(tmp_path, "2026-10-01",
               ["layer2:None", "layer1:real.com"], [], [])
    bad = tmp_path / "2026-10-08"
    bad.mkdir()
    (bad / "events.json").write_text("{not json")
    index = sc.rebuild(tmp_path)
    ids = {s["_id"] for s in index["sources"]}
    assert ids == {"real.com"}


def test_stale_sources_threshold(tmp_path):
    _sample_runs(tmp_path)
    index = sc.rebuild(tmp_path)
    assert sc.stale_sources(index, threshold=3) == []
    stale = sc.stale_sources(index, threshold=2)
    assert [s["_id"] for s in stale] == ["dead.com"]


def test_brief_section(tmp_path):
    _sample_runs(tmp_path)
    index = sc.rebuild(tmp_path)
    section = sc.brief_section(index)
    assert "## Source health" in section
    assert "good.com" in section
    # dead.com not yet at the default threshold of 3
    assert "Stale" not in section
    section2 = sc.brief_section(index, top_n=1)
    assert "good.com" in section2


def test_brief_section_empty_index():
    assert sc.brief_section(None) == ""
    assert sc.brief_section({"sources": []}) == ""


def test_recently_featured(tmp_path):
    _sample_runs(tmp_path)
    index = sc.rebuild(tmp_path)
    hits = sc.recently_featured(index, "Panda Fest")
    assert len(hits) == 2
    assert sc.recently_featured(index, "Panda Fest", venue="Red Rocks") == []
    assert sc.recently_featured(index, "Nonexistent Event") == []
    # last_n_runs window respected
    assert sc.recently_featured(index, "Panda Fest", last_n_runs=1)[0]["run"] \
        == "2026-10-08"


def test_save_load_roundtrip(tmp_path):
    _sample_runs(tmp_path)
    index = sc.rebuild(tmp_path)
    p = sc.save(index, tmp_path / "index.json")
    assert p.exists()
    loaded = sc.load(p)
    assert loaded["sources"] == index["sources"]
    assert sc.load(tmp_path / "missing.json") is None


# ------------------------------------------------------- replacement hunt


def _stale_run(tmp_path, run_date, failed_source="dead.com"):
    _write_run(tmp_path, run_date,
               ["layer1:good.com"],
               [f"layer1:{failed_source}"],
               [])


def test_replacement_section_names_stale_sources(tmp_path):
    for d in ("2026-10-01", "2026-10-08", "2026-10-15"):
        _stale_run(tmp_path, d)
    index = sc.rebuild(tmp_path)
    section = sc.replacement_section(index)
    assert "## Replacement hunt" in section
    assert "FIND A REPLACEMENT for dead.com" in section
    assert "3 consecutive failed runs" in section
    assert "never invent URLs" in section


def test_replacement_section_empty_without_stale(tmp_path):
    _write_run(tmp_path, "2026-10-01", ["layer1:good.com"], [], [])
    index = sc.rebuild(tmp_path)
    assert sc.replacement_section(index) == ""
    assert sc.replacement_section(None) == ""


def test_brief_section_includes_hunt_when_stale(tmp_path):
    for d in ("2026-10-01", "2026-10-08", "2026-10-15"):
        _stale_run(tmp_path, d)
    index = sc.rebuild(tmp_path)
    section = sc.brief_section(index)
    assert "## Source health" in section
    assert "## Replacement hunt" in section
    assert "FIND A REPLACEMENT for dead.com" in section


@pytest.mark.parametrize("proposals,expected", [
    ([{"replaces": "dead.com", "candidate": "New Weekly",
       "url": "https://new.example.com", "why": "current weekend roundup"}],
     ["New Weekly (https://new.example.com) replaces dead.com -- current weekend roundup"]),
    ([{"candidate": "No URL Site", "replaces": "dead.com"}],
     ["No URL Site replaces dead.com"]),
    ([{"replaces": "dead.com", "url": "https://x.example.com"},
      "junk", None],
     []),
    ([], []),
    (None, []),
])
def test_format_proposals(proposals, expected):
    assert sc.format_proposals(proposals) == expected
