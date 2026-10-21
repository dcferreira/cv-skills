"""`pin-floor`: do the pinned entries alone fit the page limit? Pins are entry-level, so the floor is
each pinned entry with no highlights and no skills items (its summary or bullet stays with it). With a
summary guide, the guide's section is in the floor too, with all its master text: every draft must keep it,
and the written summary is never longer (`max_chars` is at most that text's length)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cv_skills.build import build
from cv_skills.master import load_master
from cv_skills.pagecheck import page_check


def floor_selection(master_path: Path, section: str | None = None) -> dict:
    """The smallest valid selection: pinned entries in master order, children emptied, no swaps; and every entry
    of `section` (the summary guide's section, plain strings only)."""
    m = load_master(master_path)
    sections = []
    for node in m.nodes.values():
        if node.kind != "section":
            continue
        entries = []
        for eid in m.children(node.id, "entry"):
            if not (m.nodes[eid].pin or node.id == section):
                continue
            entry: dict = {"id": eid}
            if m.children(eid, "highlight"):
                entry["highlights"] = []
            if m.children(eid, "detail"):
                entry["details"] = []
            entries.append(entry)
        if entries:
            sections.append({"id": node.id, "entries": entries})
    return {"master_hash": m.hash, "sections": sections}


def pin_floor(master: Path, limit: int, section: str | None = None) -> dict:
    """Build the floor selection in a temp dir and page-check it; over the limit is `fits: false`."""
    selection = floor_selection(master, section)
    with tempfile.TemporaryDirectory(prefix="cv-skills-") as work:
        sel_path = Path(work) / "selection.json"
        sel_path.write_text(json.dumps(selection), encoding="utf-8")
        out = Path(work) / "tailored.yaml"
        build(master, sel_path, out)
        r = page_check(out, limit)
    return {"ok": True, "pages": r["pages"], "limit": r["limit"], "fits": r["fits"]}
