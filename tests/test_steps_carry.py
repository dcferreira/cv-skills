"""The `tailor` step adapters for carrying a run across a master change (`from_selection`), and for keeping the
recruiter's gate about the CV under review."""

import json
import shutil
from pathlib import Path

import pytest
from test_steps import (  # noqa: F401  (fixtures)
    A,
    FINAL,
    MASTER,
    POSTING,
    R1_BAD,
    R1_OK,
    R2_OK,
    R3,
    REQS,
    SELECTION,
    app,
    codes,
    repo,
    run,
    sha,
    write_guide,
    write_review,
)

from cv_skills import steps
from cv_skills.carry import SNAPSHOT

OLD = "applications/2026-01-05-acme-analytics-senior-data-engineer"
FEATURE_STORE = "          - Built a feature store used by four ML teams for training and online serving\n"
NEW_BULLET = "          - Ran the on-call rotation for the data platform\n"
GO_SERVICE = "          - Designed a streaming ingestion service in **Go**"


@pytest.fixture
def old_run(repo):
    """An earlier run's application folder (example master, example selection), then the master edited: a
    bullet inserted at the top of experience.0, so every later highlight ID there shifts by one."""
    old = repo / OLD
    old.mkdir(parents=True)
    shutil.copy(SELECTION, old / "selection.json")
    shutil.copy(MASTER, old / SNAPSHOT)
    (old / "posting.txt").write_text(POSTING.strip() + "\n")
    (old / "posting-url.txt").write_text("https://jobs.example.com/acme/1\n")
    (old / "requirements.json").write_text(json.dumps(REQS))
    text = (repo / "master.yaml").read_text()
    (repo / "master.yaml").write_text(text.replace(GO_SERVICE, NEW_BULLET + GO_SERVICE, 1))
    return old


# --- fetch: the master snapshot, and a carried run's inputs ----------------------


def test_fetch_snapshots_the_master(repo, capsys):
    (repo / "p.txt").write_text(POSTING)
    assert run(capsys, "fetch", "p.txt", "run1", "", "master.yaml")[0] == 0
    assert (repo / A / SNAPSHOT).read_bytes() == (repo / "master.yaml").read_bytes()


def test_fetch_from_selection_reuses_the_posting_and_requirements(old_run, repo, capsys):
    rc, last, _ = run(capsys, "fetch", "https://jobs.example.com/acme/1", "run1", "", "master.yaml", f"{OLD}/selection.json")
    assert (rc, last) == (0, 'carried {"app_dir": "applications/.tmp-run1"}')
    a = repo / A
    assert (a / "posting.txt").read_text() == (old_run / "posting.txt").read_text()
    assert json.loads((a / "requirements.json").read_text()) == REQS
    assert (a / "posting-url.txt").read_text() == "https://jobs.example.com/acme/1\n"
    assert (a / SNAPSHOT).read_bytes() == (repo / "master.yaml").read_bytes()  # the new master


def test_fetch_from_selection_refuses_another_posting(old_run, capsys):
    rc, last, _ = run(capsys, "fetch", "https://jobs.example.com/other/9", "run1", "", "master.yaml", f"{OLD}/selection.json")
    assert rc == 1 and codes(last) == {"carry_posting_mismatch"}


def test_fetch_from_selection_without_its_posting(old_run, capsys):
    (old_run / "posting.txt").unlink()
    rc, last, _ = run(capsys, "fetch", "p.txt", "run1", "", "master.yaml", f"{OLD}/selection.json")
    assert rc == 1 and codes(last) == {"carry_source_invalid"}


def test_fetch_from_selection_checks_the_requirements(old_run, capsys):
    (old_run / "requirements.json").write_text("{}")
    rc, last, _ = run(capsys, "fetch", "p.txt", "run1", "", "master.yaml", f"{OLD}/selection.json")
    assert rc == 1 and codes(last) == {"requirements_invalid"}


# --- name-folder routes a carried run to the carry step ------------------------------


@pytest.mark.parametrize("from_selection, token", [("", "draft"), ("x/selection.json", "carry")])
def test_name_folder_routes(repo, capsys, from_selection, token):
    (repo / FINAL).mkdir()
    (repo / FINAL / "requirements.json").write_text(json.dumps(REQS))
    rc, last, _ = run(capsys, "name-folder", FINAL, from_selection)
    assert rc == 0 and last.split(" ", 1)[0] == token
    assert json.loads(last.split(" ", 1)[1])["app_dir"] == FINAL


# --- carry ----------------------------------------------------------------------


def _carried_folder(repo) -> Path:
    a = repo / FINAL
    a.mkdir()
    (a / "requirements.json").write_text(json.dumps(REQS))
    return a


def _carry(capsys, *extra):
    rc, last, _ = run(capsys, "carry", FINAL, "master.yaml", f"{OLD}/selection.json", "2", "", "", "4", *extra)
    token, _, payload = last.partition(" ")
    return rc, token, json.loads(payload) if payload else None


