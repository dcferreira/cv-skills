import json

import pytest

from cv_skills.cli import Failure
from cv_skills.review import decide, load_requirements, load_review, parse_review, verify


def test_quote_with_a_literal_asterisk_verifies():
    """A plain `*`, and the same `*` copied with the PDF's invisible U+FE0E, both verify."""
    from cv_skills.cvtext import normalise

    line = normalise("Cut costs 5 * 3 = 15 ways")
    assert normalise("5 * 3 = 15") in line
    assert normalise("5 *︎ 3 = 15") in line

LINES = [
    "Robin Kestrel",
    "Built a feature store used by four ML teams for training and online serving",
    "Designed a streaming ingestion service in **Go** that processes 40k events per second",
    "Mentored three junior engineers",
]

REQS = {
    "company": "Acme",
    "role": "Data Engineer",
    "requirements": [
        {"id": "r1", "text": "Feature store experience", "kind": "must", "evidence": "cv"},
        {"id": "r2", "text": "Streaming in Go", "kind": "must", "evidence": "cv"},
        {"id": "r3", "text": "Mentoring", "kind": "nice", "evidence": "cv"},
    ],
}


def reqs(tmp_path, data=REQS):
    p = tmp_path / "requirements.json"
    p.write_text(json.dumps(data))
    return load_requirements(p)


def review(**over):
    base = {
        "requirements": [
            {"id": "r1", "status": "met", "evidence_quote": "feature store used by four ML teams", "reason": "direct"},
            {"id": "r2", "status": "partial", "evidence_quote": "streaming ingestion service in Go", "reason": "go"},
            {"id": "r3", "status": "missing", "evidence_quote": "", "reason": "none"},
        ],
        "top_fixes": [],
        "missed_requirements": [],
    }
    base.update(over)
    return base


def parsed(tmp_path, **over):
    r = reqs(tmp_path)
    return r, parse_review(review(**over), r)


def codes(exc):
    return [(e["code"], e["id"]) for e in exc.value.errors]


# --- requirements.json -------------------------------------------------------


def test_load_requirements_ok(tmp_path):
    r = reqs(tmp_path)
    assert r.company == "Acme" and [x.id for x in r.items] == ["r1", "r2", "r3"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra=1),
        lambda d: d["requirements"][0].update(kind="should"),
        lambda d: d["requirements"].append(dict(d["requirements"][0])),
        lambda d: d["requirements"][0].pop("text"),
        lambda d: d.pop("company"),
    ],
)
def test_requirements_invalid_exit_2(tmp_path, mutate):
    d = json.loads(json.dumps(REQS))
    mutate(d)
    p = tmp_path / "r.json"
    p.write_text(json.dumps(d))
    with pytest.raises(Failure) as exc:
        load_requirements(p)
    assert exc.value.code == 2
    assert exc.value.errors[0]["code"] == "requirements_invalid"


def test_requirements_missing_file(tmp_path):
    with pytest.raises(Failure) as exc:
        load_requirements(tmp_path / "nope.json")
    assert exc.value.code == 2 and exc.value.errors[0]["code"] == "requirements_invalid"


def with_none(*ids):
    """REQS with the given ids marked evidence `none`."""
    d = json.loads(json.dumps(REQS))
    for r in d["requirements"]:
        if r["id"] in ids:
            r["evidence"] = "none"
    return d


def test_load_requirements_evidence_cv_and_none(tmp_path):
    r = reqs(tmp_path, with_none("r2"))
    assert [x.evidence for x in r.items] == ["cv", "none", "cv"]


def test_requirements_without_evidence_need_a_fresh_run(tmp_path):
    d = json.loads(json.dumps(REQS))
    for r in d["requirements"]:
        r.pop("evidence")
    p = tmp_path / "r.json"
    p.write_text(json.dumps(d))
    with pytest.raises(Failure) as exc:
        load_requirements(p)
    assert exc.value.code == 2
    assert codes(exc) == [("requirements_invalid", f"requirements[{i}].evidence") for i in range(3)]
    msg = exc.value.errors[0]["message"]
    assert "fresh run" in msg and '"cv"' in msg and '"none"' in msg and "language fluency" in msg


def test_requirements_bad_evidence_value(tmp_path):
    d = json.loads(json.dumps(REQS))
    d["requirements"][1]["evidence"] = "maybe"
    p = tmp_path / "r.json"
    p.write_text(json.dumps(d))
    with pytest.raises(Failure) as exc:
        load_requirements(p)
    assert exc.value.code == 2 and codes(exc) == [("requirements_invalid", "requirements[1].evidence")]
    assert "'maybe'" in exc.value.errors[0]["message"]


# --- review.json -------------------------------------------------------------


def test_review_file_not_json_is_review_invalid(tmp_path):
    p = tmp_path / "review.json"
    p.write_text("{nope")
    with pytest.raises(Failure) as exc:
        load_review(p, reqs(tmp_path))
    assert exc.value.code == 2 and exc.value.errors[0]["code"] == "review_invalid"


