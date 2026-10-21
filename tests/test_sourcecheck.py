import json
from pathlib import Path

import pytest

from cv_skills import cli
from cv_skills.cli import Failure
from cv_skills.render import render_pdf
from cv_skills.sourcecheck import latex_to_text, source_check

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests/fixtures/setup"
TEX = FIXTURES / "cv.tex"
EXPECTED = FIXTURES / "cv.expected.yaml"
EXAMPLE = REPO / "examples/master.yaml"


def _master(tmp_path: Path, old: str | None = None, new: str = "") -> Path:
    text = EXPECTED.read_text(encoding="utf-8")
    if old is not None:
        assert old in text
        text = text.replace(old, new)
    path = tmp_path / "master.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _errors(master: Path, source: Path = TEX) -> list[dict]:
    with pytest.raises(Failure) as exc:
        source_check(master, source)
    return exc.value.errors


def test_faithful_latex_conversion_passes(tmp_path):
    result = source_check(_master(tmp_path), TEX)
    assert result["ok"] is True
    assert result["source"] == "tex"
    # 1 profile + 5 highlights + 2 experience entries x 2 fields + 3 education fields + 2 labels + 6 skills items
    assert result["checked_items"] == 1 + 5 + 4 + 3 + 2 + 6


def test_latex_macros_escapes_and_comments_do_not_cause_false_alarms():
    text = latex_to_text(TEX.read_text(encoding="utf-8"))
    text = " ".join(text.split())
    for expected in ["60% by adding SLO", "R&D staging", "900 ms to 120 ms", "Zürich billing"]:
        assert expected in text
    assert "ten engineers" not in text


def test_invented_bullet_reported_with_id(tmp_path):
    master = _master(tmp_path, "          - Built an invoicing", "          - Founded a payments unicorn\n          - Built an invoicing")
    [error] = _errors(master)
    assert (error["code"], error["id"]) == ("text_not_in_source", "experience.1.highlights.0")
    assert "Founded a payments unicorn" in error["message"]


def test_reworded_bullet_reported(tmp_path):
    master = _master(tmp_path, "Reduced p99 latency", "Slashed p99 latency")
    [error] = _errors(master)
    assert (error["code"], error["id"]) == ("text_not_in_source", "experience.0.highlights.0")


def test_wrong_employer_and_title_reported(tmp_path):
    master = _master(tmp_path, "company: Harborlight Payments", "company: Harbourlight Payments")
    master = _master_replace(master, "position: Software Engineer", "position: Staff Engineer")
    ids = sorted(e["id"] for e in _errors(master))
    assert ids == ["experience.0.company", "experience.1.position"]


def _master_replace(path: Path, old: str, new: str) -> Path:
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    return path


def test_dates_and_contact_fields_are_not_checked(tmp_path):
    master = _master(tmp_path, "start_date: 2021-02", "start_date: 1999-01")
    _master_replace(master, "email: mara.linden@example.com", "email: other@example.com")
    _master_replace(master, "location: Delft", "location: Rotterdam")
    assert source_check(master, TEX)["ok"] is True


def test_all_unmatched_reported_at_once(tmp_path):
    master = _master(tmp_path, "Python, Go, SQL", "Python, Go, SQL, Haskell, Erlang")
    assert [e["id"] for e in _errors(master)] == ["skills.0.details.3", "skills.0.details.4"]


def test_short_item_is_not_found_inside_a_longer_word(tmp_path):
    source = tmp_path / "cv.txt"
    source.write_text("Languages: Google, Python\n", encoding="utf-8")
    master = tmp_path / "master.yaml"
    master.write_text(
        "cv:\n  name: A B\n  sections:\n    skills:\n      - label: Languages\n        details: Go, Python\n", encoding="utf-8"
    )
    [error] = _errors(master, source)
    assert error["id"] == "skills.0.details.0"


def test_commented_out_latex_text_is_not_source(tmp_path):
    master = _master(tmp_path, "          - Built an invoicing", "          - Managed a team of ten engineers\n          - Built an invoicing")
    [error] = _errors(master)
    assert error["id"] == "experience.1.highlights.0"


def test_pdf_round_trip_with_wrapping_and_hyphenation(tmp_path):
    pdf = render_pdf(EXAMPLE, tmp_path / "out")
    result = source_check(EXAMPLE, pdf)
    assert result["source"] == "pdf"
    assert result["checked_items"] > 10


def test_yaml_source(tmp_path):
    assert source_check(_master(tmp_path), EXPECTED)["source"] == "yaml"


def test_source_missing_exit_2(tmp_path):
    [error] = _errors(_master(tmp_path), tmp_path / "nope.tex")
    assert (error["code"], error["id"]) == ("source_unreadable", str(tmp_path / "nope.tex"))


