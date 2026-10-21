"""WP17: the summary written for each posting (guide-check, summary-check, build/review-page --summary,
cv-text/check-review --exclude-section). Fictional fixtures only: the example master, `profile` as the section."""

import json
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from cv_skills import cli
from cv_skills.master import load_master

REPO = Path(__file__).resolve().parent.parent
MASTER = REPO / "examples" / "master.yaml"
SELECTION = REPO / "examples" / "selection.json"
PROFILE = [
    "Data engineer with eight years of experience turning messy event streams into dependable, well-tested datasets.",
    "Comfortable owning a platform end to end – from ingestion and storage to the dashboards and models on top.",
]
MASTER_LEN = len(" ".join(PROFILE))
PAGE_CHECK = {"pages": 1, "limit": 2, "fits": True, "last_page_remaining_mm": 41.5, "last_page_fill_ratio": 0.78}
REQS = {
    "company": "Acme Analytics",
    "role": "Senior Data Engineer",
    "requirements": [
        {"id": "r1", "text": "Feature store", "kind": "must", "evidence": "cv"},
        {"id": "r2", "text": "Tidy pipelines", "kind": "nice", "evidence": "cv"},
    ],
}
GOOD = {
    "sentences": [
        {
            "text": "Data engineer at Quillfeather Labs who built a **feature store** used by four machine-learning teams.",
            "sources": ["experience.0", "experience.0.highlights.2"],
        },
        {"text": "Cut nightly batch runtime from 6 hours to 50 minutes with DuckDB.", "sources": ["experience.0.highlights.1"]},
    ]
}


def run(capsys, *argv):
    rc = cli.main([str(a) for a in argv])
    return rc, json.loads(capsys.readouterr().out)


def write_guide(path: Path, front: str = "section: profile\n", body: str = "Answer: why this team?\n") -> Path:
    path.write_text(f"---\n{front}---\n{body}", encoding="utf-8")
    return path