def test_review_schema_unknown_key_and_bad_status(tmp_path):
    r = reqs(tmp_path)
    bad = review(extra=1)
    bad["requirements"][0]["status"] = "great"
    with pytest.raises(Failure) as exc:
        parse_review(bad, r)
    assert exc.value.code == 1
    assert ("review_schema", "extra") in codes(exc)
    assert ("review_schema", "requirements[0].status") in codes(exc)


def test_review_schema_fix_asks_only_for_cv_requirements(tmp_path):
    with pytest.raises(Failure) as exc:
        parse_review(review(extra=1), reqs(tmp_path))
    assert 'evidence is "cv"' in exc.value.errors[0]["message"]


def test_duplicate_id_reported_even_when_its_evidence_is_bad(tmp_path):
    d = json.loads(json.dumps(REQS))
    d["requirements"][1]["evidence"] = "maybe"
    d["requirements"][2]["id"] = "r2"
    p = tmp_path / "r.json"
    p.write_text(json.dumps(d))
    with pytest.raises(Failure) as exc:
        load_requirements(p)
    assert ("requirements_invalid", "requirements[2].id") in codes(exc)


def test_review_schema_lone_surrogate_rejected(tmp_path):
    r = reqs(tmp_path)
    bad = review()
    bad["requirements"][0]["reason"] = "x\ud800"
    with pytest.raises(Failure) as exc:
        parse_review(bad, r)
    assert ("review_schema", "requirements[0].reason") in codes(exc)


def test_review_unknown_requirement(tmp_path):
    r = reqs(tmp_path)
    bad = review()
    bad["requirements"].append({"id": "r9", "status": "missing", "evidence_quote": "", "reason": "x"})
    with pytest.raises(Failure) as exc:
        parse_review(bad, r)
    assert exc.value.code == 1 and ("review_unknown_requirement", "r9") in codes(exc)


def test_review_missing_requirement(tmp_path):
    r = reqs(tmp_path)
    bad = review()
    bad["requirements"].pop()
    with pytest.raises(Failure) as exc:
        parse_review(bad, r)
    assert ("review_missing_requirement", "r3") in codes(exc)


def test_review_entry_for_a_none_requirement_is_rejected(tmp_path):
    r = reqs(tmp_path, with_none("r3"))
    with pytest.raises(Failure) as exc:
        parse_review(review(), r)
    assert exc.value.code == 1 and codes(exc) == [("review_unrated_requirement", "r3")]
    assert exc.value.errors[0]["message"] == (
        '`r3` is marked evidence "none" in requirements.json: leave it out; the user judges it.'
    )


def test_review_does_not_need_an_entry_for_a_none_requirement(tmp_path):
    r = reqs(tmp_path, with_none("r3"))
    rv = parse_review(review(requirements=review()["requirements"][:2]), r)
    assert [i.id for i in rv.items] == ["r1", "r2"]
    assert [x["id"] for x in verify(rv, r, LINES)] == ["r1", "r2"]


def test_review_duplicate_requirement(tmp_path):
    r = reqs(tmp_path)
    bad = review()
    bad["requirements"].append(dict(bad["requirements"][0]))
    with pytest.raises(Failure) as exc:
        parse_review(bad, r)
    assert ("review_duplicate_requirement", "r1") in codes(exc)


# --- quote verification ------------------------------------------------------


def test_quote_verifies_within_one_line(tmp_path):
    r, rv = parsed(tmp_path)
    v = verify(rv, r, LINES)
    assert [x["status"] for x in v] == ["met", "partial", "missing"]
    assert not any(x["downgraded"] for x in v)


def test_quote_with_markup_and_dashes_verifies(tmp_path):
    r = reqs(tmp_path)
    rv = parse_review(review(), r)
    rv.items[1].evidence_quote = "ingestion  service in **Go**"
    assert verify(rv, r, LINES)[1]["status"] == "partial"


def test_spliced_quote_across_lines_fails(tmp_path):
    r = reqs(tmp_path)
    rv = parse_review(review(), r)
    rv.items[0].evidence_quote = "serving Designed a streaming"
    v = verify(rv, r, LINES)
    assert v[0]["status"] == "missing" and v[0]["downgraded"] is True
    assert v[0]["claimed_status"] == "met"


def test_quote_case_sensitive(tmp_path):
    r = reqs(tmp_path)
    rv = parse_review(review(), r)
    rv.items[0].evidence_quote = "FEATURE STORE"
    assert verify(rv, r, LINES)[0]["downgraded"] is True


def test_blank_quote_for_met_is_unverified(tmp_path):
    r = reqs(tmp_path)
    rv = parse_review(review(), r)
    rv.items[0].evidence_quote = "  **  "
    v = verify(rv, r, LINES)
    assert v[0]["status"] == "missing" and v[0]["downgraded"] is True


