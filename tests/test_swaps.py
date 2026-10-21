import json
from pathlib import Path

import pytest

from cv_skills.master import load_master
from cv_skills.selection import parse_selection
from cv_skills.swaps import apply_swaps, check_swaps

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "master.yaml"
EXAMPLE_SELECTION = REPO / "examples" / "selection.json"

ALLOWED_KINDS = ("highlight", "summary", "detail", "text entry", "bullet")


@pytest.fixture(scope="module")
def master():
    return load_master(EXAMPLE)


def raw_with(*swaps: dict) -> dict:
    raw = json.loads(EXAMPLE_SELECTION.read_text(encoding="utf-8"))
    raw["swaps"] = list(swaps)
    return raw


def sw(target_id: str, from_: str, to: str) -> dict:
    return {"target_id": target_id, "from": from_, "to": to}


def check(master, *swaps: dict, raw: dict | None = None) -> list[dict]:
    raw = raw if raw is not None else raw_with(*swaps)
    return check_swaps(parse_selection(raw), master)


def apply(master, *swaps: dict):
    sel = parse_selection(raw_with(*swaps))
    assert check_swaps(sel, master) == []
    return apply_swaps(sel, master)


def assert_one(errors: list[dict], code: str, id: str) -> dict:
    err = next((e for e in errors if e["code"] == code and e["id"] == id), None)
    assert err is not None, f"no {code} with id {id} in {errors}"
    assert set(err) == {"code", "id", "message"}
    assert id in err["message"]
    return err


# --- check_swaps ----------------------------------------------------------


def test_example_swaps_are_valid(master):
    raw = json.loads(EXAMPLE_SELECTION.read_text(encoding="utf-8"))
    assert check(master, raw=raw) == []


def test_swap_target_unknown(master):
    errors = check(master, sw("experience.9.highlights.0", "a", "b"))
    assert_one(errors, "swap_target_unknown", "experience.9.highlights.0")


def test_swap_target_not_selected_highlight_dropped(master):
    # experience.0.highlights.3 is not in the example selection
    errors = check(master, sw("experience.0.highlights.3", "Mentored", "Coached"))
    err = assert_one(errors, "swap_target_not_selected", "experience.0.highlights.3")
    assert "select" in err["message"]


def test_swap_target_not_selected_entry_dropped(master):
    # honors.0 is dropped in the example selection, so its bullet is too
    errors = check(master, sw("honors.0.bullet", "Best Paper", "Top Paper"))
    assert_one(errors, "swap_target_not_selected", "honors.0.bullet")
    # experience.2 is dropped: its highlights are not selected either
    errors = check(master, sw("experience.2.highlights.0", "REST", "HTTP"))
    assert_one(errors, "swap_target_not_selected", "experience.2.highlights.0")


def test_swap_forbidden_field(master):
    errors = check(
        master,
        sw("experience.0.position", "Senior", "Staff"),
        sw("experience.0", "Quillfeather", "Quill"),
        sw("skills.0.label", "Languages", "Tongues"),
    )
    for id in ("experience.0.position", "experience.0", "skills.0.label"):
        err = assert_one(errors, "swap_forbidden_field", id)
        for kind in ALLOWED_KINDS:
            assert kind in err["message"]
    assert len(errors) == 3


def test_swap_from_empty(master):
    assert_one(check(master, sw("profile.0", "", "x")), "swap_from_empty", "profile.0")


def test_swap_to_empty(master):
    assert_one(check(master, sw("profile.0", "Data engineer", "")), "swap_to_empty", "profile.0")


def test_swap_from_not_found(master):
    err = assert_one(
        check(master, sw("experience.0.highlights.1", "Snowflake", "BigQuery")),
        "swap_from_not_found",
        "experience.0.highlights.1",
    )
    assert "Snowflake" in err["message"]


def test_swap_from_is_case_sensitive(master):
    errors = check(master, sw("experience.0.highlights.1", "duckdb", "DuckDB"))
    assert_one(errors, "swap_from_not_found", "experience.0.highlights.1")