def write_json(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def guide_check(capsys, guide, master=MASTER):
    return run(capsys, "guide-check", "--guide", guide, "--master", master)


def summary_check(capsys, tmp_path, summary, *, front="section: profile\n", master=MASTER, selection=SELECTION):
    guide = write_guide(tmp_path / "summary-guide.md", front)
    js = write_json(tmp_path / "summary.json", summary) if not isinstance(summary, Path) else summary
    return run(
        capsys, "summary-check", "--master", master, "--selection", selection, "--guide", guide, "--summary", js
    )


def one(text: str, *sources: str) -> dict:
    return {"sentences": [{"text": text, "sources": list(sources)}]}


def codes(out: dict) -> list[tuple[str, str]]:
    return [(e["code"], e["id"]) for e in out["errors"]]


# --- guide-check ----------------------------------------------------------------


def test_guide_check_valid(tmp_path, capsys):
    g = write_guide(tmp_path / "g.md", "section: profile\nmax_chars: 200\nmax_sentences: 2\nfacts:\n  - 8 years of experience\n")
    rc, out = guide_check(capsys, g)
    assert rc == 0, out
    assert out == {"ok": True, "section": "profile", "max_chars": 200, "max_sentences": 2, "facts": 1}


def test_guide_check_defaults(tmp_path, capsys):
    rc, out = guide_check(capsys, write_guide(tmp_path / "g.md"))
    assert rc == 0, out
    assert out["max_chars"] == MASTER_LEN and out["max_sentences"] == 3 and out["facts"] == 0


def test_load_guide_returns_body_uninterpreted(tmp_path):
    from cv_skills.summary import load_guide

    g = load_guide(write_guide(tmp_path / "g.md", body="---\nsection: experience\n"), load_master(MASTER))
    assert g.section == "profile" and g.body == "---\nsection: experience\n" and g.facts == ()


@pytest.mark.parametrize(
    "front, id",
    [
        ("max_chars: 100\n", "guide.section"),  # missing
        ("section: awards\n", "guide.section"),  # not in the master
        ("section: experience\n", "guide.section"),  # not plain-text entries
        ("section: 3\n", "guide.section"),
        (f"section: profile\nmax_chars: {MASTER_LEN + 1}\n", "guide.max_chars"),  # R1
        ("section: profile\nmax_chars: 0\n", "guide.max_chars"),
        ("section: profile\nmax_chars: '200'\n", "guide.max_chars"),
        ("section: profile\nmax_chars: true\n", "guide.max_chars"),
        ("section: profile\nmax_sentences: 6\n", "guide.max_sentences"),  # R3
        ("section: profile\nmax_sentences: 0\n", "guide.max_sentences"),
        ("section: profile\nfacts: 8 years\n", "guide.facts"),
        ("section: profile\nfacts:\n  - ok\n  - ''\n", "guide.facts[1]"),
        ("section: profile\nfacts:\n  - ok\n  - 8\n", "guide.facts[1]"),
        ('section: profile\nfacts:\n  - "two\\nlines"\n', "guide.facts[0]"),
        ("section: profile\ntone: punchy\n", "guide.tone"),  # unknown key
    ],
)
def test_guide_check_rejects(tmp_path, capsys, front, id):
    rc, out = guide_check(capsys, write_guide(tmp_path / "g.md", front))
    assert rc == 2, out
    assert ("guide_invalid", id) in codes(out), out


def test_guide_check_max_chars_equal_to_master_length_is_allowed(tmp_path, capsys):
    rc, out = guide_check(capsys, write_guide(tmp_path / "g.md", f"section: profile\nmax_chars: {MASTER_LEN}\n"))
    assert rc == 0, out


@pytest.mark.parametrize("text", ["section: profile\n", "no front matter\n", "---\nsection: profile\n", "---\n- a\n---\n"])
def test_guide_check_needs_front_matter_mapping(tmp_path, capsys, text):
    g = tmp_path / "g.md"
    g.write_text(text, encoding="utf-8")
    rc, out = guide_check(capsys, g)
    assert rc == 2 and out["errors"][0]["code"] == "guide_invalid"


def test_guide_check_missing_file(tmp_path, capsys):
    rc, out = guide_check(capsys, tmp_path / "nope.md")
    assert rc == 2 and codes(out) == [("guide_invalid", str(tmp_path / "nope.md"))]


def test_guide_check_reports_every_problem(tmp_path, capsys):
    rc, out = guide_check(capsys, write_guide(tmp_path / "g.md", "section: profile\nmax_sentences: 9\nfacts: x\nx: 1\n"))
    assert rc == 2
    assert {i for _, i in codes(out)} == {"guide.max_sentences", "guide.facts", "guide.x"}


# --- summary-check ----------------------------------------------------------------


def test_summary_check_ok(tmp_path, capsys):
    rc, out = summary_check(capsys, tmp_path, GOOD)
    assert rc == 0, out
    assert out["ok"] is True and out["sentences"] == 2 and out["max_chars"] == MASTER_LEN
    assert out["chars"] == len(
        "Data engineer at Quillfeather Labs who built a feature store used by four machine-learning teams."
        " Cut nightly batch runtime from 6 hours to 50 minutes with DuckDB."
    )


def test_invented_number(tmp_path, capsys):
    s = one("Engineer with 12 years of experience.", "guide.facts.0")
    rc, out = summary_check(capsys, tmp_path, s, front="section: profile\nfacts:\n  - 8 years of experience\n")
    assert rc == 1 and codes(out) == [("summary_unsupported_token", "sentences[0]")]
    msg = out["errors"][0]["message"]
    assert "`12`" in msg and "guide.facts.0" in msg


def test_guide_fact_passes(tmp_path, capsys):
    s = one("Engineer with 8 years of experience.", "guide.facts.0")
    rc, out = summary_check(capsys, tmp_path, s, front="section: profile\nfacts:\n  - 8 years of experience\n")
    assert rc == 0, out


def test_invented_proper_noun(tmp_path, capsys):
    rc, out = summary_check(capsys, tmp_path, one("Built a feature store on Kubernetes.", "experience.0.highlights.2"))
    assert rc == 1 and codes(out) == [("summary_unsupported_token", "sentences[0]")]
    assert "`Kubernetes`" in out["errors"][0]["message"]


def test_swapped_text_is_what_supports(tmp_path, capsys):
    # The master says "four ML teams"; the selection swaps it to "four machine-learning teams".
    rc, out = summary_check(capsys, tmp_path, one("Feature store for four ML teams.", "experience.0.highlights.2"))
    assert rc == 1 and "`ML`" in out["errors"][0]["message"]


def test_unselected_source(tmp_path, capsys):
    rc, out = summary_check(capsys, tmp_path, one("Maintained REST APIs.", "experience.2.highlights.0"))
    assert rc == 1
    assert ("summary_source_not_selected", "sentences[0].sources[0]") in codes(out)


def test_unknown_source(tmp_path, capsys):
    rc, out = summary_check(capsys, tmp_path, one("Built things.", "experience.0.highlights.2", "experience.9"))
    assert rc == 1 and codes(out) == [("summary_source_unknown", "sentences[0].sources[1]")]


@pytest.mark.parametrize("src", ["guide.facts.3", "experience", "experience.0.title_variants.0", "nonsense"])
def test_unknown_source_kinds(tmp_path, capsys, src):
    rc, out = summary_check(capsys, tmp_path, one("Built things.", src))
    assert rc == 1 and ("summary_source_unknown", "sentences[0].sources[0]") in codes(out)


@pytest.mark.parametrize("src", ["profile.0", "profile"])
def test_source_in_own_section(tmp_path, capsys, src):
    rc, out = summary_check(capsys, tmp_path, one("Data engineer.", src))
    assert rc == 1
    assert ("summary_source_not_selected", "sentences[0].sources[0]") in codes(out)
    assert "own section" in out["errors"][0]["message"]


def test_source_missing(tmp_path, capsys):
    rc, out = summary_check(capsys, tmp_path, one("Data engineer.",))
    assert rc == 1 and codes(out) == [("summary_source_missing", "sentences[0].sources")]


def test_over_length(tmp_path, capsys):
    rc, out = summary_check(capsys, tmp_path, GOOD, front="section: profile\nmax_chars: 100\n")
    assert rc == 1 and codes(out) == [("summary_too_long", "sentences")]


def test_length_is_markdown_stripped(tmp_path, capsys):
    text = GOOD["sentences"][0]["text"]
    plain = len(text.replace("**", ""))
    s = {"sentences": [GOOD["sentences"][0]]}
    rc, out = summary_check(capsys, tmp_path, s, front=f"section: profile\nmax_chars: {plain}\n")
    assert rc == 0, out


def test_too_many_sentences(tmp_path, capsys):
    s = {"sentences": [{"text": "Data engineer.", "sources": ["experience.0"]}] * 4}
    rc, out = summary_check(capsys, tmp_path, s)
    assert rc == 1 and codes(out) == [("summary_too_many_sentences", "sentences")]
    rc, out = summary_check(capsys, tmp_path, s, front="section: profile\nmax_sentences: 4\n")
    assert rc == 0, out


def test_empty(tmp_path, capsys):
    rc, out = summary_check(capsys, tmp_path, {"sentences": []})
    assert rc == 1 and codes(out) == [("summary_empty", "sentences")]
    rc, out = summary_check(capsys, tmp_path, one("   ", "experience.0"))
    assert rc == 1 and codes(out) == [("summary_empty", "sentences[0]")]


@pytest.mark.parametrize(
    "text",
    [
        "Built a feature store.",  # "built" is in the source (case-insensitive)
        "Experienced builder of a feature store.",  # a listed opener
        "Data engineer who built a feature store.",  # a listed opener
        "The feature store, built for training.",
    ],
)
def test_ordinary_first_word_allowed(tmp_path, capsys, text):
    rc, out = summary_check(capsys, tmp_path, one(text, "experience.0.highlights.2"))
    assert rc == 0, out


@pytest.mark.parametrize(
    "text, tok",
    [
        ("Kubernetes specialist who built a feature store.", "Kubernetes"),  # not in the source, not an opener
        ("Mentoring aside, built a feature store.", "Mentoring"),
        ("DataOps engineer who built a feature store.", "DataOps"),  # an inner capital is never an opener
    ],
)
def test_invented_name_as_first_word(tmp_path, capsys, text, tok):
    rc, out = summary_check(capsys, tmp_path, one(text, "experience.0.highlights.2"))
    assert rc == 1 and codes(out) == [("summary_unsupported_token", "sentences[0]")]
    assert f"`{tok}`" in out["errors"][0]["message"]


@pytest.mark.parametrize("tok", ["eBay", "iOS", "gRPC", "macOS", "iPhone", "jQuery"])
def test_inner_capital_needs_support(tmp_path, capsys, tok):
    rc, out = summary_check(capsys, tmp_path, one(f"Built a feature store on {tok}.", "experience.0.highlights.2"))
    assert rc == 1 and codes(out) == [("summary_unsupported_token", "sentences[0]")]
    assert f"`{tok}`" in out["errors"][0]["message"]


@pytest.mark.parametrize("pronoun", ["I", "I'm", "I've", "I'd", "I'll", "I’m", "I’ll"])
def test_first_person_pronoun_mid_sentence_needs_no_support(tmp_path, capsys, pronoun):
    """A capitalised pronoun is grammar, not a name: it carries no claim."""
    s = one(f"Built a feature store, and {pronoun} like that work.", "experience.0.highlights.2")
    rc, out = summary_check(capsys, tmp_path, s)
    assert rc == 0, out


@pytest.mark.parametrize("tok", ["IBM", "Iceland", "Ivy", "IT"])
def test_capitalised_i_words_still_need_support(tmp_path, capsys, tok):
    rc, out = summary_check(capsys, tmp_path, one(f"Built a feature store, and {tok} uses it.", "experience.0.highlights.2"))
    assert rc == 1 and codes(out) == [("summary_unsupported_token", "sentences[0]")]


def test_inner_capital_supported_by_its_source(tmp_path, capsys, title_app):
    master, selection = title_app(replace=(("four ML teams", "four ML teams on gRPC"),))
    s = one("Built a feature store on gRPC.", "experience.0.highlights.2")
    rc, out = summary_check(capsys, tmp_path, s, master=master, selection=selection)
    assert rc == 0, out


@pytest.mark.parametrize("word", ["twelve", "Twelve", "dozens", "thrice", "seventh", "hundred", "half", "twice"])
def test_number_word_needs_support(tmp_path, capsys, word):
    rc, out = summary_check(capsys, tmp_path, one(f"Built a feature store for {word} teams.", "experience.0.highlights.2"))
    assert rc == 1 and codes(out) == [("summary_unsupported_token", "sentences[0]")]
    assert f"`{word}`" in out["errors"][0]["message"]


@pytest.mark.parametrize("text", ["Built a feature store for four teams.", "Built a feature store for FOUR teams.", "Four teams use the feature store we built."])
def test_number_word_in_the_source_passes(tmp_path, capsys, text):
    rc, out = summary_check(capsys, tmp_path, one(text, "experience.0.highlights.2"))
    assert rc == 0, out


def test_number_word_as_first_word_needs_support(tmp_path, capsys):
    rc, out = summary_check(capsys, tmp_path, one("Twelve teams use the feature store we built.", "experience.0.highlights.2"))
    assert rc == 1 and "`Twelve`" in out["errors"][0]["message"]


@pytest.mark.parametrize(
    "text",
    [
        "Built a feature store with #emph[style].",  # a Typst command
        "Built a feature store for four_teams.",
        "Built a _feature store_.",
        "Built a [feature store](https://example.com).",
        "Built a feature store [for] four teams.",
        "Built a `feature store`.",
        "Built a *feature store*.",  # italics
        "Built a **feature store* for four teams.",  # unbalanced
    ],
)
def test_markup_other_than_bold_is_rejected(tmp_path, capsys, text):
    rc, out = summary_check(capsys, tmp_path, one(text, "experience.0.highlights.2"))
    assert rc == 1 and ("summary_markup_not_allowed", "sentences[0]") in codes(out), out
    assert "**bold**" in next(e for e in out["errors"] if e["code"] == "summary_markup_not_allowed")["message"]


def test_bold_is_allowed(tmp_path, capsys):
    rc, out = summary_check(capsys, tmp_path, one("Built a **feature store** for **four** teams.", "experience.0.highlights.2"))
    assert rc == 0, out


def test_a_hash_before_a_digit_is_not_markup(tmp_path, capsys):
    rc, out = summary_check(capsys, tmp_path, one("Built a feature store for #4 teams.", "experience.0.highlights.2"))
    assert rc == 1 and codes(out) == [("summary_unsupported_token", "sentences[0]")]  # `4`, not markup


def test_capitalised_word_from_entry_fields(tmp_path, capsys):
    rc, out = summary_check(capsys, tmp_path, one("Data engineer in Lisbon at Quillfeather Labs since 2022.", "experience.0"))
    assert rc == 0, out


def test_derived_number_not_supported_by_entry_dates(tmp_path, capsys):
    # R2: "8 years" computed from the dates is not literally in any cited source.
    s = one("With 8 years at Quillfeather Labs.", "experience.0")
    rc, out = summary_check(capsys, tmp_path, s)
    assert rc == 1 and codes(out) == [("summary_unsupported_token", "sentences[0]")]
    assert "`8`" in out["errors"][0]["message"]
    s = one("With 8 years at Quillfeather Labs.", "experience.0", "guide.facts.0")
    rc, out = summary_check(capsys, tmp_path, s, front="section: profile\nfacts:\n  - 8 years in data engineering\n")
    assert rc == 0, out


def test_number_inside_a_longer_number_does_not_count(tmp_path, capsys):
    # "4" must be a whole token: "40k" and "2022" do not support it.
    rc, out = summary_check(capsys, tmp_path, one("Designed for 4 events.", "experience.0", "experience.0.highlights.0"))
    assert rc == 1 and "`4`" in out["errors"][0]["message"]


def test_hyphenated_word_and_markup_fold(tmp_path, capsys):
    s = one("Caught drift before it reached **Zürich** finance reports.", "experience.1.highlights.1")
    rc, out = summary_check(capsys, tmp_path, s)
    assert rc == 0, out


def test_chosen_title_passes_and_unchosen_default_does_not(tmp_path, capsys, title_app):
    master, selection = title_app(position="Senior ML Engineer")
    ok = one("A Senior ML Engineer.", "experience.0.position")
    rc, out = summary_check(capsys, tmp_path, ok, master=master, selection=selection)
    assert rc == 0, out
    rc, out = summary_check(capsys, tmp_path, one("A Senior ML Engineer.", "experience.0"), master=master, selection=selection)
    assert rc == 0, out
    bad = one("A Senior Data Engineer.", "experience.0.position", "experience.0")
    rc, out = summary_check(capsys, tmp_path, bad, master=master, selection=selection)
    assert rc == 1 and codes(out) == [("summary_unsupported_token", "sentences[0]")]
    assert "`Data`" in out["errors"][0]["message"]


def test_default_title_passes_when_not_changed(tmp_path, capsys, title_app):
    master, selection = title_app()
    rc, out = summary_check(capsys, tmp_path, one("A Senior Data Engineer.", "experience.0.position"), master=master, selection=selection)
    assert rc == 0, out


def test_position_of_unselected_entry_is_not_selected(tmp_path, capsys):
    rc, out = summary_check(capsys, tmp_path, one("Experienced developer.", "experience.2.position"))
    assert rc == 1 and codes(out) == [("summary_source_not_selected", "sentences[0].sources[0]")]


def test_wp14_markup(tmp_path, capsys):
    s = one("Designed a streaming service in **Go**; fast.", "experience.0.highlights.0")
    rc, out = summary_check(capsys, tmp_path, s)
    assert rc == 1 and codes(out) == [("markup_swallows_text", "sentences[0]")]


def test_wp14_markup_across_sentences(tmp_path, capsys):
    s = {
        "sentences": [
            {"text": "Designed a *streaming", "sources": ["experience.0.highlights.0"]},
            {"text": "service*(Go).", "sources": ["experience.0.highlights.0"]},
        ]
    }
    rc, out = summary_check(capsys, tmp_path, s)
    assert rc == 1 and ("markup_swallows_text", "sentences") in codes(out)


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"sentences": "x"}',
        '{"sentences": [], "extra": 1}',
        '{"sentences": [{"text": "a", "sources": ["x"], "why": "y"}]}',
        '{"sentences": [{"text": 3, "sources": []}]}',
        '{"sentences": [{"text": "a", "sources": [1]}]}',
        '{"sentences": [{"text": "a\\nb", "sources": ["x"]}]}',
        "[]",
    ],
)
def test_summary_invalid(tmp_path, capsys, raw):
    p = tmp_path / "summary.json"
    p.write_text(raw, encoding="utf-8")
    rc, out = summary_check(capsys, tmp_path, p)
    assert rc == 2 and all(e["code"] == "summary_invalid" for e in out["errors"]), out


