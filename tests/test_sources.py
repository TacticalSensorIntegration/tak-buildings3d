"""sources.json: every built-in source carries its licence, and LICENSES.md matches it."""
import json
import re
from pathlib import Path

from tak_buildings3d import licences
from tak_buildings3d.imagery import BUILTIN

RAW = json.loads((Path(licences.__file__).parent / "sources.json").read_text(encoding="utf-8"))
OUTLINES = json.loads((Path(licences.__file__).parent / "outlines.json").read_text(encoding="utf-8"))
REQUIRED = {"name", "continent", "country", "url", "max_zoom", "resolution_m", "coverage",
            "licence", "licence_url", "attribution", "checked"}
OPTIONAL = {"regions", "outline", "exclude", "backend", "pc_collection", "note"}


def test_every_source_has_its_licence():
    for sid, s in RAW.items():
        assert re.fullmatch(r"[a-z0-9-]+", sid), sid
        assert REQUIRED <= set(s), f"{sid} is missing {REQUIRED - set(s)}"
        assert set(s) <= REQUIRED | OPTIONAL, f"{sid} has unknown fields {set(s) - REQUIRED - OPTIONAL}"
        for f in ("name", "country", "licence", "attribution"):
            assert isinstance(s[f], str) and s[f].strip(), f"{sid}.{f}"
        assert s["licence_url"].startswith("https://"), sid
        assert s["continent"] in licences.CONTINENTS, sid
        assert re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", s["checked"]), sid


def test_every_source_is_usable():
    for sid, s in RAW.items():
        assert s["url"].startswith("https://"), sid
        assert "{bbox}" in s["url"] or all(k in s["url"] for k in ("{z}", "{x}", "{y}")), sid
        w, so, e, n = s["coverage"]
        assert -180 <= w < e <= 180 and -90 <= so < n <= 90, sid
        assert isinstance(s["max_zoom"], int) and 0 < s["max_zoom"] <= 24, sid
        assert s["resolution_m"] > 0, sid
        if "outline" in s:
            assert s["outline"] in OUTLINES, sid


def test_builtin_is_loaded_from_sources_json():
    assert list(BUILTIN) == list(RAW)
    assert all(BUILTIN[k]["group"] == RAW[k]["continent"] for k in RAW)


def test_licenses_md_matches_sources_json():
    text = (Path(__file__).parent.parent / "LICENSES.md").read_text(encoding="utf-8")
    assert licences.updated(text) == text, "run: python -m tak_buildings3d.licences"
