"""The example CV's theme must extract cleanly (plan T5: no icon fonts, single column)."""

import subprocess

from cv_skills.finalrender import check_text, expected_items
from cv_skills.render import pdf_text, render_pdf


def test_example_pdf_has_no_icon_fonts_or_private_use_glyphs(tailored, tmp_path):
    pdf = render_pdf(tailored, tmp_path)
    fonts = subprocess.run(["pdffonts", str(pdf)], capture_output=True, text=True, check=True).stdout
    assert "FontAwesome" not in fonts
    text = pdf_text(pdf)
    assert [c for c in text if 0xE000 <= ord(c) <= 0xF8FF] == []  # icon glyphs land in the header's contact line
    assert check_text(expected_items(tailored), text) == []