def test_summary_check_bad_guide_is_exit_2(tmp_path, capsys):
    rc, out = summary_check(capsys, tmp_path, GOOD, front="section: experience\n")
    assert rc == 2 and codes(out) == [("guide_invalid", "guide.section")]


def test_check_summary_function(tmp_path):
    from cv_skills.build import read_selection
    from cv_skills.summary import Sentence, check_summary, load_guide

    m = load_master(MASTER)
    g = load_guide(write_guide(tmp_path / "g.md"), m)
    sel = read_selection(SELECTION)
    assert check_summary(m, sel, g, [Sentence("Built a feature store.", ("experience.0.highlights.2",))]) == []
    errs = check_summary(m, sel, g, [Sentence("Built Kubernetes.", ("experience.0.highlights.2",))])
    assert [e["code"] for e in errs] == ["summary_unsupported_token"]


# --- build --summary ----------------------------------------------------------------


def built(tmp_path, capsys, summary=GOOD, front="section: profile\n", selection=SELECTION, out_name="tailored.yaml"):
    guide = write_guide(tmp_path / "summary-guide.md", front)
    js = write_json(tmp_path / "summary.json", summary)
    out = tmp_path / out_name
    rc, res = run(
        capsys, "build", "--master", MASTER, "--selection", selection, "--out", out, "--summary", js, "--guide", guide
    )
    return rc, res, out


