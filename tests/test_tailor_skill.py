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


def test_a_master_change_carries_the_run_instead_of_redrafting():
    text = _skill()
    assert "from_selection=" in text and "changes=" in text
    assert "input_changed" in text  # the blocked run that a master edit leaves


def test_readme_documents_the_carried_run():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "from_selection" in readme and "master.snapshot.yaml" in readme and "carry.json" in readme