def test_source_not_utf8_exit_2(tmp_path):
    bad = tmp_path / "cv.txt"
    bad.write_bytes(b"\xff\xfe\x00bad")
    assert _errors(_master(tmp_path), bad)[0]["code"] == "source_unreadable"


def test_master_invalid_exit_2(tmp_path):
    bad = tmp_path / "master.yaml"
    bad.write_text("cv: [1, 2]\n", encoding="utf-8")
    assert _errors(bad)[0]["code"] == "master_invalid"


def test_cli_exit_codes(tmp_path, capsys):
    master = _master(tmp_path)
    assert cli.main(["source-check", "--master", str(master), "--source", str(TEX)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    bad = _master(tmp_path, "Reduced", "Shrank")
    assert cli.main(["source-check", "--master", str(bad), "--source", str(TEX)]) == 1
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "text_not_in_source"
    assert cli.main(["source-check", "--master", str(master), "--source", str(tmp_path / "nope")]) == 2


def _tiny(tmp_path: Path, section: str, entries: str, source_text: str, suffix: str = ".txt") -> tuple[Path, Path]:
    """A one-section master (`entries` is YAML for the list items) and a source file with `source_text`."""
    master = tmp_path / "tiny.yaml"
    master.write_text(f"cv:\n  name: A B\n  sections:\n    {section}:\n{entries}\n", encoding="utf-8")
    source = tmp_path / f"tiny{suffix}"
    source.write_text(source_text, encoding="utf-8")
    return master, source


def _profile(tmp_path: Path, text: str, source_text: str, suffix: str = ".txt") -> tuple[Path, Path]:
    return _tiny(tmp_path, "profile", f"      - {json.dumps(text, ensure_ascii=False)}", source_text, suffix)


def test_promoted_title_reported(tmp_path):
    master = _master(tmp_path, "position: Software Engineer", "position: Senior Backend Engineer")
    [error] = _errors(master)
    assert (error["code"], error["id"]) == ("text_not_in_source", "experience.1")
    assert "Brightmoor Software" in error["message"] and "Senior Backend Engineer" in error["message"]


def test_swapped_employer_reported(tmp_path):
    master = _master(tmp_path, "company: Brightmoor Software", "company: Harborlight Payments")
    assert [e["id"] for e in _errors(master)] == ["experience.1"]


def test_education_fields_must_occur_together(tmp_path):
    master = _master(tmp_path, "area: Computer Science", "area: Senior Backend Engineer")
    assert [e["id"] for e in _errors(master)] == ["education.0"]


def test_short_skill_is_not_found_inside_another_token(tmp_path):
    master, source = _tiny(
        tmp_path, "skills", "      - label: Languages\n        details: R, Go", "Languages: Go\nLed R&D tooling\n"
    )
    assert [e["id"] for e in _errors(master, source)] == ["skills.0.details.0"]


@pytest.mark.parametrize("item", ["R", "C++", "C#", "Go"])
def test_short_skill_is_found_in_a_skills_list(tmp_path, item):
    master, source = _tiny(
        tmp_path, "skills", f"      - label: Languages\n        details: {item}", "Languages: Python, R, C++, C#, Go, SQL\n"
    )
    assert source_check(master, source)["ok"] is True


def test_text_after_escaped_backslash_percent_is_a_comment():
    assert "hidden" not in latex_to_text("visible \\\\% hidden\nnext")
    assert "50%" in latex_to_text("grew by 50\\% in a year")


def test_iffalse_and_comment_environment_are_not_source():
    text = latex_to_text("a \\iffalse Led a team of 40 \\fi b \\begin{comment}Founded Acme\\end{comment} c")
    assert "Led a team" not in text and "Founded Acme" not in text
    assert all(w in text for w in "abc")


def test_title_journal_label_and_number_fields_are_checked(tmp_path):
    entries = "      - title: An invented paper\n        authors: [Mara Linden]\n        journal: Nature\n        date: 2020-01"
    master, source = _tiny(tmp_path, "publications", entries, "Selected work by Mara Linden\n")
    assert [e["id"] for e in _errors(master, source)] == ["publications.0.title", "publications.0.journal"]
    master, source = _tiny(tmp_path, "skills", "      - label: Leadership\n        details: Go", "Languages: Go\n")
    assert [e["id"] for e in _errors(master, source)] == ["skills.0.label"]


def test_authors_list_is_checked(tmp_path):
    entries = "      - title: Real paper\n        authors: [Mara Linden, Invented Person]\n        date: 2020-01"
    master, source = _tiny(tmp_path, "publications", entries, "Real paper by Mara Linden\n")
    assert [e["id"] for e in _errors(master, source)] == ["publications.0.authors.1"]


def test_hyphen_between_digits_counts(tmp_path):
    master, source = _profile(tmp_path, "Grew from 23 to 5 engineers", "Grew from 2-3 to 5 engineers")
    assert [e["id"] for e in _errors(master, source)] == ["profile.0"]
    master, source = _profile(tmp_path, "Ran 2-3 pilots", "Ran 2–3 pilots")
    assert source_check(master, source)["ok"] is True


@pytest.mark.parametrize(
    "tex, text",
    [
        (r"Worked with Bj\o rn on Stra\ss e maps", "Worked with Bjørn on Straße maps"),
        (r"Mentor to Mar\'{\i}a and Erd\H{o}s", "Mentor to María and Erdős"),
        (r"Reached $\sim$30\% coverage, 3$\times$ faster\ldots", "Reached ~30% coverage, 3× faster…"),
    ],
)
def test_common_latex_symbols_are_resolved(tmp_path, tex, text):
    master, source = _profile(tmp_path, text, tex, ".tex")
    assert source_check(master, source)["ok"] is True


HREF_TEX = r"Built the pipeline described in \href{https://example.com/docs}{the design docs} for the team"


def test_href_url_is_not_source_text(tmp_path):
    master, source = _profile(
        tmp_path, "Built the pipeline described in [the design docs](https://example.com/docs) for the team", HREF_TEX, ".tex"
    )
    assert source_check(master, source)["ok"] is True


def test_href_still_rejects_changed_text_and_the_url_as_text(tmp_path):
    master, source = _profile(
        tmp_path, "Built the pipeline described in [the design docs](https://example.com/docs) for the board", HREF_TEX, ".tex"
    )
    assert [e["id"] for e in _errors(master, source)] == ["profile.0"]
    master, source = _profile(tmp_path, "Built the pipeline described in https://example.com/docs for the team", HREF_TEX, ".tex")
    assert [e["id"] for e in _errors(master, source)] == ["profile.0"]


def test_href_with_escapes_and_nested_commands(tmp_path):
    tex = r"Read \href{https://example.com/a\_b\%20c?x=1\#top}{\textbf{the} guide} first"
    master, source = _profile(tmp_path, "Read [**the** guide](https://example.com/a_b%20c?x=1#top) first", tex, ".tex")
    assert source_check(master, source)["ok"] is True


@pytest.mark.parametrize(
    "tex",
    [
        r"A \href{https://x.example/a%20b}{the docs} for team",  # raw % is not a comment inside the URL
        r"A \href{https://x.example/\~u}{the docs} for team",  # accent-like escape in the URL
        r"A \href{http://x.example/\~{}u}{the docs} for team",  # empty brace pair in the URL
        r"A \href[pdfnewwindow]{https://x.example}{the docs} for team",  # optional argument
        "A \\href{https://x.example}\n  {the docs} for team",  # newline between the arguments
        r"A \hyperref[sec:a]{the docs} for team",  # optional argument of \hyperref
    ],
)
def test_link_urls_and_options_never_reach_the_text(tex):
    text = " ".join(latex_to_text(tex).split())
    assert "A the docs for team" in text, text


def test_unterminated_href_in_a_comment_does_not_eat_the_next_line():
    text = " ".join(latex_to_text("% see \\href{http://x.example\nKept line {with braces} here").split())
    assert "Kept line" in text and "here" in text


def test_url_command_shows_its_argument(tmp_path):
    master, source = _profile(tmp_path, "Docs at https://example.com/docs today", r"Docs at \url{https://example.com/docs} today", ".tex")
    assert source_check(master, source)["ok"] is True


def test_source_that_is_not_a_pdf_is_unreadable(tmp_path):
    master = _master(tmp_path)
    fake = tmp_path / "cv.pdf"
    fake.write_text("not a pdf", encoding="utf-8")
    [error] = _errors(master, fake)
    assert (error["code"], error["id"]) == ("source_unreadable", str(fake))


def test_directory_source_is_not_reported_as_missing(tmp_path):
    [error] = _errors(_master(tmp_path), tmp_path)
    assert error["code"] == "source_unreadable" and "is a directory" in error["message"]


def test_title_variants_are_not_source_checked(tmp_path):
    # The variants are the user's own later wording, not converted text: they are ignored.
    position = "        position: Senior Backend Engineer\n"
    master = _master(tmp_path, position, position + "        title_variants: Staff Platform Engineer; Payments Lead\n")
    result = source_check(master, TEX)
    assert result["ok"] is True
    assert result["checked_items"] == 1 + 5 + 4 + 3 + 2 + 6