def test_build_with_summary_one_text_entry(tmp_path, capsys):
    rc, res, out = built(tmp_path, capsys)
    assert rc == 0, res
    doc = YAML(typ="safe").load(out.read_text(encoding="utf-8"))
    assert doc["cv"]["sections"]["profile"] == [" ".join(s["text"] for s in GOOD["sentences"])]
    assert list(doc["cv"]["sections"]) == ["profile", "experience", "skills", "education", "honors"]
    assert res["summary"] == {"section": "profile", "sentences": GOOD["sentences"]}


def test_build_without_summary_has_no_summary_key(tmp_path, capsys):
    rc, res = run(capsys, "build", "--master", MASTER, "--selection", SELECTION, "--out", tmp_path / "t.yaml")
    assert rc == 0 and "summary" not in res


@pytest.mark.parametrize("flag", ["--summary", "--guide"])
def test_build_summary_and_guide_go_together(tmp_path, capsys, flag):
    p = write_guide(tmp_path / "x")
    rc, res = run(capsys, "build", "--master", MASTER, "--selection", SELECTION, "--out", tmp_path / "t.yaml", flag, p)
    assert rc == 2 and res["errors"][0]["code"] == "usage"
    assert not (tmp_path / "t.yaml").exists()


def test_build_rejects_failing_summary_and_writes_nothing(tmp_path, capsys):
    rc, res, out = built(tmp_path, capsys, summary=one("Built it on Kubernetes.", "experience.0.highlights.2"))
    assert rc == 1 and codes(res) == [("summary_unsupported_token", "sentences[0]")]
    assert not out.exists()


