import json
from pathlib import Path

import pytest

from cv_skills import cli

REPO = Path(__file__).resolve().parent.parent
MASTER = REPO / "examples" / "master.yaml"
SELECTION = REPO / "examples" / "selection.json"

REQS = {
    "company": "Acme Analytics",
    "role": "Senior Data Engineer",
    "requirements": [
        {"id": "r1", "text": "Feature store", "kind": "must", "evidence": "cv"},
        {"id": "r2", "text": "Streaming in Go", "kind": "must", "evidence": "cv"},
        {"id": "r3", "text": "Mentoring", "kind": "nice", "evidence": "cv"},
    ],
}


def run(capsys, *argv):
    rc = cli.main([str(a) for a in argv])
    out = capsys.readouterr().out
    return rc, json.loads(out)


@pytest.fixture
def app(tmp_path, capsys):
    """An application folder: tailored.yaml built from the example, requirements.json."""
    tailored = tmp_path / "tailored.yaml"
    rc, _ = run(capsys, "build", "--master", MASTER, "--selection", SELECTION, "--out", tailored)
    assert rc == 0
    (tmp_path / "requirements.json").write_text(json.dumps(REQS))
    return tmp_path


def write_review(app, **over):
    review = {
        "requirements": [
            {"id": "r1", "status": "met", "evidence_quote": "feature store used by four machine-learning teams", "reason": "x"},
            {"id": "r2", "status": "partial", "evidence_quote": "streaming ingestion service in Go", "reason": "x"},
            {"id": "r3", "status": "missing", "evidence_quote": "", "reason": "x"},
        ],
        "top_fixes": ["Lead with the feature store"],
        "missed_requirements": ["Kubernetes"],
    }
    review.update(over)
    (app / "review.json").write_text(json.dumps(review))


def check(capsys, app, *extra):
    return run(
        capsys,
        "check-review",
        "--review", app / "review.json",
        "--requirements", app / "requirements.json",
        "--tailored", app / "tailored.yaml",
        "--out", app / "review.verified.json",
        *extra,
    )


def test_check_review_pass(app, capsys):
    write_review(app)
    rc, out = check(capsys, app)
    assert rc == 0
    assert out["ok"] is True and out["gate"] == "pass" and out["outcome"] == "pass"
    assert out["supported_must"] == 2 and out["total_must"] == 2 and out["downgraded"] == []
    verified = json.loads((app / "review.verified.json").read_text())
    assert [r["status"] for r in verified["requirements"]] == ["met", "partial", "missing"]
    assert verified["top_fixes"] == ["Lead with the feature store"]
    assert verified["company"] == "Acme Analytics" and verified["gate"] == "pass"
    assert out["for_you_to_judge"] == verified["for_you_to_judge"] == []


def test_check_review_lists_none_requirements_for_you_to_judge(app, capsys):
    reqs = json.loads(json.dumps(REQS))
    reqs["requirements"].append({"id": "r4", "text": "Fluent German", "kind": "must", "evidence": "none"})
    (app / "requirements.json").write_text(json.dumps(reqs))
    write_review(app)
    rc, out = check(capsys, app)
    assert rc == 0 and out["gate"] == "pass" and out["total_must"] == 2
    verified = json.loads((app / "review.verified.json").read_text())
    assert out["for_you_to_judge"] == verified["for_you_to_judge"] == [
        {"id": "r4", "kind": "must", "text": "Fluent German"}
    ]


def test_check_review_swapped_text_is_what_is_checked(app, capsys):
    # the example selection swaps "four ML teams" -> "four machine-learning teams";
    # a quote of the MASTER wording must not verify against the tailored CV.
    review = [
        {"id": "r1", "status": "met", "evidence_quote": "used by four ML teams", "reason": "x"},
        {"id": "r2", "status": "met", "evidence_quote": "streaming ingestion service in Go", "reason": "x"},
        {"id": "r3", "status": "missing", "evidence_quote": "", "reason": "x"},
    ]
    write_review(app, requirements=review)
    rc, out = check(capsys, app)
    assert rc == 0 and out["downgraded"] == ["r1"] and out["gate"] == "fail"


def test_check_review_downgrade_then_redraft(app, capsys):
    bad = [
        {"id": "r1", "status": "met", "evidence_quote": "invented sentence", "reason": "x"},
        {"id": "r2", "status": "met", "evidence_quote": "streaming ingestion service in Go", "reason": "x"},
        {"id": "r3", "status": "missing", "evidence_quote": "", "reason": "x"},
    ]
    write_review(app, requirements=bad)
    rc, out = check(capsys, app)
    assert rc == 0
    assert out["gate"] == "fail" and out["outcome"] == "redraft" and out["unmet_must"] == ["r1"]


def test_check_review_human_when_no_progress(app, capsys):
    bad = [
        {"id": "r1", "status": "missing", "evidence_quote": "", "reason": "x"},
        {"id": "r2", "status": "met", "evidence_quote": "streaming ingestion service in Go", "reason": "x"},
        {"id": "r3", "status": "missing", "evidence_quote": "", "reason": "x"},
    ]
    write_review(app, requirements=bad)
    rc, out = check(capsys, app, "--redrafts-done", "1", "--prev-supported", "1")
    assert rc == 0 and out["outcome"] == "human"


