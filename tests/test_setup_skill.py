import json
import re
from pathlib import Path

import pytest

from cv_skills import cli
from cv_skills.cli import Failure, build_parser
from cv_skills.finalrender import final_render
from cv_skills.master import load_master
from cv_skills.sourcecheck import source_check

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills/setup/SKILL.md"
FIXTURES = REPO / "tests/fixtures/setup"


def _skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def _subcommands() -> set[str]:
    sub = next(a for a in build_parser()._actions if a.dest == "command")
    return set(sub.choices)


def test_frontmatter_name_and_description():
    m = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", _skill())
    assert m, "SKILL.md must open with name and one-line description frontmatter"
    assert m.group(1) == "setup"
    assert m.group(2).startswith("Use when")


def test_every_cv_skills_subcommand_mentioned_exists():
    mentioned = set(re.findall(r"`cv-skills ([a-z][a-z-]+)", _skill()))
    assert {"check-deps", "source-check", "ids", "final-render"} <= mentioned
    assert mentioned <= _subcommands()


def test_skill_carries_the_conversion_rules():
    text = _skill()
    for rule in [
        "never overwrite",  # an existing master.yaml
        "verbatim",
        "show_icons: false",
        "display_urls_instead_of_usernames: true",
        "pin: yes",
        "YYYY-MM",
        "check the dates",
        "guards the conversion only",
        "false reject",
        "never reword or delete true content",
    ]:
        assert rule in text, rule


def test_plugin_manifest_is_unchanged_by_skill_discovery():
    # skills/<name>/SKILL.md is discovered by convention (as in agent-pawl); plugin.json lists none
    manifest = json.loads((REPO / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert "skills" not in manifest and manifest["name"] == "cv-skills"


def test_expected_master_passes_all_gates(tmp_path, capsys):
    master = FIXTURES / "cv.expected.yaml"
    assert "display_urls_instead_of_usernames: true" in master.read_text(encoding="utf-8")  # the skill's default
    assert cli.main(["ids", "--master", str(master)]) == 0
    assert source_check(master, FIXTURES / "cv.tex")["ok"] is True
    assert final_render(master, tmp_path / "out")["ok"] is True


def test_legacy_yaml_is_rejected_with_actionable_codes():
    with pytest.raises(Failure) as exc:
        load_master(FIXTURES / "legacy.yaml")
    assert {e["code"] for e in exc.value.errors} == {"pin_not_yes", "tags_not_string"}


def test_legacy_yaml_fixed_form_passes_all_gates(tmp_path):
    fixed = tmp_path / "master.yaml"
    text = (FIXTURES / "legacy.yaml").read_text(encoding="utf-8")
    fixed.write_text(text.replace("pin: true", "pin: yes").replace("[sql, dashboards]", "sql, dashboards"), encoding="utf-8")
    assert load_master(fixed).nodes
    assert source_check(fixed, FIXTURES / "legacy.yaml")["ok"] is True
    assert final_render(fixed, tmp_path / "out")["ok"] is True
