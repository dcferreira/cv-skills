import json
from pathlib import Path

import pytest

from cv_skills.cli import Failure
from cv_skills.master import load_master
from cv_skills.selection import (
    EntrySel,
    SectionSel,
    Selection,
    Swap,
    check_structure,
    parse_selection,
    selected_ids,
)

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "master.yaml"
EXAMPLE_SELECTION = REPO / "examples" / "selection.json"


@pytest.fixture(scope="module")
def master():
    return load_master(EXAMPLE)


def raw_example() -> dict:
    return json.loads(EXAMPLE_SELECTION.read_text(encoding="utf-8"))


def section(raw: dict, sid: str) -> dict:
    return next(s for s in raw["sections"] if s["id"] == sid)


def entry(raw: dict, eid: str) -> dict:
    sid = eid.split(".")[0]
    return next(e for e in section(raw, sid)["entries"] if e["id"] == eid)


def errors_for(raw: dict, master) -> list[dict]:
    return check_structure(parse_selection(raw), master)


def only(errors: list[dict], code: str) -> list[dict]:
    return [e for e in errors if e["code"] == code]


def assert_one(errors: list[dict], code: str, id: str) -> dict:
    matching = only(errors, code)
    assert matching, f"no {code} in {errors}"
    err = next((e for e in matching if e["id"] == id), None)
    assert err is not None, f"no {code} with id {id} in {errors}"
    assert set(err) == {"code", "id", "message"}
    assert id in err["message"]
    return err


# --- parse_selection ------------------------------------------------------


def test_parse_example_selection():
    sel = parse_selection(raw_example())
    assert isinstance(sel, Selection)
    assert sel.master_hash.startswith("sha256:")
    assert isinstance(sel.sections[0], SectionSel)
    assert [s.id for s in sel.sections][:2] == ["profile", "experience"]
    exp = sel.sections[1]
    assert isinstance(exp.entries[0], EntrySel)
    assert exp.entries[0].highlights[0] == "experience.0.highlights.2"
    assert exp.entries[0].details is None
    profile_entry = sel.sections[0].entries[0]
    assert profile_entry.highlights is None and profile_entry.details is None
    assert isinstance(sel.swaps[0], Swap)
    assert sel.swaps[0].target_id == "experience.0.summary"
    assert sel.swaps[0].from_ == "forecasting product"


def test_selection_schema_unknown_top_level_key():
    raw = raw_example()
    raw["notes"] = "hi"
    with pytest.raises(Failure) as exc:
        parse_selection(raw)
    assert exc.value.code == 1
    err = assert_one(exc.value.errors, "selection_schema", "notes")
    assert "notes" in err["message"]


def test_selection_schema_swaps_not_a_list():
    raw = raw_example()
    raw["swaps"] = {"target_id": "profile.0"}
    with pytest.raises(Failure) as exc:
        parse_selection(raw)
    assert exc.value.code == 1
    assert_one(exc.value.errors, "selection_schema", "swaps")


def test_selection_schema_reports_all_and_uses_json_paths():
    raw = raw_example()
    raw["sections"][1]["entries"][0]["colour"] = "red"
    raw["swaps"][0]["from"] = 3
    del raw["master_hash"]
    with pytest.raises(Failure) as exc:
        parse_selection(raw)
    ids = {e["id"] for e in exc.value.errors}
    assert {"sections[1].entries[0].colour", "swaps[0].from", "master_hash"} <= ids
    assert all(e["code"] == "selection_schema" for e in exc.value.errors)


def test_selection_schema_not_an_object():
    with pytest.raises(Failure) as exc:
        parse_selection(["experience"])
    assert exc.value.code == 1
    assert exc.value.errors[0]["code"] == "selection_schema"


def test_swaps_key_optional():
    raw = raw_example()
    del raw["swaps"]
    assert parse_selection(raw).swaps == []


# --- check_structure ------------------------------------------------------


def test_valid_example_selection_has_no_errors(master):
    raw = raw_example()
    assert raw["master_hash"] == master.hash
    assert errors_for(raw, master) == []


def test_hash_mismatch(master):
    raw = raw_example()
    raw["master_hash"] = "sha256:" + "0" * 64
    errors = errors_for(raw, master)
    err = assert_one(errors, "hash_mismatch", "master_hash")
    assert master.hash in err["message"]


def test_unknown_id_entry(master):
    raw = raw_example()
    section(raw, "honors")["entries"].append({"id": "honors.7"})
    assert_one(errors_for(raw, master), "unknown_id", "honors.7")


def test_unknown_id_section(master):
    raw = raw_example()
    raw["sections"].append({"id": "awards", "entries": [{"id": "awards.0"}]})
    assert_one(errors_for(raw, master), "unknown_id", "awards")


