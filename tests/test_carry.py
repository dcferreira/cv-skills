"""Carrying a selection onto a changed master (`cv_skills.carry`): matched by text, never by position alone."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from cv_skills.build import build, read_selection
from cv_skills.carry import SNAPSHOT, carry_selection, old_master
from cv_skills.cli import Failure
from cv_skills.master import load_master
from cv_skills.summary import Sentence

REPO = Path(__file__).resolve().parent.parent
MASTER_TEXT = (REPO / "examples/master.yaml").read_text(encoding="utf-8")
SELECTION = json.loads((REPO / "examples/selection.json").read_text(encoding="utf-8"))

FEATURE_STORE = "          - Built a feature store used by four ML teams for training and online serving\n"
GO_SERVICE = "          - Designed a streaming ingestion service in **Go** that processes 40k events per second with exactly-once delivery\n"


def hash_of(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def edited(*replace: tuple[str, str]) -> str:
    text = MASTER_TEXT
    for a, b in replace:
        assert text.count(a) == 1, a
        text = text.replace(a, b)
    return text


@pytest.fixture
def masters(tmp_path):
    """`masters(new_text)`: (old Master, new Master), the old one the example master."""

    def make(new_text: str):
        (tmp_path / "old.yaml").write_text(MASTER_TEXT, encoding="utf-8")
        (tmp_path / "new.yaml").write_text(new_text, encoding="utf-8")
        return load_master(tmp_path / "old.yaml"), load_master(tmp_path / "new.yaml")

    return make


def sel_of(raw: dict):
    from cv_skills.selection import parse_selection

    return parse_selection(raw)


def entry(raw: dict, eid: str) -> dict:
    return next(e for s in raw["sections"] for e in s["entries"] if e["id"] == eid)


def test_unchanged_master_carries_everything_as_is(masters):
    old, new = masters(MASTER_TEXT)
    out = carry_selection(old, new, sel_of(SELECTION))
    assert out.selection == SELECTION
    assert out.report["changed"] == out.report["dropped"] == out.report["new"] == []
    assert out.report["swaps_dropped"] == out.report["titles_reset"] == []


def test_new_master_hash_is_written(masters):
    text = edited(("Lisbon, Portugal\n  email", "Lisboa, Portugal\n  email"))
    old, new = masters(text)
    out = carry_selection(old, new, sel_of(SELECTION))
    assert out.selection["master_hash"] == hash_of(text)
    assert out.report["old_master_hash"] == old.hash and out.report["master_hash"] == new.hash


def test_an_inserted_bullet_shifts_ids_but_keeps_the_same_texts(masters):
    new_bullet = "          - Ran the on-call rotation for the data platform\n"
    old, new = masters(edited((GO_SERVICE, new_bullet + GO_SERVICE)))
    out = carry_selection(old, new, sel_of(SELECTION))
    # highlights 2, 0, 1 are now 3, 1, 2: the same three bullets, in the reviewed order.
    assert entry(out.selection, "experience.0")["highlights"] == [
        "experience.0.highlights.3",
        "experience.0.highlights.1",
        "experience.0.highlights.2",
    ]
    assert out.report["changed"] == [] and out.report["dropped"] == []
    assert out.report["new"] == [{"id": "experience.0.highlights.0", "text": new_bullet.strip()[2:]}]
    # the swap on the feature-store bullet follows it
    assert out.selection["swaps"][1]["target_id"] == "experience.0.highlights.3"


def test_an_inserted_entry_shifts_entry_ids(masters):
    new_role = (
        "      - company: Northgate Data\n"
        "        position: Staff Data Engineer\n"
        "        start_date: 2025-01\n"
        "        end_date: present\n"
        "        highlights:\n"
        "          - Leads the data platform group\n"
    )
    anchor = "      - company: Quillfeather Labs\n"
    old, new = masters(edited((anchor, new_role + anchor)))
    out = carry_selection(old, new, sel_of(SELECTION))
    exp = next(s for s in out.selection["sections"] if s["id"] == "experience")
    assert [e["id"] for e in exp["entries"]] == ["experience.1", "experience.2"]
    assert entry(out.selection, "experience.1")["highlights"][0] == "experience.1.highlights.2"
    assert out.report["new"] == [{"id": "experience.0", "text": "Northgate Data, Staff Data Engineer"}]
    build_ok(out, new)


def test_a_bullet_reworded_in_place_is_carried_and_flagged(masters):
    reworded = "          - Built a feature store used by five ML teams for training and online serving\n"
    old, new = masters(edited((FEATURE_STORE, reworded)))
    out = carry_selection(old, new, sel_of(SELECTION))
    assert entry(out.selection, "experience.0")["highlights"][0] == "experience.0.highlights.2"
    assert out.report["changed"] == [
        {
            "old_id": "experience.0.highlights.2",
            "id": "experience.0.highlights.2",
            "was": FEATURE_STORE.strip()[2:],
            "now": reworded.strip()[2:],
        }
    ]
    # the swap's `from` ("four ML teams") is gone from the new text, so the swap is not carried
    assert [s["target_id"] for s in out.selection["swaps"]] == ["experience.0.summary", "skills.0.details.0"]
    assert out.report["swaps_dropped"][0]["index"] == 2
    assert "four ML teams" in out.report["swaps_dropped"][0]["why"]
    build_ok(out, new)


def test_a_removed_bullet_is_dropped_and_reported(masters):
    old, new = masters(edited((FEATURE_STORE, "")))
    out = carry_selection(old, new, sel_of(SELECTION))
    assert entry(out.selection, "experience.0")["highlights"] == ["experience.0.highlights.0", "experience.0.highlights.1"]
    assert [d["old_id"] for d in out.report["dropped"]] == ["experience.0.highlights.2"]
    assert out.report["dropped"][0]["was"] == FEATURE_STORE.strip()[2:]
    assert out.report["swaps_dropped"][0]["target_id"] == "experience.0.highlights.2"
    assert out.report["changed"] == []
    build_ok(out, new)


def test_a_moved_bullet_is_matched_by_text(masters):
    # The feature-store bullet moves to the top: the old ID 2 is now 0, and nothing is "changed".
    old, new = masters(edited((FEATURE_STORE, ""), (GO_SERVICE, FEATURE_STORE + GO_SERVICE)))
    out = carry_selection(old, new, sel_of(SELECTION))
    assert entry(out.selection, "experience.0")["highlights"] == [
        "experience.0.highlights.0",
        "experience.0.highlights.1",
        "experience.0.highlights.2",
    ]
    assert out.report["changed"] == out.report["dropped"] == out.report["new"] == []


def test_skills_items_are_matched_by_text(masters):
    old, new = masters(edited(("details: Kafka, Airflow,", "details: Flink, Kafka, Airflow,")))
    out = carry_selection(old, new, sel_of(SELECTION))
    # Kafka, Airflow, DuckDB (old 0, 1, 2) are now 1, 2, 3; the selection listed 2, 0, 1
    assert entry(out.selection, "skills.1")["details"] == ["skills.1.details.3", "skills.1.details.1", "skills.1.details.2"]
    assert out.report["new"] == [{"id": "skills.1.details.0", "text": "Flink"}]


def test_a_chosen_title_no_longer_listed_falls_back_to_the_default(masters):
    raw = json.loads(json.dumps(SELECTION))
    entry(raw, "experience.1")["position"] = "Analytics Engineer"
    old, new = masters(edited(("title_variants: Analytics Engineer; Data Platform Engineer", "title_variants: Data Platform Engineer")))
    out = carry_selection(old, new, sel_of(raw))
    assert "position" not in entry(out.selection, "experience.1")
    assert out.report["titles_reset"] == [{"entry": "experience.1", "was": "Analytics Engineer", "now": "Data Engineer"}]


def test_a_chosen_title_still_listed_is_kept(masters):
    raw = json.loads(json.dumps(SELECTION))
    entry(raw, "experience.1")["position"] = "Data Platform Engineer"
    old, new = masters(MASTER_TEXT)
    out = carry_selection(old, new, sel_of(raw))
    assert entry(out.selection, "experience.1")["position"] == "Data Platform Engineer"


def test_a_removed_section_drops_its_entries(masters):
    honors = (
        "    honors:\n"
        "      - bullet: Best Paper Award, Eastmere Data Systems Workshop (2017)\n"
        "      - bullet: Speaker at the fictional Atlantic Data Engineering Meetup (2023)\n"
    )
    old, new = masters(edited((honors, "")))
    out = carry_selection(old, new, sel_of(SELECTION))
    assert "honors" not in [s["id"] for s in out.selection["sections"]]
    assert [d["old_id"] for d in out.report["dropped"]] == ["honors.1"]
    build_ok(out, new)


def test_an_entry_whose_bullets_all_went_keeps_its_key_rules(masters):
    # projects.0 is not selected; drop education's only highlight: the entry stays, with no highlights key.
    old, new = masters(edited(("        highlights:\n          - 'Thesis: Incremental view maintenance for append-only event logs'\n", "")))
    out = carry_selection(old, new, sel_of(SELECTION))
    assert entry(out.selection, "education.0") == {"id": "education.0"}
    build_ok(out, new)


def test_summary_sources_are_remapped(masters):
    new_bullet = "          - Ran the on-call rotation for the data platform\n"
    old, new = masters(edited((GO_SERVICE, new_bullet + GO_SERVICE)))
    sentences = [Sentence("Built a feature store.", ("experience.0.highlights.2", "experience.0", "guide.facts.0"))]
    out = carry_selection(old, new, sel_of(SELECTION), sentences)
    assert out.summary == {
        "sentences": [{"text": "Built a feature store.", "sources": ["experience.0.highlights.3", "experience.0", "guide.facts.0"]}]
    }
    assert out.report["summary"] == "carried"


def test_a_summary_citing_a_removed_item_is_not_carried(masters):
    old, new = masters(edited((FEATURE_STORE, "")))
    sentences = [Sentence("Built a feature store.", ("experience.0.highlights.2",))]
    out = carry_selection(old, new, sel_of(SELECTION), sentences)
    assert out.summary is None
    assert out.report["summary"].startswith("not carried")
    assert "experience.0.highlights.2" in out.report["summary"]


def test_a_summary_citing_a_changed_item_is_carried_and_flagged(masters):
    reworded = "          - Built a feature store used by five ML teams for training and online serving\n"
    old, new = masters(edited((FEATURE_STORE, reworded)))
    sentences = [Sentence("Built a feature store.", ("experience.0.highlights.2",))]
    out = carry_selection(old, new, sel_of(SELECTION), sentences)
    assert out.summary is not None
    assert "cites changed" in out.report["summary"]


def test_the_selection_must_be_for_the_old_master(masters):
    old, new = masters(MASTER_TEXT)
    raw = dict(SELECTION, master_hash="sha256:" + "0" * 64)
    with pytest.raises(Failure) as e:
        carry_selection(old, new, sel_of(raw))
    assert {x["code"] for x in e.value.errors} == {"hash_mismatch"}


def test_an_invalid_selection_is_not_carried(masters):
    old, new = masters(MASTER_TEXT)
    raw = json.loads(json.dumps(SELECTION))
    entry(raw, "experience.0")["highlights"].append("experience.0.highlights.9")
    with pytest.raises(Failure) as e:
        carry_selection(old, new, sel_of(raw))
    assert {x["code"] for x in e.value.errors} == {"unknown_id"}


def build_ok(out, new) -> None:
    """The carried selection builds against the new master: master text plus listed swaps only."""
    path = new.path.parent / "carried.json"
    path.write_text(json.dumps(out.selection), encoding="utf-8")
    assert build(new.path, path, new.path.parent / "carried.yaml")["ok"]


# --- finding the old master ---------------------------------------------------


def test_old_master_from_the_snapshot(tmp_path):
    folder = tmp_path / "app"
    folder.mkdir()
    (folder / SNAPSHOT).write_text(MASTER_TEXT, encoding="utf-8")
    (tmp_path / "master.yaml").write_text(edited((FEATURE_STORE, "")), encoding="utf-8")
    m = old_master(folder, tmp_path / "master.yaml", SELECTION["master_hash"])
    assert m.hash == SELECTION["master_hash"]


def test_a_snapshot_with_another_hash_is_not_used(tmp_path):
    folder = tmp_path / "app"
    folder.mkdir()
    (folder / SNAPSHOT).write_text(edited((FEATURE_STORE, "")), encoding="utf-8")  # e.g. edited by hand
    (tmp_path / "master.yaml").write_text(MASTER_TEXT, encoding="utf-8")
    with pytest.raises(Failure) as e:
        old_master(folder, tmp_path / "master.yaml", SELECTION["master_hash"])
    assert [x["code"] for x in e.value.errors] == ["carry_master_not_found"]
    assert SNAPSHOT in e.value.errors[0]["message"]


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True, capture_output=True)


def test_old_master_from_git_history(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "master.yaml").write_text(MASTER_TEXT, encoding="utf-8")
    git(tmp_path, "add", "master.yaml")
    git(tmp_path, "commit", "-q", "-m", "old")
    (tmp_path / "master.yaml").write_text(edited((FEATURE_STORE, "")), encoding="utf-8")
    git(tmp_path, "commit", "-q", "-am", "new")
    folder = tmp_path / "app"
    folder.mkdir()  # an application folder from before snapshots
    m = old_master(folder, tmp_path / "master.yaml", SELECTION["master_hash"])
    assert m.hash == SELECTION["master_hash"]


def test_old_master_not_found(tmp_path):
    folder = tmp_path / "app"
    folder.mkdir()
    (tmp_path / "master.yaml").write_text(MASTER_TEXT, encoding="utf-8")
    with pytest.raises(Failure) as e:
        old_master(folder, tmp_path / "master.yaml", "sha256:" + "0" * 64)
    assert [x["code"] for x in e.value.errors] == ["carry_master_not_found"]


def test_carried_selection_reads_back(masters, tmp_path):
    old, new = masters(MASTER_TEXT)
    out = carry_selection(old, new, sel_of(SELECTION))
    (tmp_path / "s.json").write_text(json.dumps(out.selection))
    assert read_selection(tmp_path / "s.json").master_hash == new.hash


def test_a_reworded_bullet_after_an_inserted_one_is_still_an_edit(masters):
    # Both edits in one entry: a bullet inserted at the top shifts the reworded one from ID 2 to ID 3.
    new_bullet = "          - Ran the on-call rotation for the data platform\n"
    reworded = "          - Built a feature store used by five ML teams for training and online serving\n"
    old, new = masters(edited((GO_SERVICE, new_bullet + GO_SERVICE), (FEATURE_STORE, reworded)))
    out = carry_selection(old, new, sel_of(SELECTION))
    assert entry(out.selection, "experience.0")["highlights"][0] == "experience.0.highlights.3"
    assert [(c["old_id"], c["id"]) for c in out.report["changed"]] == [("experience.0.highlights.2", "experience.0.highlights.3")]
    assert out.report["dropped"] == []
    assert out.report["new"] == [{"id": "experience.0.highlights.0", "text": new_bullet.strip()[2:]}]


def test_an_edit_next_to_an_insert_is_not_guessed(masters):
    # One bullet reworded and another inserted right after it: two new texts for one old one, so no pairing.
    extra = "          - Ran the on-call rotation for the data platform\n"
    reworded = "          - Built a feature store used by five ML teams for training and online serving\n"
    old, new = masters(edited((FEATURE_STORE, reworded + extra)))
    out = carry_selection(old, new, sel_of(SELECTION))
    assert out.report["changed"] == []
    assert [d["old_id"] for d in out.report["dropped"]] == ["experience.0.highlights.2"]
    assert [n["id"] for n in out.report["new"]] == ["experience.0.highlights.2", "experience.0.highlights.3"]


ENTRY_SUMMARY = "        summary: Lead engineer for the analytics platform behind a forecasting product.\n"


def test_an_edited_entry_summary_is_flagged(masters):
    new_summary = "        summary: Engineer on the analytics platform behind a forecasting product.\n"
    old, new = masters(edited((ENTRY_SUMMARY, new_summary)))
    out = carry_selection(old, new, sel_of(SELECTION))
    assert [(c["old_id"], c["now"]) for c in out.report["changed"]] == [
        ("experience.0.summary", "Engineer on the analytics platform behind a forecasting product.")
    ]
    assert out.selection["swaps"][0]["target_id"] == "experience.0.summary"  # its "from" is still there, once


def test_a_summary_citing_a_removed_entry_summary_is_not_carried(masters):
    old, new = masters(edited((ENTRY_SUMMARY, "")))
    sentences = [Sentence("Lead engineer.", ("experience.0.summary",))]
    out = carry_selection(old, new, sel_of(SELECTION), sentences)
    assert out.summary is None and "experience.0.summary" in out.report["summary"]


def test_a_summary_citing_a_dropped_entry_is_not_carried(masters):
    # A new role added before experience.1, whose company is renamed: one old entry against two new ones in
    # that block, so nothing is paired and experience.1 is dropped.
    sentences = [Sentence("Data engineer.", ("experience.1",))]
    new_role = "      - company: Northgate Data\n        position: Staff Data Engineer\n        start_date: 2025-01\n        end_date: present\n"
    old, new = masters(
        edited(("      - company: Brightwater Logistics", new_role + "      - company: Brightwater Freight"))
    )
    out = carry_selection(old, new, sel_of(SELECTION), sentences)
    assert out.summary is None
    # the dropped entry's bullets are listed with it
    assert [d["old_id"] for d in out.report["dropped"]] == [
        "experience.1",
        "experience.1.highlights.1",
        "experience.1.highlights.0",
    ]
