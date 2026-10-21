import json
from pathlib import Path

import pytest

from cv_skills import cli
from cv_skills.cli import Failure
from cv_skills.deps import check_deps


def _which(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def _call(tmp_path, which, rendercv="2.8.1", pawl_version=lambda p: "pawl 0.9.0"):
    return check_deps(which=which, home=tmp_path, rendercv_version=lambda: rendercv, pawl_version=pawl_version)


def _plugin_pawl(home: Path, version: str, mode: int = 0o755) -> Path:
    path = home / ".claude/plugins/cache/agent-pawl/agent-pawl" / version / "bin/pawl"
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(mode)
    return path


def test_all_present_ok(tmp_path):
    result = _call(tmp_path, _which("pdftotext", "pawl"))
    assert result["ok"] is True
    assert [t["name"] for t in result["tools"]] == ["rendercv", "pdftotext", "pawl"]
    assert all(t["found"] for t in result["tools"])
    assert "2.8.1" in result["tools"][0]["detail"]
    assert "/usr/bin/pawl" in result["tools"][2]["detail"]


def test_missing_pdftotext_reports_install_hint(tmp_path):
    with pytest.raises(Failure) as exc:
        _call(tmp_path, _which("pawl"))
    assert exc.value.code == 2
    [error] = exc.value.errors
    assert (error["code"], error["id"]) == ("tool_missing", "pdftotext")
    assert "poppler" in error["message"]


def test_all_missing_reports_all_three_at_once(tmp_path):
    with pytest.raises(Failure) as exc:
        _call(tmp_path, _which(), rendercv=None)
    assert [e["id"] for e in exc.value.errors] == ["rendercv", "pdftotext", "pawl"]
    assert {e["code"] for e in exc.value.errors} == {"tool_missing"}
    assert "agent-pawl" in exc.value.errors[2]["message"]


def test_pawl_found_in_plugin_cache(tmp_path):
    path = _plugin_pawl(tmp_path, "0.9.0")
    result = _call(tmp_path, _which("pdftotext"))
    assert str(path) in result["tools"][2]["detail"]


def test_pawl_path_wins_over_plugin_cache(tmp_path):
    _plugin_pawl(tmp_path, "0.9.0")
    result = _call(tmp_path, _which("pdftotext", "pawl"))
    assert "/usr/bin/pawl" in result["tools"][2]["detail"]


def test_highest_plugin_cache_version_is_picked(tmp_path):
    _plugin_pawl(tmp_path, "0.9.0")
    newest = _plugin_pawl(tmp_path, "0.10.1")
    _plugin_pawl(tmp_path, "0.2.0")
    result = _call(tmp_path, _which("pdftotext"))
    assert str(newest) in result["tools"][2]["detail"]


def test_non_executable_cached_pawl_is_ignored(tmp_path):
    good = _plugin_pawl(tmp_path, "0.9.0")
    _plugin_pawl(tmp_path, "0.10.0", mode=0o644)
    result = _call(tmp_path, _which("pdftotext"))
    assert str(good) in result["tools"][2]["detail"]


def test_only_non_executable_cached_pawl_is_missing(tmp_path):
    _plugin_pawl(tmp_path, "0.9.0", mode=0o644)
    with pytest.raises(Failure) as exc:
        _call(tmp_path, _which("pdftotext"))
    assert [e["id"] for e in exc.value.errors] == ["pawl"]


@pytest.mark.parametrize("odd", ["1.0.0-rc1", "a1b2c3d", "latest"])
def test_cached_dirs_that_are_not_plain_versions_are_ignored(tmp_path, odd):
    good = _plugin_pawl(tmp_path, "0.9.0")
    _plugin_pawl(tmp_path, odd)
    result = _call(tmp_path, _which("pdftotext"))
    assert str(good) in result["tools"][2]["detail"]


def test_pawl_version_is_reported(tmp_path):
    result = _call(tmp_path, _which("pdftotext", "pawl"), pawl_version=lambda p: "pawl 0.9.0")
    assert "pawl 0.9.0" in result["tools"][2]["detail"]


@pytest.mark.parametrize("version", ["2.7.9", "3.0.0", "garbage"])
def test_rendercv_wrong_version_reported(tmp_path, version):
    with pytest.raises(Failure) as exc:
        _call(tmp_path, _which("pdftotext", "pawl"), rendercv=version)
    [error] = exc.value.errors
    assert (error["code"], error["id"]) == ("tool_missing", "rendercv")
    assert version in error["message"] and "2.8" in error["message"]


def test_cli_check_deps_json_and_exit_code(capsys):
    rc = cli.main(["check-deps"])
    out = json.loads(capsys.readouterr().out)
    # this machine has all three (RenderCV from the uv env); on others the failure shape is the contract
    assert rc == (0 if out["ok"] else 2)
    assert "tools" in out or out["errors"][0]["code"] == "tool_missing"