def test_swap_from_matched_against_master_not_selection_order(master):
    # Matching uses the master text; a `from` that only exists after another swap is not found.
    errors = check(
        master,
        sw("experience.0.highlights.1", "Spark", "PySpark"),
        sw("experience.0.highlights.1", "PySpark to", "PySpark into"),
    )
    assert_one(errors, "swap_from_not_found", "experience.0.highlights.1")


def test_swap_from_ambiguous(master):
    # "from" occurs twice: "from 6 hours" and "from Spark"
    text = master.text_of("experience.0.highlights.1")
    assert text.count("from") == 2
    err = assert_one(
        check(master, sw("experience.0.highlights.1", "from", "out of")),
        "swap_from_ambiguous",
        "experience.0.highlights.1",
    )
    assert "surrounding words" in err["message"]


def test_swap_overlap(master):
    errors = check(
        master,
        sw("experience.0.highlights.1", "nightly batch", "daily batch"),
        sw("experience.0.highlights.1", "batch runtime", "job runtime"),
    )
    err = assert_one(errors, "swap_overlap", "experience.0.highlights.1")
    assert "swaps[0]" in err["message"] and "swaps[1]" in err["message"]


def test_all_swap_errors_reported(master):
    errors = check(
        master,
        sw("experience.0.highlights.1", "Snowflake", "BigQuery"),
        sw("profile.0", "", "x"),
        sw("nope.0", "a", "b"),
    )
    assert sorted(e["code"] for e in errors) == [
        "swap_from_empty",
        "swap_from_not_found",
        "swap_target_unknown",
    ]


# --- apply_swaps ----------------------------------------------------------


def test_apply_example(master):
    raw = json.loads(EXAMPLE_SELECTION.read_text(encoding="utf-8"))
    sel = parse_selection(raw)
    texts, records = apply_swaps(sel, master)
    assert set(texts) == {s.target_id for s in sel.swaps}
    assert [r["index"] for r in records] == list(range(len(sel.swaps)))
    for r in records:
        assert set(r) == {"index", "target_id", "from", "to", "start", "end"}
        assert texts[r["target_id"]][r["start"] : r["end"]] == r["to"]
    assert texts["skills.0.details.0"] == "Python (pandas and polars)"


def test_two_swaps_one_target_offsets(master):
    target = "experience.0.highlights.1"
    original = master.text_of(target)
    # listed in reverse text order; the earlier (in text) swap changes length
    texts, records = apply(
        master,
        sw(target, "DuckDB", "an embedded OLAP engine"),
        sw(target, "nightly", "overnight"),
    )
    expected = original.replace("DuckDB", "an embedded OLAP engine").replace("nightly", "overnight")
    assert texts[target] == expected
    by_index = {r["index"]: r for r in records}
    assert [r["index"] for r in records] == [0, 1]
    for r in records:
        assert texts[target][r["start"] : r["end"]] == r["to"]
    assert by_index[0]["start"] == expected.index("an embedded OLAP engine")
    assert by_index[1]["start"] == expected.index("overnight")
    assert by_index[0]["from"] == "DuckDB" and by_index[0]["target_id"] == target


def test_adjacent_swaps_do_not_overlap(master):
    target = "experience.0.highlights.1"
    texts, _ = apply(master, sw(target, "Cut", "Reduced"), sw(target, " nightly", " daily"))
    assert texts[target].startswith("Reduced daily batch")


def test_offsets_are_characters(master):
    # profile.1 has "–" (3 UTF-8 bytes) before the match
    target = "profile.1"
    original = master.text_of(target)
    assert "–" in original
    texts, records = apply(master, sw(target, "dashboards", "BI dashboards"))
    r = records[0]
    assert r["start"] == texts[target].index("BI dashboards")
    assert r["start"] > original.index("–")
    assert texts[target][r["start"] : r["end"]] == "BI dashboards"
    assert r["end"] - r["start"] == len("BI dashboards")

    # experience.1.highlights.1 has "ü"; it is selected in the example
    target = "experience.1.highlights.1"
    original = master.text_of(target)
    assert "ü" in original
    texts, records = apply(master, sw(target, "finance reports", "financial reporting"))
    r = records[0]
    assert len(original.encode()) != len(original)
    assert texts[target][r["start"] : r["end"]] == "financial reporting"
    assert r["start"] == original.index("finance reports")


