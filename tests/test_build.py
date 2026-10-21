import json
import os
import random
import re
from pathlib import Path

import pytest
from rendercv.schema.rendercv_model_builder import build_rendercv_dictionary_and_model
from rendercv.schema.yaml_reader import read_yaml

from cv_skills.cli import Failure, main
from cv_skills.master import EXTRA_KEYS, load_master, strip_extra_keys

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "master.yaml"
EXAMPLE_SELECTION = REPO / "examples" / "selection.json"


@pytest.fixture(scope="module")
def master():
    return load_master(EXAMPLE)


def example_raw() -> dict:
    return json.loads(EXAMPLE_SELECTION.read_text(encoding="utf-8"))


def write_sel(tmp_path: Path, raw: dict, name: str = "selection.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(raw), encoding="utf-8")
    return p


def run_build(tmp_path: Path, raw: dict):
    from cv_skills.build import build

    out = tmp_path / "tailored.yaml"
    result = build(EXAMPLE, write_sel(tmp_path, raw), out)
    return result, out, read_yaml(out.read_text(encoding="utf-8"))


def run_cli(argv: list[str], capsys) -> tuple[int, dict]:
    code = main(argv)
    return code, json.loads(capsys.readouterr().out)


# --- selections built in tests --------------------------------------------


def everything_raw(m) -> dict:
    """Every section, entry, highlight and detail, in master order; no swaps."""
    sections = []
    for sid in m.children(None, "section"):
        entries = []
        for eid in m.children(sid, "entry"):
            e: dict = {"id": eid}
            if m.children(eid, "highlight"):
                e["highlights"] = m.children(eid, "highlight")
            if m.children(eid, "detail"):
                e["details"] = m.children(eid, "detail")
            entries.append(e)
        sections.append({"id": sid, "entries": entries})
    return {"master_hash": m.hash, "sections": sections, "swaps": []}


def swappable_selected(m, raw: dict) -> list[str]:
    """Every swappable target the selection keeps: text entries, highlights, details, summary, bullet."""
    out = []
    for s in raw["sections"]:
        for e in s["entries"]:
            entry = master_entry_of(m.data, e["id"])
            if isinstance(entry, str):
                out.append(e["id"])
                continue
            for key in ("summary", "bullet"):
                if isinstance(entry.get(key), str) and entry[key]:
                    out.append(f"{e['id']}.{key}")
            out += e.get("highlights", []) + e.get("details", [])
    return out


def random_swaps(m, raw: dict, rng: random.Random) -> list[dict]:
    """Random valid swaps: for some selected targets, 1–2 non-overlapping substrings that
    each occur exactly once in the target's master text, replaced by a marker `to`."""
    swaps: list[dict] = []
    for target in swappable_selected(m, raw):
        if rng.random() < 0.5:
            continue
        text = raw_master_text(m.data, target)
        taken: list[tuple[int, int]] = []
        for _ in range(rng.choice((1, 2))):
            for _attempt in range(50):
                start = rng.randrange(len(text))
                end = rng.randint(start + 1, min(len(text), start + 15))
                frm = text[start:end]
                if (
                    frm.strip()
                    and text.count(frm) == 1
                    and all(end <= a or start >= b for a, b in taken)
                ):
                    taken.append((start, end))
                    swaps.append({"target_id": target, "from": frm, "to": f"⟨swap{len(swaps)} ü⟩"})
                    break
    rng.shuffle(swaps)
    return swaps


def random_raw(m, seed: int) -> dict:
    """A random valid selection: random subsets in random order, pinned entries kept,
    random swaps on some selected targets."""
    rng = random.Random(seed)

    def subset(ids: list[str], keep: set[str] = frozenset()) -> list[str]:
        chosen = [i for i in ids if i in keep or rng.random() < 0.6]
        rng.shuffle(chosen)
        return chosen

    pinned = {n.id for n in m.nodes.values() if n.kind == "entry" and n.pin}
    pinned_sections = {m.nodes[p].parent for p in pinned}
    sections = []
    for sid in subset(m.children(None, "section"), pinned_sections):
        entry_ids = subset(m.children(sid, "entry"), pinned) or m.children(sid, "entry")[:1]
        entries = []
        for eid in entry_ids:
            e: dict = {"id": eid}
            if m.children(eid, "highlight"):
                e["highlights"] = subset(m.children(eid, "highlight"))
            if m.children(eid, "detail"):
                e["details"] = subset(m.children(eid, "detail"))
            entries.append(e)
        sections.append({"id": sid, "entries": entries})
    raw = {"master_hash": m.hash, "sections": sections, "swaps": []}
    raw["swaps"] = random_swaps(m, raw, rng)
    return raw


# --- P1 property ------------------------------------------------------------
# The oracle reads the raw master YAML (`m.data`) by walking the ID path; it does
# not use the loader's node table (`m.text_of`/`m.children`), so a loader bug
# cannot hide a build bug.


def details_items(s: str) -> list[str]:
    """Skills items of a `details` string, split independently of `split_details`:
    commas not inside ()/[] (no nesting in the example), trimmed, empties dropped."""
    parts = re.split(r",(?![^()\[\]]*[)\]])", s)
    return [p.strip() for p in parts if p.strip()]


def master_entry_of(data, eid: str):
    sid, i = eid.split(".")[:2]
    return data["cv"]["sections"][sid][int(i)]


def raw_master_text(data, target_id: str) -> str:
    """The raw master string at `target_id`: section key -> entry index -> field/index."""
    parts = target_id.split(".")
    entry = master_entry_of(data, target_id)
    if len(parts) == 2:
        assert isinstance(entry, str)
        return entry
    if parts[2] == "highlights":
        return entry["highlights"][int(parts[3])]
    if parts[2] == "details":
        return details_items(entry["details"])[int(parts[3])]
    assert parts[2] in ("summary", "bullet") and len(parts) == 3
    return entry[parts[2]]


def expected_text(m, target_id: str, swaps: list[dict]) -> str:
    """Oracle: raw master text of `target_id` with exactly its listed swaps applied."""
    text = raw_master_text(m.data, target_id)
    spans = []
    for s in swaps:
        if s["target_id"] == target_id:
            assert text.count(s["from"]) == 1
            spans.append((text.index(s["from"]), s["from"], s["to"]))
    out, pos = "", 0
    for start, frm, to in sorted(spans):
        out += text[pos:start] + to
        pos = start + len(frm)
    return out + text[pos:]


def assert_no_extra_keys(obj) -> None:
    if isinstance(obj, dict):
        assert "pin" not in obj and "tags" not in obj, obj
        for v in obj.values():
            assert_no_extra_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            assert_no_extra_keys(v)


def assert_p1(m, raw: dict, out_doc) -> None:
    """Every string leaf of the tailored `cv.sections` is master text, or master
    text with exactly the listed swaps; details lines are a subset of master items
    (any order), each master-or-swapped, rejoined with ", "; no pin/tags remain."""
    swaps = raw.get("swaps", [])
    master_sections = m.data["cv"]["sections"]
    out_sections = out_doc["cv"]["sections"]
    assert_no_extra_keys(out_sections)
    assert list(out_sections) == [s["id"] for s in raw["sections"]]

    for s in raw["sections"]:
        out_entries = out_sections[s["id"]]
        assert len(out_entries) == len(s["entries"])
        for e, out_entry in zip(s["entries"], out_entries):
            eid = e["id"]
            master_entry = master_sections[s["id"]][int(eid.split(".")[1])]
            if isinstance(master_entry, str):
                assert out_entry == expected_text(m, eid, swaps)
                continue
            assert set(out_entry) <= set(master_entry) - set(EXTRA_KEYS)
            for key, value in out_entry.items():
                if key == "highlights":
                    assert value == [expected_text(m, h, swaps) for h in e["highlights"]]
                    assert value, "empty highlights should be dropped, not emitted"
                elif key == "details" and "label" in master_entry:
                    n_items = len(details_items(master_entry["details"]))
                    assert len(set(e["details"])) == len(e["details"])
                    assert set(e["details"]) <= {f"{eid}.details.{k}" for k in range(n_items)}
                    items = [expected_text(m, d, swaps) for d in e["details"]]
                    assert value == ", ".join(items)
                elif key in ("summary", "bullet"):
                    assert value == expected_text(m, f"{eid}.{key}", swaps)
                else:
                    assert value == master_entry[key], (eid, key)
            for key in set(master_entry) - set(EXTRA_KEYS) - set(out_entry):
                assert key == "highlights" and e["highlights"] == []


def test_p1_example_selection(tmp_path, master):
    raw = example_raw()
    assert raw["swaps"], "the example should exercise swaps"
    _, _, doc = run_build(tmp_path, raw)
    assert_p1(master, raw, doc)


def test_p1_everything_in_master_order_equals_master_minus_extra_keys(tmp_path, master):
    raw = everything_raw(master)
    _, _, doc = run_build(tmp_path, raw)
    assert_p1(master, raw, doc)
    expected = {
        sid: [e if isinstance(e, str) else dict(strip_extra_keys(e)) for e in entries]
        for sid, entries in master.data["cv"]["sections"].items()
    }
    got = {sid: [e if isinstance(e, str) else dict(e) for e in entries] for sid, entries in doc["cv"]["sections"].items()}
    assert got == expected


@pytest.mark.parametrize("seed", range(20))
def test_p1_random_selections(tmp_path, master, seed):
    raw = random_raw(master, seed)
    result, _, doc = run_build(tmp_path, raw)
    assert_p1(master, raw, doc)
    swaps = raw["swaps"]
    assert [(r["target_id"], r["from"], r["to"]) for r in result["swaps"]] == [
        (s["target_id"], s["from"], s["to"]) for s in swaps
    ]
    for r in result["swaps"]:
        assert expected_text(master, r["target_id"], swaps)[r["start"] : r["end"]] == r["to"]


def test_random_selections_exercise_swaps(master):
    counts = [len(random_raw(master, seed)["swaps"]) for seed in range(20)]
    assert sum(1 for c in counts if c) >= 15, counts
    targets = {s["target_id"].split(".")[2] if s["target_id"].count(".") >= 2 else "entry"
               for seed in range(20) for s in random_raw(master, seed)["swaps"]}
    assert {"highlights", "details", "summary", "bullet", "entry"} <= targets, targets


# --- build output -----------------------------------------------------------


def test_build_example_writes_valid_rendercv(tmp_path):
    _, out, _ = run_build(tmp_path, example_raw())
    build_rendercv_dictionary_and_model(out.read_text(encoding="utf-8"))


def test_output_has_no_pin_or_tags(tmp_path):
    _, out, doc = run_build(tmp_path, example_raw())
    assert_no_extra_keys(doc)
    text = out.read_text(encoding="utf-8")
    assert "pin:" not in text and "tags:" not in text


def test_non_section_fields_copied_verbatim(tmp_path, master):
    _, _, doc = run_build(tmp_path, example_raw())
    assert list(doc) == ["cv", "design", "locale", "settings"]
    for key in ("design", "locale", "settings"):
        assert doc[key] == master.data[key]
    for key, value in master.data["cv"].items():
        if key != "sections":
            assert doc["cv"][key] == value
    assert list(doc["cv"]) == list(master.data["cv"])


def test_order_follows_selection(tmp_path, master):
    _, _, doc = run_build(tmp_path, example_raw())
    sections = doc["cv"]["sections"]
    assert list(sections) == ["profile", "experience", "skills", "education", "honors"]
    assert sections["profile"] == [master.text_of("profile.1"), master.text_of("profile.0")]
    assert [e["company"] for e in sections["experience"]] == ["Quillfeather Labs", "Brightwater Logistics"]
    assert sections["experience"][0]["highlights"][0].startswith("Built a feature store used by four machine-learning teams")
    assert sections["experience"][0]["highlights"][1] == master.text_of("experience.0.highlights.0")
    assert [e["label"] for e in sections["skills"]] == ["Data and infrastructure", "Languages"]
    assert sections["skills"][0]["details"] == "DuckDB, Kafka, Airflow"
    assert sections["skills"][1]["details"] == "Python (pandas and polars), SQL"


def test_empty_highlights_drops_key(tmp_path, master):
    raw = example_raw()
    raw["sections"][1]["entries"][1]["highlights"] = []
    _, out, doc = run_build(tmp_path, raw)
    assert "highlights" not in doc["cv"]["sections"]["experience"][1]
    assert_p1(master, raw, doc)


def test_swap_records_returned(tmp_path, master):
    result, out, doc = run_build(tmp_path, example_raw())
    assert result["ok"] is True
    assert result["out"] == str(out)
    assert result["master_hash"] == master.hash
    assert [r["index"] for r in result["swaps"]] == [0, 1, 2]
    rec = result["swaps"][0]
    assert rec["target_id"] == "experience.0.summary"
    tailored = doc["cv"]["sections"]["experience"][0]["summary"]
    assert tailored[rec["start"] : rec["end"]] == rec["to"] == "demand-forecasting product"


# --- rejections ------------------------------------------------------------


def test_rejection_writes_nothing_and_exits_1(tmp_path, capsys):
    raw = example_raw()
    raw["swaps"].append({"target_id": "experience.0.highlights.3", "from": "three", "to": "3"})
    raw["sections"][3]["entries"] = []  # education: empty_section + pin_missing
    out = tmp_path / "tailored.yaml"
    code, payload = run_cli(
        ["build", "--master", str(EXAMPLE), "--selection", str(write_sel(tmp_path, raw)), "--out", str(out)], capsys
    )
    assert code == 1
    assert payload["ok"] is False
    codes = {e["code"] for e in payload["errors"]}
    assert {"swap_target_not_selected", "empty_section", "pin_missing"} <= codes
    assert not out.exists()


def test_schema_rejection_exits_1(tmp_path, capsys):
    raw = example_raw()
    raw["extra"] = 1
    out = tmp_path / "tailored.yaml"
    code, payload = run_cli(
        ["build", "--master", str(EXAMPLE), "--selection", str(write_sel(tmp_path, raw)), "--out", str(out)], capsys
    )
    assert code == 1
    assert payload["errors"][0]["code"] == "selection_schema"
    assert not out.exists()


def test_missing_master_file_exits_2(tmp_path, capsys):
    out = tmp_path / "tailored.yaml"
    code, payload = run_cli(
        [
            "build",
            "--master",
            str(tmp_path / "nope.yaml"),
            "--selection",
            str(write_sel(tmp_path, example_raw())),
            "--out",
            str(out),
        ],
        capsys,
    )
    assert code == 2
    assert payload["ok"] is False
    assert payload["errors"][0]["code"] == "master_invalid"
    assert not out.exists()


@pytest.mark.parametrize("content", [None, "{not json", "\xff"])
def test_unusable_selection_file_exits_2(tmp_path, capsys, content):
    sel = tmp_path / "selection.json"
    if content == "\xff":
        sel.write_bytes(b"\xff\xfe")
    elif content is not None:
        sel.write_text(content, encoding="utf-8")
    out = tmp_path / "tailored.yaml"
    code, payload = run_cli(["build", "--master", str(EXAMPLE), "--selection", str(sel), "--out", str(out)], capsys)
    assert code == 2
    (err,) = payload["errors"]
    assert err["code"] == "selection_invalid"
    assert err["id"] == str(sel)
    assert str(sel) in err["message"]
    assert not out.exists()


def test_invalid_output_is_internal_error(tmp_path, monkeypatch, capsys):
    import cv_skills.build as b

    real = b.assemble

    def broken(*args, **kwargs):
        doc = real(*args, **kwargs)
        doc["cv"]["not_a_rendercv_field"] = "x"
        return doc

    monkeypatch.setattr(b, "assemble", broken)
    out = tmp_path / "tailored.yaml"
    code, payload = run_cli(
        ["build", "--master", str(EXAMPLE), "--selection", str(write_sel(tmp_path, example_raw())), "--out", str(out)],
        capsys,
    )
    assert code == 2
    assert {e["code"] for e in payload["errors"]} == {"internal_invalid_output"}
    assert "cv-skills bug" in payload["errors"][0]["message"]
    assert not out.exists()


def test_rejection_leaves_existing_out_untouched(tmp_path):
    from cv_skills.build import build

    out = tmp_path / "tailored.yaml"
    out.write_text("previous\n", encoding="utf-8")
    raw = example_raw()
    raw["master_hash"] = "sha256:0"
    with pytest.raises(Failure) as exc:
        build(EXAMPLE, write_sel(tmp_path, raw), out)
    assert exc.value.code == 1
    assert out.read_text(encoding="utf-8") == "previous\n"


def test_cli_build_ok_json(tmp_path, capsys, master):
    out = tmp_path / "tailored.yaml"
    code, payload = run_cli(
        ["build", "--master", str(EXAMPLE), "--selection", str(EXAMPLE_SELECTION), "--out", str(out)], capsys
    )
    assert code == 0
    assert set(payload) == {"ok", "out", "master_hash", "swaps", "titles"}
    assert payload["ok"] is True
    assert payload["titles"] == []  # always present
    assert payload["out"] == str(out)
    assert payload["master_hash"] == master.hash
    for rec in payload["swaps"]:
        assert set(rec) == {"index", "target_id", "from", "to", "start", "end"}
        assert rec["end"] - rec["start"] == len(rec["to"])
    assert out.exists()


# --- hardened error paths ----------------------------------------------------


def cli_build(master_path: Path, sel_path: Path, out: Path, capsys) -> tuple[int, dict]:
    return run_cli(["build", "--master", str(master_path), "--selection", str(sel_path), "--out", str(out)], capsys)


def test_master_without_sections_and_empty_selection_builds(tmp_path, capsys):
    m_path = tmp_path / "m.yaml"
    m_path.write_text("cv:\n  name: Ann Example\n", encoding="utf-8")
    m = load_master(m_path)
    out = tmp_path / "out.yaml"
    code, payload = cli_build(m_path, write_sel(tmp_path, {"master_hash": m.hash, "sections": []}), out, capsys)
    assert code == 0, payload
    assert "sections" not in read_yaml(out.read_text(encoding="utf-8"))["cv"]


def test_out_is_directory_is_out_unwritable(tmp_path, capsys):
    out = tmp_path / "outdir"
    out.mkdir()
    code, payload = cli_build(EXAMPLE, write_sel(tmp_path, example_raw()), out, capsys)
    assert code == 2
    (err,) = payload["errors"]
    assert err["code"] == "out_unwritable"
    assert err["id"] == str(out)
    assert str(out) in err["message"]
    assert list(out.iterdir()) == []
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


@pytest.mark.parametrize("which", ["master", "selection", "master_other_spelling"])
def test_out_is_input_rejected(tmp_path, capsys, which):
    m_path = tmp_path / "master.yaml"
    m_path.write_bytes(EXAMPLE.read_bytes())
    sel = write_sel(tmp_path, example_raw())
    before = {p: p.read_bytes() for p in (m_path, sel)}
    out = {
        "master": m_path,
        "selection": sel,
        "master_other_spelling": tmp_path / "sub" / ".." / "master.yaml",
    }[which]
    (tmp_path / "sub").mkdir()
    code, payload = cli_build(m_path, sel, out, capsys)
    assert code == 2
    (err,) = payload["errors"]
    assert err["code"] == "out_is_input"
    assert err["id"] == str(out)
    assert str(out) in err["message"]
    assert {p: p.read_bytes() for p in (m_path, sel)} == before


def test_lone_surrogate_to_rejected_and_nothing_written(tmp_path, capsys):
    raw_text = json.dumps(example_raw())
    sel = tmp_path / "selection.json"
    swap = '{"target_id": "profile.0", "from": "Data engineer", "to": "\\ud800"}'
    sel.write_text(raw_text.replace('"swaps": [', '"swaps": [' + swap + ", ", 1), encoding="utf-8")
    out = tmp_path / "out.yaml"
    code, payload = cli_build(EXAMPLE, sel, out, capsys)
    assert code == 1
    (err,) = payload["errors"]
    assert err["code"] == "selection_schema"
    assert err["id"] == "swaps[0].to"
    assert not out.exists()


def test_unencodable_result_writes_nothing(tmp_path, monkeypatch, capsys):
    import cv_skills.build as b

    real = b.apply_swaps

    def bad(*args):
        texts, records = real(*args)
        records[0]["to"] = "\ud800"
        return texts, records

    monkeypatch.setattr(b, "apply_swaps", bad)
    out = tmp_path / "out.yaml"
    code, payload = cli_build(EXAMPLE, write_sel(tmp_path, example_raw()), out, capsys)
    assert code == 2
    assert payload["errors"][0]["code"] == "internal_error"
    assert not out.exists()


def test_output_mode_follows_umask(tmp_path):
    from cv_skills.build import build

    old = os.umask(0o027)
    try:
        out = tmp_path / "out.yaml"
        build(EXAMPLE, write_sel(tmp_path, example_raw()), out)
    finally:
        os.umask(old)
    assert out.stat().st_mode & 0o777 == 0o640


# --- swap_markup_swallows_text --------------------------------------------


def comma_master(tmp_path: Path) -> Path:
    """The example master with `**Go**, ` in experience.0.highlights.0 (clean: `,` is safe)."""
    text = EXAMPLE.read_text(encoding="utf-8").replace("in **Go** that", "in **Go**, Python and Rust that")
    p = tmp_path / "master.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_swap_creating_swallowed_text_exits_1(tmp_path, capsys):
    p = comma_master(tmp_path)
    m = load_master(p)
    raw = everything_raw(m)
    raw["swaps"] = [{"target_id": "experience.0.highlights.0", "from": ", ", "to": "; "}]
    out = tmp_path / "tailored.yaml"
    code, payload = cli_build(p, write_sel(tmp_path, raw), out, capsys)
    assert code == 1
    assert [(e["code"], e["id"]) for e in payload["errors"]] == [("swap_markup_swallows_text", "swaps[0]")]
    msg = payload["errors"][0]["message"]
    assert "experience.0.highlights.0" in msg and "`**Go**`" in msg and "`;`" in msg
    assert "not right after the markup" in msg
    assert not out.exists()


def test_swap_elsewhere_in_same_string_is_ok(tmp_path, capsys):
    p = comma_master(tmp_path)
    m = load_master(p)
    raw = everything_raw(m)
    raw["swaps"] = [
        {"target_id": "experience.0.highlights.0", "from": "40k", "to": "50k"},
        {"target_id": "experience.0.highlights.0", "from": "Rust", "to": "**Rust**"},
    ]
    out = tmp_path / "tailored.yaml"
    code, payload = cli_build(p, write_sel(tmp_path, raw), out, capsys)
    assert code == 0, payload
    assert out.exists()


def test_swallowing_swap_blamed_not_its_neighbour(tmp_path, capsys):
    p = comma_master(tmp_path)
    m = load_master(p)
    raw = everything_raw(m)
    raw["swaps"] = [
        {"target_id": "experience.0.highlights.0", "from": "40k", "to": "50k"},
        {"target_id": "experience.0.highlights.0", "from": "Rust that", "to": "*Rust*(x) that"},
    ]
    out = tmp_path / "tailored.yaml"
    code, payload = cli_build(p, write_sel(tmp_path, raw), out, capsys)
    assert code == 1
    assert [(e["code"], e["id"]) for e in payload["errors"]] == [("swap_markup_swallows_text", "swaps[1]")]
    assert not out.exists()


def test_swallowing_swap_combination_blamed_beside_a_lone_one(tmp_path, capsys):
    """swaps[0] alone creates `**Go**;`; no swap alone creates `*Rust*(`, so every swap on the target shares it."""
    p = comma_master(tmp_path)
    m = load_master(p)
    raw = everything_raw(m)
    raw["swaps"] = [
        {"target_id": "experience.0.highlights.0", "from": ", ", "to": "; "},
        {"target_id": "experience.0.highlights.0", "from": "Rust", "to": "*Rust"},
        {"target_id": "experience.0.highlights.0", "from": " that", "to": "*(x) that"},
    ]
    out = tmp_path / "tailored.yaml"
    code, payload = cli_build(p, write_sel(tmp_path, raw), out, capsys)
    assert code == 1
    got = [(e["id"], e["message"]) for e in payload["errors"]]
    assert [(id, "`**Go**`" in msg, "`*Rust*`" in msg) for id, msg in got] == [
        ("swaps[0]", True, False),
        ("swaps[0]", False, True),
        ("swaps[1]", False, True),
        ("swaps[2]", False, True),
    ]
    assert not out.exists()


def test_swallowing_swap_on_a_summary_spanning_lines(tmp_path, capsys):
    """A summary renders as one block, so a swap after markup that spans its lines is caught too."""
    text = EXAMPLE.read_text(encoding="utf-8").replace(
        "summary: Lead engineer for the analytics", 'summary: "Lead **engineer\\nfor** the analytics'
    ).replace("behind a forecasting product.", 'behind a forecasting product."', 1)
    p = tmp_path / "master.yaml"
    p.write_text(text, encoding="utf-8")
    m = load_master(p)
    raw = everything_raw(m)
    raw["swaps"] = [{"target_id": "experience.0.summary", "from": "** the", "to": "**; the"}]
    out = tmp_path / "tailored.yaml"
    code, payload = cli_build(p, write_sel(tmp_path, raw), out, capsys)
    assert code == 1
    assert [(e["code"], e["id"]) for e in payload["errors"]] == [("swap_markup_swallows_text", "swaps[0]")]


# --- selection_markup_swallows_text ---------------------------------------


def test_selected_details_re_pairing_markup_exits_1(tmp_path, capsys):
    """`*x, y*z, w*(q)` is clean; without its first item `*z, w*` pairs up and swallows `(`."""
    text = EXAMPLE.read_text(encoding="utf-8").replace(
        "details: Python (pandas, polars), Go, SQL, Rust", "details: '*x, y*z, w*(q)'"
    )
    p = tmp_path / "master.yaml"
    p.write_text(text, encoding="utf-8")
    m = load_master(p)
    raw = everything_raw(m)
    out = tmp_path / "tailored.yaml"
    assert cli_build(p, write_sel(tmp_path, raw), out, capsys)[0] == 0  # all items: as in the master
    si = next(i for i, s in enumerate(raw["sections"]) if s["id"] == "skills")
    raw["sections"][si]["entries"][0]["details"] = ["skills.0.details.1", "skills.0.details.2"]
    out.unlink()
    code, payload = cli_build(p, write_sel(tmp_path, raw), out, capsys)
    assert code == 1
    assert [(e["code"], e["id"]) for e in payload["errors"]] == [
        ("selection_markup_swallows_text", f"sections[{si}].entries[0].details")
    ]
    msg = payload["errors"][0]["message"]
    assert "skills.0" in msg and "`*z, w*`" in msg and "`(`" in msg and "\n" not in msg
    assert not out.exists()


# --- chosen titles (title_variants) ------------------------------------------


def build_titles(title_app, tmp_path, position=None):
    from cv_skills.build import build

    master_path, selection_path = title_app(position)
    out = tmp_path / "tailored.yaml"
    result = build(master_path, selection_path, out)
    return result, read_yaml(out.read_text(encoding="utf-8"))


@pytest.mark.parametrize("position", [None, "Senior Data Engineer"])
def test_default_title_is_the_master_position(title_app, tmp_path, position):
    result, doc = build_titles(title_app, tmp_path, position)
    entry = doc["cv"]["sections"]["experience"][0]
    assert entry["position"] == "Senior Data Engineer"
    assert "title_variants" not in entry
    assert result["titles"] == []


def test_chosen_variant_is_written_and_recorded(title_app, tmp_path):
    result, doc = build_titles(title_app, tmp_path, "Senior ML Engineer")
    entries = doc["cv"]["sections"]["experience"]
    assert entries[0]["position"] == "Senior ML Engineer"
    assert list(entries[0])[:2] == ["company", "position"]  # the title keeps its slot
    assert entries[1]["position"] == "Data Engineer"
    assert "title_variants" not in entries[0]
    assert result["titles"] == [
        {"entry": "experience.0", "default": "Senior Data Engineer", "chosen": "Senior ML Engineer"}
    ]


def test_title_not_listed_exits_1_and_writes_nothing(title_app, tmp_path, capsys):
    master_path, selection_path = title_app("Head of Data")
    out = tmp_path / "tailored.yaml"
    code, payload = cli_build(master_path, selection_path, out, capsys)
    assert code == 1
    assert [(e["code"], e["id"]) for e in payload["errors"]] == [
        ("position_not_allowed", "sections[1].entries[0].position")
    ]
    assert not out.exists()