def test_carry_writes_the_remapped_selection_and_its_report(old_run, repo, capsys):
    a = _carried_folder(repo)
    rc, token, payload = _carry(capsys)
    assert (rc, token) == (0, "ok")
    sel = json.loads((a / "selection.json").read_text())
    assert sel["master_hash"] == "sha256:" + sha(repo / "master.yaml")
    exp0 = sel["sections"][1]["entries"][0]
    assert exp0["highlights"] == ["experience.0.highlights.3", "experience.0.highlights.1", "experience.0.highlights.2"]
    report = json.loads((a / "carry.json").read_text())
    assert report["from"] == OLD
    assert report["new"] == [{"id": "experience.0.highlights.0", "text": NEW_BULLET.strip()[2:]}]
    assert payload["after_changes"] is True
    assert payload["carry_sha256"] == sha(a / "carry.json")
    assert payload["carry_note"].startswith(f"Carried from {OLD}:")
    assert payload["carry_note"].endswith("1 item new in the master (listed on the review page).")
    assert "change_request" not in payload


def test_carry_with_changes_goes_to_apply_changes(old_run, repo, capsys):
    _carried_folder(repo)
    rc, token, payload = _carry(capsys, "add experience.0.highlights.0")
    assert (rc, token) == (0, "fix")
    assert payload["change_request"] == "add experience.0.highlights.0"


def test_carry_that_does_not_build_asks_for_the_smallest_fix(old_run, repo, capsys):
    # The edit also pins a role the carried selection does not have: build says pin_missing.
    text = (repo / "master.yaml").read_text()
    (repo / "master.yaml").write_text(text.replace("        position: Junior Software Developer\n", "        position: Junior Software Developer\n        pin: yes\n"))
    _carried_folder(repo)
    rc, token, payload = _carry(capsys, "and drop swap 1")
    assert (rc, token) == (0, "fix")
    request = payload["change_request"]
    assert "pin_missing" in request and "experience.2" in request
    assert request.endswith("Then, as the user asked: and drop swap 1")


def test_carry_keeps_the_summary_with_its_sources_remapped(old_run, repo, capsys):
    summary = {"sentences": [{"text": "Built a feature store.", "sources": ["experience.0.highlights.2"]}]}
    (old_run / "summary.json").write_text(json.dumps(summary))
    write_guide(repo)
    a = _carried_folder(repo)
    assert _carry_with_guide(capsys)[1] == "ok"
    assert json.loads((a / "summary.json").read_text())["sentences"][0]["sources"] == ["experience.0.highlights.3"]


def _old_tailored(old_run, profile: list[str]) -> None:
    lines = "".join(f"      - {json.dumps(x)}\n" for x in profile)
    (old_run / "tailored.yaml").write_text(f"cv:\n  name: x\n  sections:\n    profile:\n{lines}")


def test_carry_keeps_a_summary_the_earlier_cv_showed(old_run, repo, capsys):
    summary = {"sentences": [{"text": "Built a feature store.", "sources": ["experience.0.highlights.2"]}]}
    (old_run / "summary.json").write_text(json.dumps(summary))
    _old_tailored(old_run, ["Built a feature store."])
    write_guide(repo)
    a = _carried_folder(repo)
    rc, token, payload = _carry_with_guide(capsys)
    assert (rc, token) == (0, "ok") and (a / "summary.json").exists()
    assert "summary_choice" not in payload


def test_carry_does_not_bring_back_a_summary_the_user_turned_down(old_run, repo, capsys):
    # The earlier run wrote a summary, but its CV showed the master's own (the user chose "keep the original").
    summary = {"sentences": [{"text": "Built a feature store.", "sources": ["experience.0.highlights.2"]}]}
    (old_run / "summary.json").write_text(json.dumps(summary))
    _old_tailored(old_run, ["Comfortable owning a platform end to end.", "Data engineer with eight years."])
    write_guide(repo)
    a = _carried_folder(repo)
    rc, token, payload = _carry_with_guide(capsys)
    assert (rc, token) == (0, "ok") and not (a / "summary.json").exists()
    assert payload["summary_choice"] == "original"
    assert json.loads((a / "carry.json").read_text())["summary"].startswith("not carried: the CV reviewed there showed")


def test_carry_without_a_guide_carries_no_summary(old_run, repo, capsys):
    (old_run / "summary.json").write_text(json.dumps({"sentences": []}))
    a = _carried_folder(repo)
    assert _carry(capsys)[1] == "ok"
    assert not (a / "summary.json").exists()


def _carry_with_guide(capsys):
    rc, last, _ = run(capsys, "carry", FINAL, "master.yaml", f"{OLD}/selection.json", "2", "summary-guide.md", "profile", "4")
    token, _, payload = last.partition(" ")
    return rc, token, json.loads(payload) if payload else None


def test_carry_without_the_old_master_is_exit_1(old_run, repo, capsys):
    (old_run / SNAPSHOT).unlink()  # and the test repo has no git history
    _carried_folder(repo)
    rc, last, _ = run(capsys, "carry", FINAL, "master.yaml", f"{OLD}/selection.json", "2", "", "", "4")
    assert rc == 1 and codes(last) == {"carry_master_not_found"}