def test_check_review_bad_review_exit_1_lists_all_errors(app, capsys):
    write_review(app, requirements=[{"id": "r1", "status": "great", "evidence_quote": "", "reason": "x"}])
    rc, out = check(capsys, app)
    assert rc == 1 and out["ok"] is False
    assert {e["code"] for e in out["errors"]} == {"review_schema", "review_missing_requirement"}
    assert not (app / "review.verified.json").exists()


def test_check_review_tailored_missing_exit_2(app, capsys):
    write_review(app)
    (app / "tailored.yaml").unlink()
    rc, out = check(capsys, app)
    assert rc == 2 and out["errors"][0]["code"] == "tailored_invalid"


def test_check_review_out_is_input(app, capsys):
    write_review(app)
    rc, out = run(
        capsys,
        "check-review",
        "--review", app / "review.json",
        "--requirements", app / "requirements.json",
        "--tailored", app / "tailored.yaml",
        "--out", app / "review.json",
    )
    assert rc == 2 and out["errors"][0]["code"] == "out_is_input"
    assert json.loads((app / "review.json").read_text())["top_fixes"] == ["Lead with the feature store"]


def test_check_review_out_unwritable(app, capsys):
    write_review(app)
    (app / "outdir").mkdir()
    rc, out = run(
        capsys,
        "check-review",
        "--review", app / "review.json",
        "--requirements", app / "requirements.json",
        "--tailored", app / "tailored.yaml",
        "--out", app / "outdir",
    )
    assert rc == 2 and out["errors"][0]["code"] == "out_unwritable"


@pytest.mark.parametrize("flag,value", [("--redrafts-done", "-1"), ("--prev-supported", "x"), ("--redrafts-done", "1.5")])
def test_check_review_bad_counts_are_usage(app, flag, value):
    with pytest.raises(SystemExit) as exc:
        cli.main(["check-review", "--review", "r", "--requirements", "q", "--tailored", "t", "--out", "o", flag, value])
    assert exc.value.code == 2


def test_cv_text_writes_lines(app, capsys):
    rc, out = run(capsys, "cv-text", "--tailored", app / "tailored.yaml", "--out", app / "cv.txt")
    assert rc == 0 and out["ok"] is True and out["lines"] > 5
    text = (app / "cv.txt").read_text()
    assert "four machine-learning teams" in text and "Robin Kestrel" in text
    assert len(text.splitlines()) == out["lines"]


def test_cv_text_has_no_inline_markup(app, capsys):
    run(capsys, "cv-text", "--tailored", app / "tailored.yaml", "--out", app / "cv.txt")
    text = (app / "cv.txt").read_text()
    assert "**" not in text and "](" not in text
    assert any(l.startswith("Designed a streaming ingestion service in Go that") for l in text.splitlines())


def test_cv_text_out_is_input(app, capsys):
    rc, out = run(capsys, "cv-text", "--tailored", app / "tailored.yaml", "--out", app / "tailored.yaml")
    assert rc == 2 and out["errors"][0]["code"] == "out_is_input"


def test_cv_text_is_what_check_review_verifies_against(app, capsys):
    run(capsys, "cv-text", "--tailored", app / "tailored.yaml", "--out", app / "cv.txt")
    line = next(l for l in (app / "cv.txt").read_text().splitlines() if "feature store" in l)
    review = [
        {"id": "r1", "status": "met", "evidence_quote": line, "reason": "x"},
        {"id": "r2", "status": "missing", "evidence_quote": "", "reason": "x"},
        {"id": "r3", "status": "missing", "evidence_quote": "", "reason": "x"},
    ]
    write_review(app, requirements=review)
    rc, out = check(capsys, app)
    assert out["downgraded"] == []


def test_check_review_redrafts_done_requires_prev_supported(app, capsys):
    write_review(app)
    rc, out = check(capsys, app, "--redrafts-done", "1")
    assert rc == 2 and out["errors"][0]["code"] == "usage"
    assert "--prev-supported" in out["errors"][0]["message"]
    assert not (app / "review.verified.json").exists()


def test_check_review_binds_verified_review_to_tailored_bytes(app, capsys):
    import hashlib

    write_review(app)
    check(capsys, app)
    verified = json.loads((app / "review.verified.json").read_text())
    assert verified["tailored_sha256"] == hashlib.sha256((app / "tailored.yaml").read_bytes()).hexdigest()


def test_chosen_title_is_in_cv_text_and_a_quote_of_it_verifies(tmp_path, capsys, title_app):
    master, selection = title_app("Senior ML Engineer")
    assert run(capsys, "build", "--master", master, "--selection", selection, "--out", tmp_path / "tailored.yaml")[0] == 0
    (tmp_path / "requirements.json").write_text(json.dumps(REQS))
    run(capsys, "cv-text", "--tailored", tmp_path / "tailored.yaml", "--out", tmp_path / "cv.txt")
    text = (tmp_path / "cv.txt").read_text()
    assert "Senior ML Engineer" in text.splitlines()
    # Neither the default nor the unchosen variant reaches the recruiter.
    assert "Senior Data Engineer" not in text and "Senior Analytics Engineer" not in text
    review = [
        {"id": "r1", "status": "met", "evidence_quote": "Senior ML Engineer", "reason": "x"},
        {"id": "r2", "status": "met", "evidence_quote": "Senior Data Engineer", "reason": "x"},
        {"id": "r3", "status": "missing", "evidence_quote": "", "reason": "x"},
    ]
    write_review(tmp_path, requirements=review)
    rc, out = check(capsys, tmp_path)
    assert rc == 0 and out["downgraded"] == ["r2"]
