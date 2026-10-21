from pathlib import Path

import pytest

from cv_skills.cli import Failure
from cv_skills.cvtext import cv_lines, cv_text, load_tailored, normalise, strip_markup

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "master.yaml"


def test_normalise_dashes_quotes_nbsp():
    assert normalise("a  b–c — d “q” ’s") == 'a b-c - d "q" \'s'


def test_normalise_ligature_nfkc():
    assert normalise("eﬃcient") == "efficient"


def test_normalise_strips_markup():
    assert normalise("**Go** [docs](http://x) `code` *it* __b__") == "Go docs code it b"


@pytest.mark.parametrize(
    "raw, stripped",
    [
        ("see [the docs](https://x.example/a_b) now", "see the docs now"),
        ("built in **Go** and **Rust**", "built in Go and Rust"),
        ("built in __Go__", "built in Go"),
        ("an *italic* word", "an italic word"),
        ("run `make test`", "run make test"),
        ("Plain text, nothing to strip.", "Plain text, nothing to strip."),
        ("2019 – 2022 “quoted”  twice", "2019 – 2022 “quoted”  twice"),  # no dash, quote or space changes
        ("eﬃcient snake_case_name", "eﬃcient snake_case_name"),  # no NFKC
    ],
)
def test_strip_markup(raw, stripped):
    assert strip_markup(raw) == stripped


def test_normalise_keeps_snake_case_underscores():
    assert normalise("snake_case_name") == "snake_case_name"


def test_cv_lines_one_line_per_string():
    doc = load_tailored(EXAMPLE)
    lines = cv_lines(doc)
    assert "Robin Kestrel" in lines
    assert any(l.startswith("Designed a streaming ingestion service in **Go**") for l in lines)
    # skills `details` are split into items, one line each
    assert "Python (pandas, polars)" in lines
    assert "Go" in lines
    # a plain-string entry and a bullet entry are lines too
    assert any(l.startswith("Data engineer with eight years") for l in lines)
    assert any(l.startswith("Best Paper Award") for l in lines)
    # dates are stringified
    assert "2022-03" in lines


def test_cv_lines_has_section_headings():
    lines = cv_lines(load_tailored(EXAMPLE))
    assert "Experience" in lines and "Skills" in lines


def test_cv_text_joins_lines_with_newlines():
    doc = load_tailored(EXAMPLE)
    assert cv_text(doc) == "\n".join(cv_lines(doc))


def test_cv_lines_skips_empty_details():
    doc = {"cv": {"name": "A", "sections": {"skills": [{"label": "L", "details": ""}]}}}
    assert cv_lines(doc) == ["A", "Skills", "L"]


def test_load_tailored_missing_file_is_tailored_invalid(tmp_path):
    with pytest.raises(Failure) as exc:
        load_tailored(tmp_path / "nope.yaml")
    assert exc.value.code == 2
    assert exc.value.errors[0]["code"] == "tailored_invalid"


def test_load_tailored_rejects_non_mapping(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("- a\n- b\n")
    with pytest.raises(Failure) as exc:
        load_tailored(p)
    assert exc.value.errors[0]["code"] == "tailored_invalid"


def test_load_tailored_rejects_bad_yaml(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("cv: [unclosed\n")
    with pytest.raises(Failure) as exc:
        load_tailored(p)
    assert exc.value.errors[0]["code"] == "tailored_invalid"


# --- review findings -----------------------------------------------------------


def test_section_headings_match_rendercv():
    doc = {"cv": {"name": "A", "sections": {"education_and_training": ["x"], "AI Projects": ["y"]}}}
    lines = cv_lines(doc)
    assert "Education and Training" in lines and "AI Projects" in lines


def test_newline_in_a_string_stays_one_line():
    doc = {"cv": {"name": "A", "sections": {"s": [{"label": "L", "highlights": ["Line one\nline two"]}]}}}
    assert "Line one line two" in cv_lines(doc)
    assert all("\n" not in l for l in cv_lines(doc))


@pytest.mark.parametrize(
    "yaml_text",
    [
        "cv:\n  name: A\n  sections: [1, 2]\n",
        "cv:\n  name: A\n  sections:\n    exp:\n",
        "cv:\n  name: A\n  sections:\n    1: [a]\n",
        "cv:\n  name: A\n  sections:\n    exp: hello\n",
        "cv:\n  name: A\n  sections:\n    exp: [[a]]\n",
        'cv:\n  name: "\\ud800"\n',
    ],
)
def test_load_tailored_rejects_malformed_sections(tmp_path, yaml_text):
    p = tmp_path / "t.yaml"
    p.write_text(yaml_text)
    with pytest.raises(Failure) as exc:
        load_tailored(p)
    assert exc.value.code == 2 and exc.value.errors[0]["code"] == "tailored_invalid"