# --- the recruiter's gate describes the CV under review -----------------------------


def test_check_review_records_the_text_it_graded(app, capsys):
    write_review(app, R1_OK, R2_OK, R3)
    rc, last, _ = run(capsys, "check-review", FINAL, "0", "0", "false")
    assert rc == 0 and last == f"pass reviewed_cv_sha256={steps.cv_text_sha(app / 'tailored.yaml', '')}"


def _drop_honors(app):
    sel = json.loads((app / "selection.json").read_text())
    sel["sections"] = [s for s in sel["sections"] if s["id"] != "honors"]
    (app / "selection.json").write_text(json.dumps(sel))


def test_build_changes_reruns_the_recruiter_when_the_cv_text_changed(app, capsys):
    write_review(app, R1_OK, R2_OK, R3)
    reviewed = steps.cv_text_sha(app / "tailored.yaml", "")
    _drop_honors(app)
    rc, last, _ = run(capsys, "build-changes", FINAL, "master.yaml", "2", "false", "", "", "", "4", reviewed)
    assert (rc, last) == (0, "rerun after_changes=true rerun_review=false")
    assert (app / "review.round1.json").exists()  # set aside: the recruiter writes a new one


def test_build_changes_rechecks_when_the_cv_text_is_the_same(app, capsys):
    reviewed = steps.cv_text_sha(app / "tailored.yaml", "")
    rc, last, _ = run(capsys, "build-changes", FINAL, "master.yaml", "2", "false", "", "", "", "4", reviewed)
    assert (rc, last) == (0, "recheck after_changes=true rerun_review=false")


def test_build_changes_reruns_when_nothing_was_reviewed_yet(app, capsys):
    # A carried run goes from the carry step to apply_changes before any review.
    rc, last, _ = run(capsys, "build-changes", FINAL, "master.yaml", "2", "false", "", "", "", "4", "")
    assert (rc, last) == (0, "rerun after_changes=true rerun_review=false")


def test_review_page_says_when_the_review_is_of_an_earlier_version(app, capsys):
    write_review(app, R1_BAD, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false")[0] == 0
    rc, last, _ = run(capsys, "review-page", FINAL, "master.yaml", "p.txt", "", "", "", "", "", "sha-of-another-cv")
    assert rc == 0
    assert json.loads(last)["summary"].startswith("Recruiter gate (from a review of an earlier version of this CV): fail")
    assert "graded an earlier version of this CV" in (app / "review.html").read_text()


def test_review_page_with_the_reviewed_text_has_no_such_note(app, capsys):
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false")[0] == 0
    reviewed = steps.cv_text_sha(app / "tailored.yaml", "")
    rc, last, _ = run(capsys, "review-page", FINAL, "master.yaml", "p.txt", "", "", "", "", "", reviewed)
    assert rc == 0 and json.loads(last)["summary"].startswith("Recruiter gate: pass")
    assert "earlier version" not in (app / "review.html").read_text()


def test_review_page_shows_the_carry_report(app, capsys):
    report = {
        "from": OLD,
        "old_master_hash": "sha256:old",
        "master_hash": "sha256:new",
        "kept": 12,
        "changed": [{"old_id": "experience.0.highlights.2", "id": "experience.0.highlights.2", "was": "four <ML>", "now": "five"}],
        "dropped": [{"old_id": "honors.0", "was": "Best Paper", "why": "gone"}],
        "swaps_dropped": [{"index": 2, "target_id": "experience.0.highlights.2", "from": "a", "to": "b", "why": "gone"}],
        "titles_reset": [],
        "new": [{"id": "experience.0.highlights.4", "text": "Ran on-call"}],
        "summary": "carried",
    }
    (app / "carry.json").write_text(json.dumps(report))
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false")[0] == 0
    args = ("review-page", FINAL, "master.yaml", "p.txt", "", "", "", "", "", "", sha(app / "carry.json"))
    assert run(capsys, *args)[0] == 0
    h = (app / "review.html").read_text()
    carried = h.split('<section class="carry">')[1].split("</section>")[0]
    assert OLD in carried and "four &lt;ML&gt;" in carried and "Best Paper" in carried
    assert "experience.0.highlights.4" in carried and "Ran on-call" in carried
    assert "add experience.0.highlights.4" in carried


def test_review_page_refuses_a_changed_carry_report(app, capsys):
    (app / "carry.json").write_text(json.dumps({"from": OLD}))
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false")[0] == 0
    rc, last, _ = run(capsys, "review-page", FINAL, "master.yaml", "p.txt", "", "", "", "", "", "", "0" * 64)
    assert rc == 1 and codes(last) == {"carry_report_changed"}


def test_old_check_review_tests_still_see_their_outcome(app, capsys):
    """The outcome token is first: pawl routes on it; the hash is a state write after it."""
    write_review(app, R1_BAD, R2_OK, R3)
    rc, last, _ = run(capsys, "check-review", FINAL, "0", "0", "false")
    assert last.split()[0:3] == ["redraft", "redrafts_done=1", "prev_supported=1"]
