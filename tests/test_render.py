
import pytest

from cv_skills.cli import Failure
from cv_skills.render import page_margins, pdf_pages, render_pdf


def test_render_pdf_writes_only_in_workdir(tailored, tmp_path):
    before = set(tailored.parent.iterdir())
    work = tmp_path / "work"
    pdf = render_pdf(tailored, work)
    assert pdf.parent == work and pdf.suffix == ".pdf" and pdf.stat().st_size > 0
    assert set(tailored.parent.iterdir()) == before


def test_render_pdf_invalid_yaml_is_yaml_invalid(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("cv: [unclosed", encoding="utf-8")
    with pytest.raises(Failure) as exc:
        render_pdf(bad, tmp_path / "work")
    assert exc.value.code == 2
    assert exc.value.errors[0]["code"] == "yaml_invalid"


def test_render_pdf_missing_yaml_is_yaml_invalid(tmp_path):
    with pytest.raises(Failure) as exc:
        render_pdf(tmp_path / "nope.yaml", tmp_path / "work")
    assert exc.value.errors[0]["code"] == "yaml_invalid"


def test_pdf_pages_counts_pages_and_lines(tailored, tmp_path):
    pages = pdf_pages(render_pdf(tailored, tmp_path / "w"))
    assert len(pages) == 1
    assert (pages[0].width, pages[0].height) == (612.0, 792.0)
    assert any("Quillfeather" in line.text for line in pages[0].lines)


def test_pdf_pages_multi_page(make_cv, tmp_path):
    pages = pdf_pages(render_pdf(make_cv(60), tmp_path / "w"))
    assert len(pages) >= 2


def test_tool_missing_names_tool(tailored, tmp_path, monkeypatch):
    pdf = render_pdf(tailored, tmp_path / "w")
    monkeypatch.setenv("PATH", "")
    with pytest.raises(Failure) as exc:
        pdf_pages(pdf)
    err = exc.value.errors[0]
    assert (exc.value.code, err["code"], err["id"]) == (2, "tool_missing", "pdftotext")


def test_margins_in_points(tailored, tmp_path):
    assert page_margins(tailored) == (pytest.approx(50.4), pytest.approx(50.4))  # 0.7in


def test_yaml_invalid_message_speaks_of_the_yaml_not_the_master(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("cv:\n  name: A\n  email: not-an-email\n", encoding="utf-8")
    with pytest.raises(Failure) as exc:
        render_pdf(bad, tmp_path / "w")
    err = exc.value.errors[0]
    assert err["code"] == "yaml_invalid" and "Master" not in err["message"] and "master" not in err["message"]
    assert "cv.email" in err["message"]
