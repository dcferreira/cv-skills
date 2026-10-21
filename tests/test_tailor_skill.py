"""Rules the cv-skills:tailor skill text must carry (its drivers follow it by hand)."""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _skill() -> str:
    return " ".join((REPO / "skills/tailor/SKILL.md").read_text(encoding="utf-8").split())


def test_driver_passes_descriptions_verbatim():
    text = _skill()
    assert "verbatim" in text and "add nothing" in text


def test_a_live_run_is_resumed_not_reinstalled_over():
    text = _skill()
    assert "run_live" in text and "pawl run tailor" in text


def test_a_posting_file_comes_with_its_web_address():
    text = _skill()
    assert "posting_url=" in text


def test_the_summary_guide_is_mentioned():
    text = _skill()
    assert "summary-guide.md" in text and "summary_guide=" in text