def test_markdown_matched_raw(master):
    target = "experience.0.highlights.0"
    assert "**Go**" in master.text_of(target)
    texts, records = apply(master, sw(target, "**Go**", "**Golang**"))
    assert "**Golang**" in texts[target]
    r = records[0]
    assert texts[target][r["start"] : r["end"]] == "**Golang**"


def test_swap_on_detail_item_and_text_entry(master):
    texts, _ = apply(
        master,
        sw("skills.0.details.2", "SQL", "PostgreSQL SQL"),
        sw("profile.0", "eight years", "8 years"),
    )
    assert texts["skills.0.details.2"] == "PostgreSQL SQL"
    assert "8 years" in texts["profile.0"]


def test_no_swaps_yields_nothing(master):
    sel = parse_selection(raw_with())
    assert check_swaps(sel, master) == []
    assert apply_swaps(sel, master) == ({}, [])


@pytest.mark.parametrize("target,entry", [("honors.0.bullet", "honors.0"), ("projects.0.summary", "projects.0")])
def test_swap_target_not_selected_summary_bullet_says_select_entry(master, target, entry):
    err = assert_one(check(master, sw(target, "x", "y")), "swap_target_not_selected", target)
    assert f"select entry `{entry}`" in err["message"].lower()
    assert f"Select `{target}`" not in err["message"]


def test_swap_target_not_selected_highlight_still_says_select_it(master):
    err = assert_one(check(master, sw("experience.0.highlights.3", "Mentored", "Coached")), "swap_target_not_selected", "experience.0.highlights.3")
    assert "`experience.0.highlights.3`" in err["message"]


def test_swap_whitespace_only_from_and_to_messages(master):
    errors = check(master, sw("profile.0", " \t", "x"), sw("profile.1", "a", "  "))
    assert "empty or whitespace-only" in assert_one(errors, "swap_from_empty", "profile.0")["message"]
    assert "empty or whitespace-only" in assert_one(errors, "swap_to_empty", "profile.1")["message"]


# --- R9: a swap changes what an item says, not the CV's shape ---------------


@pytest.mark.parametrize("to", ["two\nlines", "a\r\nb", "tab\there", "nul\x00", "nel\x85x", "end\n"])
def test_swap_to_multiline_rejected(master, to):
    errors = check(master, sw("experience.0.highlights.2", "four ML teams", to))
    err = assert_one(errors, "swap_to_multiline", "experience.0.highlights.2")
    assert "swaps[0]" in err["message"]
    assert "one line" in err["message"]


@pytest.mark.parametrize("target,frm", [("profile.0", "eight years"), ("experience.0.summary", "forecasting product")])
def test_swap_to_multiline_rejected_on_every_kind(master, target, frm):
    assert_one(check(master, sw(target, frm, "a\nb")), "swap_to_multiline", target)


def test_swap_to_comma_in_detail_rejected(master):
    errors = check(master, sw("skills.0.details.0", "Python", "Python, Java"))
    err = assert_one(errors, "swap_to_comma_in_detail", "skills.0.details.0")
    assert "swaps[0]" in err["message"]
    assert "one item" in err["message"] and "comma" in err["message"]


def test_comma_in_to_allowed_outside_details(master):
    assert check(master, sw("experience.0.highlights.2", "four ML teams", "four ML teams, then six")) == []


def test_markdown_in_to_accepted(master):
    assert check(master, sw("experience.0.highlights.2", "four ML teams", "**four** ML teams")) == []
    texts, _ = apply(master, sw("experience.0.highlights.2", "four ML teams", "**four** ML teams"))
    assert "**four** ML teams" in texts["experience.0.highlights.2"]