def test_unknown_id_highlight(master):
    raw = raw_example()
    entry(raw, "experience.1")["highlights"].append("experience.1.highlights.9")
    assert_one(errors_for(raw, master), "unknown_id", "experience.1.highlights.9")


def test_wrong_parent_highlight_of_another_entry(master):
    raw = raw_example()
    entry(raw, "experience.1")["highlights"].append("experience.0.highlights.3")
    err = assert_one(errors_for(raw, master), "wrong_parent", "experience.0.highlights.3")
    assert "experience.0" in err["message"]


def test_wrong_parent_entry_in_other_section(master):
    raw = raw_example()
    section(raw, "honors")["entries"].append({"id": "profile.0"})
    section(raw, "profile")["entries"] = [{"id": "profile.1"}]
    assert_one(errors_for(raw, master), "wrong_parent", "profile.0")


def test_duplicate_id(master):
    raw = raw_example()
    entry(raw, "experience.1")["highlights"].append("experience.1.highlights.0")
    assert_one(errors_for(raw, master), "duplicate_id", "experience.1.highlights.0")


def test_duplicate_section(master):
    raw = raw_example()
    raw["sections"].append({"id": "honors", "entries": [{"id": "honors.0"}]})
    assert_one(errors_for(raw, master), "duplicate_id", "honors")


def test_highlights_missing(master):
    raw = raw_example()
    del entry(raw, "experience.1")["highlights"]
    err = assert_one(errors_for(raw, master), "highlights_missing", "experience.1")
    assert "[]" in err["message"]


def test_highlights_empty_list_is_allowed(master):
    raw = raw_example()
    entry(raw, "experience.1")["highlights"] = []
    assert errors_for(raw, master) == []


def test_details_missing(master):
    raw = raw_example()
    del entry(raw, "skills.0")["details"]
    err = assert_one(errors_for(raw, master), "details_missing", "skills.0")
    assert "[]" in err["message"]


def test_not_selectable_highlights_on_entry_without_highlights(master):
    raw = raw_example()
    entry(raw, "skills.0")["highlights"] = []
    entry(raw, "profile.0")["highlights"] = []
    errors = errors_for(raw, master)
    assert_one(errors, "not_selectable", "skills.0")
    assert_one(errors, "not_selectable", "profile.0")


def test_not_selectable_details_on_entry_without_details(master):
    raw = raw_example()
    entry(raw, "experience.0")["details"] = []
    assert_one(errors_for(raw, master), "not_selectable", "experience.0")


def test_not_selectable_summary_id(master):
    raw = raw_example()
    entry(raw, "experience.0")["highlights"].append("experience.0.summary")
    err = assert_one(errors_for(raw, master), "not_selectable", "experience.0.summary")
    assert "experience.0" in err["message"]


def test_not_selectable_bullet_as_entry(master):
    raw = raw_example()
    section(raw, "honors")["entries"].append({"id": "honors.0.bullet"})
    assert_one(errors_for(raw, master), "not_selectable", "honors.0.bullet")


def test_empty_section(master):
    raw = raw_example()
    section(raw, "honors")["entries"] = []
    assert_one(errors_for(raw, master), "empty_section", "honors")


def test_pin_missing_entry_absent(master):
    raw = raw_example()
    exp = section(raw, "experience")
    exp["entries"] = [e for e in exp["entries"] if e["id"] != "experience.0"]
    raw["swaps"] = []
    errors = errors_for(raw, master)
    err = assert_one(errors, "pin_missing", "experience.0")
    assert "pin" in err["message"]


def test_pin_missing_whole_section_absent(master):
    raw = raw_example()
    raw["sections"] = [s for s in raw["sections"] if s["id"] != "education"]
    errors = errors_for(raw, master)
    err = assert_one(errors, "pin_missing", "education.0")
    assert "education" in err["message"]


def test_pinned_entry_highlights_stay_selectable(master):
    raw = raw_example()
    entry(raw, "experience.0")["highlights"] = ["experience.0.highlights.3"]
    entry(raw, "education.0")["highlights"] = []
    assert errors_for(raw, master) == []


def test_errors_are_all_reported(master):
    raw = raw_example()
    del entry(raw, "experience.1")["highlights"]
    section(raw, "honors")["entries"] = []
    codes = sorted(e["code"] for e in errors_for(raw, master))
    assert codes == ["empty_section", "highlights_missing"]


# --- selected_ids ---------------------------------------------------------


def test_selected_ids_include_implied_summary_and_bullet():
    ids = selected_ids(parse_selection(raw_example()))
    assert {"experience", "experience.0", "experience.0.highlights.2"} <= ids
    assert "experience.0.summary" in ids
    assert "honors.1.bullet" in ids
    assert "skills.0.details.2" in ids
    assert "honors.0" not in ids and "honors.0.bullet" not in ids
    assert "experience.2" not in ids
    assert "projects" not in ids