def test_missing_with_empty_quote_ok(tmp_path):
    r, rv = parsed(tmp_path)
    v = verify(rv, r, LINES)
    assert v[2]["status"] == "missing" and v[2]["downgraded"] is False


def test_verified_carries_requirement_fields(tmp_path):
    r, rv = parsed(tmp_path)
    v = verify(rv, r, LINES)
    assert v[0]["id"] == "r1" and v[0]["kind"] == "must" and v[0]["text"] == "Feature store experience"
    assert set(v[0]) == {"id", "kind", "text", "status", "claimed_status", "downgraded", "evidence_quote", "reason"}


# --- gate --------------------------------------------------------------------


def v(*pairs):
    return [{"id": f"r{i}", "kind": k, "status": s} for i, (k, s) in enumerate(pairs)]


def test_gate_passes_when_all_must_met_or_partial():
    d = decide(v(("must", "met"), ("must", "partial")), 0, None)
    assert (d["gate"], d["outcome"]) == ("pass", "pass")
    assert d["supported_must"] == 2 and d["total_must"] == 2 and d["unmet_must"] == []


def test_gate_ignores_nice():
    assert decide(v(("must", "met"), ("nice", "missing")), 0, None)["gate"] == "pass"


def test_gate_with_no_must_passes():
    d = decide(v(("nice", "missing")), 0, None)
    assert d["gate"] == "pass" and d["total_must"] == 0


def test_decide_first_round_redrafts():
    d = decide(v(("must", "met"), ("must", "missing")), 0, None)
    assert (d["gate"], d["outcome"], d["unmet_must"]) == ("fail", "redraft", ["r1"])


def test_decide_redrafts_when_supported_went_up():
    assert decide(v(("must", "met"), ("must", "missing")), 1, 0)["outcome"] == "redraft"


def test_decide_stops_when_supported_did_not_go_up():
    assert decide(v(("must", "met"), ("must", "missing")), 1, 1)["outcome"] == "human"
    assert decide(v(("must", "met"), ("must", "missing")), 1, 2)["outcome"] == "human"


def test_decide_caps_at_two_redrafts():
    assert decide(v(("must", "missing")), 2, 0)["outcome"] == "human"


def test_decide_pass_wins_over_cap():
    assert decide(v(("must", "met")), 2, 5)["outcome"] == "pass"


def _check(tmp_path, data, rows):
    """check_review on LINES as a tailored CV; returns (result, verified document)."""
    from cv_skills.review import check_review

    p = tmp_path / "requirements.json"
    p.write_text(json.dumps(data))
    (tmp_path / "review.json").write_text(json.dumps(review(requirements=rows)))
    tailored = tmp_path / "tailored.yaml"
    tailored.write_text("cv:\n  name: Robin Kestrel\n  sections:\n    experience:\n" + "".join(f"      - {line!r}\n" for line in LINES[1:]))
    out = tmp_path / "review.verified.json"
    result = check_review(tmp_path / "review.json", p, tailored, out)
    return result, json.loads(out.read_text())


def test_gate_leaves_out_a_none_must(tmp_path):
    d = json.loads(json.dumps(REQS))
    d["requirements"].insert(1, {"id": "r9", "text": "Fluent German", "kind": "must", "evidence": "none"})
    result, doc = _check(tmp_path, d, review()["requirements"])
    assert (result["gate"], result["outcome"]) == ("pass", "pass")
    assert result["supported_must"] == 2 and result["total_must"] == 2 and result["unmet_must"] == []
    assert [x["id"] for x in doc["requirements"]] == ["r1", "r2", "r3"]
    assert doc["for_you_to_judge"] == [{"id": "r9", "kind": "must", "text": "Fluent German"}]
    assert result["for_you_to_judge"] == doc["for_you_to_judge"]


def test_no_cv_musts_and_empty_review_passes(tmp_path):
    result, doc = _check(tmp_path, with_none("r1", "r2", "r3"), [])
    assert (result["gate"], result["total_must"]) == ("pass", 0)
    assert doc["requirements"] == [] and [x["id"] for x in doc["for_you_to_judge"]] == ["r1", "r2", "r3"]


def test_for_you_to_judge_empty_when_all_cv(tmp_path):
    _, doc = _check(tmp_path, REQS, review()["requirements"])
    assert doc["for_you_to_judge"] == []


# --- review findings -----------------------------------------------------------


def _quote_verifies(tmp_path, line, quote):
    r = reqs(tmp_path)
    rv = parse_review(review(), r)
    rv.items[0].evidence_quote = quote
    return not verify(rv, r, [line])[0]["downgraded"]


def test_quote_copied_from_raw_line_verifies_even_with_half_markup(tmp_path):
    assert _quote_verifies(tmp_path, "Designed a service in **Go** that scales", "service in **Go")
    assert _quote_verifies(tmp_path, "Grew revenue 2*3*4 times", "2*3")


def test_markup_only_quote_still_unverified(tmp_path):
    assert not _quote_verifies(tmp_path, "Designed a service in **Go** that scales", "**")