def test_build_section_not_selected(tmp_path, capsys):
    sel = json.loads(SELECTION.read_text())
    sel["sections"] = [s for s in sel["sections"] if s["id"] != "profile"]
    p = write_json(tmp_path / "sel.json", sel)
    rc, res, out = built(tmp_path, capsys, selection=p)
    assert rc == 1 and codes(res) == [("summary_written_section_not_selected", "guide.section")]
    assert not out.exists()


def test_build_summary_out_is_input(tmp_path, capsys):
    guide = write_guide(tmp_path / "summary-guide.md")
    js = write_json(tmp_path / "summary.json", GOOD)
    rc, res = run(capsys, "build", "--master", MASTER, "--selection", SELECTION, "--out", js, "--summary", js, "--guide", guide)
    assert rc == 2 and res["errors"][0]["code"] == "out_is_input"


# --- recruiter isolation --------------------------------------------------------------

LOVES = one("Data engineer who loves tidy pipelines and a feature store.", "experience.0.highlights.2")


def test_cv_text_exclude_section(tmp_path, capsys):
    rc, _, tailored = built(tmp_path, capsys, summary=LOVES)
    assert rc == 0
    rc, out = run(capsys, "cv-text", "--tailored", tailored, "--out", tmp_path / "cv.txt", "--exclude-section", "profile")
    assert rc == 0, out
    text = (tmp_path / "cv.txt").read_text(encoding="utf-8")
    assert "Profile" not in text.splitlines() and "loves tidy pipelines" not in text
    assert "Experience" in text.splitlines()
    rc, _ = run(capsys, "cv-text", "--tailored", tailored, "--out", tmp_path / "all.txt")
    full = (tmp_path / "all.txt").read_text(encoding="utf-8")
    assert "loves tidy pipelines" in full and out["lines"] == len(full.splitlines()) - 2


