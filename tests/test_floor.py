import json
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from cv_skills import cli
from cv_skills.build import build
from cv_skills.floor import floor_selection, pin_floor
from cv_skills.master import ids_listing, load_master

REPO = Path(__file__).resolve().parent.parent
MASTER = REPO / "examples" / "master.yaml"


def run(capsys, *argv):
    rc = cli.main([str(a) for a in argv])
    return rc, json.loads(capsys.readouterr().out)


def test_example_floor_fits_one_page():
    r = pin_floor(MASTER, 1)
    assert r["ok"] and r["limit"] == 1 and r["fits"] is True and r["pages"] == 1
    assert set(r) == {"ok", "pages", "limit", "fits"}


def test_floor_selection_shape():
    sel = floor_selection(MASTER)
    m = load_master(MASTER)
    pinned = [n.id for n in m.nodes.values() if n.kind == "entry" and n.pin]
    assert sel["master_hash"] == m.hash
    assert [e["id"] for s in sel["sections"] for e in s["entries"]] == pinned
    assert "swaps" not in sel
    ids = {i["id"]: i["kind"] for i in ids_listing(m)["ids"]}
    for s in sel["sections"]:
        for e in s["entries"]:
            has_h = any(k == "highlight" and i.startswith(e["id"] + ".") for i, k in ids.items())
            has_d = any(k == "detail" and i.startswith(e["id"] + ".") for i, k in ids.items())
            assert e.get("highlights") == ([] if has_h else None)
            assert e.get("details") == ([] if has_d else None)


def test_floor_selection_builds(tmp_path):
    sel = tmp_path / "selection.json"
    sel.write_text(json.dumps(floor_selection(MASTER)))
    assert build(MASTER, sel, tmp_path / "out.yaml")["ok"] is True


def _pinned_master(tmp_path, change):
    yaml = YAML()
    data = yaml.load(MASTER.read_text(encoding="utf-8"))
    entry = next(
        e for sec in data["cv"]["sections"].values() for e in sec if not isinstance(e, str) and e.get("pin") == "yes"
    )
    change(entry)
    master = tmp_path / "master.yaml"
    with master.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)
    return master


def test_pinned_highlights_are_not_in_the_floor(tmp_path):
    # Pins are entry-level: the floor drops highlights, so 80 of them cost nothing.
    master = _pinned_master(
        tmp_path, lambda e: e.update(highlights=[f"Long pinned bullet {i} about pipelines and quality" for i in range(80)])
    )
    assert pin_floor(master, 1)["fits"] is True


def test_long_pinned_entry_does_not_fit(tmp_path):
    # The summary stays with the entry, so a long one is part of the floor.
    master = _pinned_master(tmp_path, lambda e: e.update(summary=" ".join(f"Sentence {i} of a very long summary." for i in range(400))))
    r = pin_floor(master, 1)
    assert r["ok"] is True and r["fits"] is False and r["pages"] > 1


def test_cli_prints_json(capsys):
    rc, out = run(capsys, "pin-floor", "--master", MASTER, "--limit", "1")
    assert rc == 0 and out == {"ok": True, "pages": 1, "limit": 1, "fits": True}


def test_cli_default_limit_is_2(capsys):
    rc, out = run(capsys, "pin-floor", "--master", MASTER)
    assert rc == 0 and out["limit"] == 2


@pytest.mark.parametrize("limit", ["0", "-1", "x", "1.5", ""])
def test_bad_limit_exit_2(capsys, limit):
    rc, out = run(capsys, "pin-floor", "--master", MASTER, "--limit", limit)
    assert rc == 2 and out["errors"][0]["code"] == "limit_invalid"


def _long_profile(tmp_path):
    """The example master with a long, unpinned profile: one page without it, more with it."""
    yaml = YAML()
    data = yaml.load(MASTER.read_text(encoding="utf-8"))
    data["cv"]["sections"]["profile"] = [" ".join(f"Sentence {i} of a long profile paragraph." for i in range(120))] * 3
    master = tmp_path / "master.yaml"
    with master.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)
    guide = tmp_path / "summary-guide.md"
    guide.write_text("---\nsection: profile\n---\n", encoding="utf-8")
    return master, guide


def test_floor_selection_with_a_section_keeps_all_its_entries():
    sel = floor_selection(MASTER, "profile")
    assert sel["sections"][0] == {"id": "profile", "entries": [{"id": "profile.0"}, {"id": "profile.1"}]}
    assert sel["sections"][1:] == floor_selection(MASTER)["sections"]


def test_floor_with_the_guide_section_grows(tmp_path):
    master, _ = _long_profile(tmp_path)
    assert pin_floor(master, 1)["fits"] is True
    r = pin_floor(master, 1, "profile")
    assert r["fits"] is False and r["pages"] > 1


def test_cli_guide_counts_its_section(tmp_path, capsys):
    master, guide = _long_profile(tmp_path)
    rc, out = run(capsys, "pin-floor", "--master", master, "--limit", "1", "--guide", guide)
    assert rc == 0 and out["fits"] is False and out["pages"] > 1
    rc, out = run(capsys, "pin-floor", "--master", master, "--limit", "1")
    assert rc == 0 and out == {"ok": True, "pages": 1, "limit": 1, "fits": True}  # without --guide: unchanged


def test_cli_invalid_guide(tmp_path, capsys):
    guide = tmp_path / "summary-guide.md"
    guide.write_text("---\nsection: nope\n---\n", encoding="utf-8")
    rc = cli.main(["pin-floor", "--master", str(MASTER), "--guide", str(guide)])
    assert rc == 2 and {e["code"] for e in json.loads(capsys.readouterr().out)["errors"]} == {"guide_invalid"}