def test_selection_schema_rejects_non_utf8_strings():
    raw = raw_example()
    raw["swaps"][0]["to"] = "\ud800"
    raw["swaps"][0]["from"] = "a\udfffb"
    raw["sections"][0]["entries"][0]["id"] = "profile.\ud800"
    raw["sections"][1]["entries"][0]["highlights"][0] = "\udc00"
    raw["\ud800bad"] = 1
    with pytest.raises(Failure) as exc:
        parse_selection(raw)
    assert exc.value.code == 1
    ids = {e["id"] for e in exc.value.errors}
    assert {
        "swaps[0].to",
        "swaps[0].from",
        "sections[0].entries[0].id",
        "sections[1].entries[0].highlights[0]",
    } <= ids
    assert all(e["code"] == "selection_schema" for e in exc.value.errors)
    # every error is itself emittable as UTF-8 JSON
    json.dumps(exc.value.errors, ensure_ascii=False).encode("utf-8")


# --- position (title variants) -------------------------------------------------


def title_errors(title_app, position: object, where: str = "experience.0", **kw) -> list[dict]:
    """Errors of the example selection on the variants master, with `where`'s `"position"` set."""
    master_path, selection_path = title_app(**kw)
    raw = json.loads(selection_path.read_text(encoding="utf-8"))
    entry(raw, where)["position"] = position
    return errors_for(raw, load_master(master_path))


@pytest.mark.parametrize("title", ["Senior Data Engineer", "Senior Analytics Engineer", "Senior ML Engineer"])
def test_position_default_or_variant_is_allowed(title_app, title):
    assert title_errors(title_app, title) == []


def test_position_is_parsed():
    raw = raw_example()
    entry(raw, "experience.0")["position"] = "Senior ML Engineer"
    sel = parse_selection(raw)
    assert sel.sections[1].entries[0].position == "Senior ML Engineer"
    assert sel.sections[1].entries[1].position is None


def test_position_must_be_a_string(title_app):
    master_path, selection_path = title_app()
    raw = json.loads(selection_path.read_text(encoding="utf-8"))
    entry(raw, "experience.0")["position"] = ["Senior ML Engineer"]
    with pytest.raises(Failure) as exc:
        parse_selection(raw)
    assert [(e["code"], e["id"]) for e in exc.value.errors] == [("selection_schema", "sections[1].entries[0].position")]


@pytest.mark.parametrize("title", ["Staff ML Engineer", "senior ml engineer", "Senior ML Engineer ", "Senior  ML Engineer"])
def test_position_not_listed_is_rejected(title_app, title):
    errors = title_errors(title_app, title)
    assert [e["code"] for e in errors] == ["position_not_allowed"]
    err = assert_one(errors, "position_not_allowed", "sections[1].entries[0].position")
    for allowed in ("`Senior Data Engineer`", "`Senior Analytics Engineer`", "`Senior ML Engineer`"):
        assert allowed in err["message"]
    assert "experience.0" in err["message"] and "\n" not in err["message"]


def test_variants_are_per_entry(title_app):
    # experience.1 has no variants: only its own position is allowed.
    assert title_errors(title_app, "Data Engineer", "experience.1") == []
    errors = title_errors(title_app, "Senior ML Engineer", "experience.1")
    err = assert_one(errors, "position_not_allowed", "sections[1].entries[1].position")
    assert "`Data Engineer`" in err["message"]


@pytest.mark.parametrize("where", ["profile.0", "education.0"])
def test_position_on_entry_without_one_is_not_selectable(title_app, where):
    errors = title_errors(title_app, "Senior ML Engineer", where)
    assert [e["code"] for e in errors] == ["not_selectable"]
    assert errors[0]["id"] == where


def test_position_node_cannot_be_listed(title_app):
    master_path, selection_path = title_app()
    raw = json.loads(selection_path.read_text(encoding="utf-8"))
    entry(raw, "experience.0")["highlights"].append("experience.0.position")
    errors = errors_for(raw, load_master(master_path))
    err = assert_one(errors, "not_selectable", "experience.0.position")
    assert '"position"' in err["message"]


def test_swap_on_position_with_variants_stays_forbidden(title_app):
    from cv_skills.swaps import check_swaps

    master_path, selection_path = title_app()
    raw = json.loads(selection_path.read_text(encoding="utf-8"))
    raw["swaps"] = [{"target_id": "experience.0.position", "from": "Senior", "to": "Staff"}]
    errors = check_swaps(parse_selection(raw), load_master(master_path))
    assert [(e["code"], e["id"]) for e in errors] == [("swap_forbidden_field", "experience.0.position")]