def test_cv_lines_exclude(tmp_path):
    from cv_skills.cvtext import cv_lines

    doc = {"cv": {"name": "A", "sections": {"profile": ["x"], "skills": [{"label": "L", "details": "a, b"}]}}}
    assert cv_lines(doc, exclude_section="profile") == ["A", "Skills", "L", "a", "b"]
    assert cv_lines(doc, exclude_section="nope") == cv_lines(doc)


def check_review(capsys, app, tailored, quote1, quote2, *extra):
    write_json(app / "requirements.json", REQS)
    write_json(
        app / "review.json",
        {
            "requirements": [
                {"id": "r1", "status": "met", "evidence_quote": quote1, "reason": "x"},
                {"id": "r2", "status": "met", "evidence_quote": quote2, "reason": "x"},
            ],
            "top_fixes": [],
            "missed_requirements": [],
        },
    )
    return run(
        capsys,
        "check-review",
        "--review", app / "review.json",
        "--requirements", app / "requirements.json",
        "--tailored", tailored,
        "--out", app / "review.verified.json",
        *extra,
    )


def test_check_review_exclude_section_refuses_summary_only_quote(tmp_path, capsys):
    rc, _, tailored = built(tmp_path, capsys, summary=LOVES)
    assert rc == 0
    # Without the flag the summary counts (today's behaviour) ...
    rc, out = check_review(capsys, tmp_path, tailored, "feature store used by four", "loves tidy pipelines")
    assert rc == 0 and out["downgraded"] == []
    # ... with it, a quote found only in the summary is downgraded, one also in a highlight is accepted.
    rc, out = check_review(
        capsys, tmp_path, tailored, "a feature store", "loves tidy pipelines", "--exclude-section", "profile"
    )
    assert rc == 0 and out["downgraded"] == ["r2"]
    verified = json.loads((tmp_path / "review.verified.json").read_text())
    assert [r["status"] for r in verified["requirements"]] == ["met", "missing"]


# --- review-page --summary --------------------------------------------------------------


def review_app(tmp_path, capsys, summary=GOOD):
    rc, res, tailored = built(tmp_path, capsys, summary=summary)
    assert rc == 0, res
    rc, out = check_review(
        capsys, tmp_path, tailored, "feature store used by four", "", "--exclude-section", "profile"
    )
    assert rc == 0, out
    write_json(tmp_path / "page-check.json", PAGE_CHECK)
    return tailored


def page(capsys, app, *extra, tailored=None):
    return run(
        capsys,
        "review-page",
        "--master", MASTER,
        "--selection", SELECTION,
        "--tailored", tailored or app / "tailored.yaml",
        "--review", app / "review.verified.json",
        "--page-check", app / "page-check.json",
        "--out", app / "review.html",
        *extra,
    )


def with_summary(app):
    return ["--summary", app / "summary.json", "--guide", app / "summary-guide.md"]


def test_review_page_with_summary(tmp_path, capsys):
    review_app(tmp_path, capsys)
    rc, out = page(capsys, tmp_path, *with_summary(tmp_path))
    assert rc == 0, out
    h = (tmp_path / "review.html").read_text(encoding="utf-8")
    assert "written for this posting" in h
    assert '<span class="written" title="sources: experience.0, experience.0.highlights.2">' in h
    assert '<span class="written" title="sources: experience.0.highlights.1">' in h
    assert "<sup>[S1]</sup>" in h and "<sup>[S2]</sup>" in h
    assert "<strong>feature store</strong>" in h and "**" not in h
    # the master's summary is gone from the CV, kept only as the Changes row's original
    sources = h.split('id="summary-sources"')[1].split("</section>")[0]
    assert "experience.0.highlights.2" in sources and "Built a feature store used by four machine-learning teams" in sources
    assert "Quillfeather Labs" in sources and "experience.0.highlights.1" in sources
    changes = h.split('id="changes"')[1].split("</table>")[0]
    row = changes.split('<tr class="summary">')[1].split("</tr>")[0]
    assert "profile" in row and "summary" in row
    assert f"<del>{PROFILE[0]}" in row and "<ins>Data engineer at Quillfeather Labs" in row
    assert "Changes (4)" in h  # 3 swaps + the summary
    cv = h.split('<article class="cv">')[1].split("</article>")[0]
    assert "eight years of experience" not in cv


def test_review_page_summary_is_escaped(tmp_path, capsys):
    s = one('Built a feature store <for> four machine-learning teams & "more".', "experience.0.highlights.2")
    review_app(tmp_path, capsys, summary=s)
    rc, out = page(capsys, tmp_path, *with_summary(tmp_path))
    assert rc == 0, out
    h = (tmp_path / "review.html").read_text(encoding="utf-8")
    assert "&lt;for&gt;" in h and "<for>" not in h and "&amp;" in h


def test_review_page_tailored_mismatch_with_and_without_summary(tmp_path, capsys):
    review_app(tmp_path, capsys)
    # built with the summary, page without it: mismatch
    rc, out = page(capsys, tmp_path)
    assert rc == 1 and out["errors"][0]["code"] == "tailored_mismatch"
    # built without it, page with it: mismatch
    plain = tmp_path / "plain.yaml"
    assert run(capsys, "build", "--master", MASTER, "--selection", SELECTION, "--out", plain)[0] == 0
    rc, out = page(capsys, tmp_path, *with_summary(tmp_path), tailored=plain)
    assert rc == 1 and out["errors"][0]["code"] == "tailored_mismatch"


def test_review_page_summary_flags_go_together(tmp_path, capsys):
    review_app(tmp_path, capsys)
    rc, out = page(capsys, tmp_path, "--summary", tmp_path / "summary.json")
    assert rc == 2 and out["errors"][0]["code"] == "usage"


def test_review_page_failing_summary_propagates(tmp_path, capsys):
    review_app(tmp_path, capsys)
    write_json(tmp_path / "summary.json", one("Built it on Kubernetes.", "experience.0.highlights.2"))
    rc, out = page(capsys, tmp_path, *with_summary(tmp_path))
    assert rc == 1 and codes(out) == [("summary_unsupported_token", "sentences[0]")]
    assert not (tmp_path / "review.html").exists()


def test_final_render_checks_the_written_paragraph(tmp_path, capsys):
    from cv_skills.finalrender import expected_items

    rc, _, tailored = built(tmp_path, capsys)
    assert rc == 0
    assert ("profile.0", " ".join(s["text"] for s in GOOD["sentences"])) in expected_items(tailored)
    rc, out = run(capsys, "final-render", "--yaml", tailored, "--out-dir", tmp_path / "out")
    assert rc == 0, out


def test_example_guide_passes_guide_check(capsys):
    """The worked example (examples/summary-guide.md) is a valid guide for the example master."""
    rc = cli.main(["guide-check", "--guide", str(REPO / "examples" / "summary-guide.md"), "--master", str(MASTER)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out == {"ok": True, "section": "profile", "max_chars": MASTER_LEN, "max_sentences": 2, "facts": 1}
