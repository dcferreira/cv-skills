import json
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from cv_skills import cli
from cv_skills.install import FILES, install_workflow

REPO = Path(__file__).resolve().parent.parent
needs_pawl = pytest.mark.skipif(shutil.which("pawl") is None, reason="pawl is not on PATH")


def _fake_pawl(tmp_path, monkeypatch, runs: list[dict]) -> None:
    """A `pawl` first on PATH whose `status --json` reports `runs`."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    payload = json.dumps({"schema_version": 1, "root": str(tmp_path), "runs": runs})
    script = bin_dir / "pawl"
    script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{payload}\nEOF\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")


def _run(workflow: str, status: str, run_id: str = "7f3a") -> dict:
    return {"run_id": run_id, "workflow": workflow, "status": status, "step": "human_review"}


def _installed_but_older(dest: Path) -> bytes:
    """Install, then make the installed tailor.yaml differ from the plugin's (an older version)."""
    install_workflow(dest)
    old = (dest / "tailor.yaml").read_bytes() + b"# older version\n"
    (dest / "tailor.yaml").write_bytes(old)
    return old


def test_refuses_to_change_the_workflow_under_a_live_run(tmp_path, monkeypatch, capsys):
    dest = tmp_path / "wf"
    old = _installed_but_older(dest)
    _fake_pawl(tmp_path, monkeypatch, [_run("tailor", "blocked")])
    assert cli.main(["install-workflow", "--dest", str(dest)]) == 2
    err = json.loads(capsys.readouterr().out)["errors"][0]
    assert err["code"] == "run_live" and "7f3a" in err["message"] and "pawl run tailor" in err["message"]
    assert (dest / "tailor.yaml").read_bytes() == old  # nothing written


def test_asks_pawl_from_the_repo_root(tmp_path, monkeypatch):
    """Without .git/.jj pawl keys runs by the cwd itself, so it must be asked from the repo root."""
    repo = tmp_path / "cv"
    dest = repo / ".claude/workflows"
    _installed_but_older(dest)
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    live = json.dumps({"runs": [_run("tailor", "blocked")]})
    (bin_dir / "pawl").write_text(
        f'#!/bin/sh\nif [ "$(pwd)" = "{repo}" ]; then echo \'{live}\'; else echo \'{{"runs": []}}\'; fi\n'
    )
    (bin_dir / "pawl").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    with pytest.raises(Exception) as exc:
        install_workflow(dest)
    assert exc.value.errors[0]["code"] == "run_live"


@pytest.mark.parametrize("status", ["newer-format", "unreadable"])
def test_unreadable_tailor_runs_count_as_live(tmp_path, monkeypatch, status):
    dest = tmp_path / "wf"
    _installed_but_older(dest)
    run = {**_run("", status), "workflow_path": str(dest / "tailor.yaml")}
    _fake_pawl(tmp_path, monkeypatch, [run])
    with pytest.raises(Exception) as exc:
        install_workflow(dest)
    assert exc.value.errors[0]["code"] == "run_live"


def test_shims_alone_may_change_under_a_live_run(tmp_path, monkeypatch):
    """pawl pins only the workflow file; shims carry the plugin root, which moves on every update."""
    dest = tmp_path / "wf"
    install_workflow(dest)
    (dest / "scripts/tailor/step.sh").write_text("#!/bin/sh\nexec old-plugin-root\n")
    _fake_pawl(tmp_path, monkeypatch, [_run("tailor", "blocked")])
    assert install_workflow(dest)["ok"]
    assert "old-plugin-root" not in (dest / "scripts/tailor/step.sh").read_text()


@pytest.mark.parametrize("status", ["running", "blocked"])
def test_same_bytes_under_a_live_run_is_fine(tmp_path, monkeypatch, status):
    dest = tmp_path / "wf"
    install_workflow(dest)
    _fake_pawl(tmp_path, monkeypatch, [_run("tailor", status)])
    assert install_workflow(dest)["ok"]


@pytest.mark.parametrize("run", [_run("tailor", "ok"), _run("tailor", "abandoned"), _run("other", "running")])
def test_finished_or_other_runs_do_not_block(tmp_path, monkeypatch, run):
    dest = tmp_path / "wf"
    _installed_but_older(dest)
    _fake_pawl(tmp_path, monkeypatch, [run])
    assert install_workflow(dest)["ok"]
    assert (dest / "tailor.yaml").read_bytes() == (REPO / "workflow/tailor.yaml").read_bytes()


def _exec_line(path: Path) -> list[str]:
    line = next(l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("exec "))
    return shlex.split(line)


def test_installs_workflow_and_shims(tmp_path):
    dest = tmp_path / ".claude/workflows"
    result = install_workflow(dest)
    assert result["ok"]
    assert sorted(result["files"]) == sorted(str(dest / f) for f in FILES)
    assert (dest / "tailor.yaml").read_bytes() == (REPO / "workflow/tailor.yaml").read_bytes()
    for shim in ("scripts/tailor/step.sh", "scripts/tailor/cv.sh"):
        path = dest / shim
        assert "@CV_SKILLS_ROOT@" not in path.read_text(encoding="utf-8")
        assert stat.S_IMODE(path.stat().st_mode) == 0o755
        argv = _exec_line(path)
        assert argv[argv.index("--project") + 1] == str(REPO)


def test_reinstall_is_byte_identical(tmp_path):
    dest = tmp_path / "wf"
    install_workflow(dest)
    before = {f: (dest / f).read_bytes() for f in FILES}
    install_workflow(dest)
    assert {f: (dest / f).read_bytes() for f in FILES} == before


def test_plugin_root_with_space_stays_one_token(tmp_path):
    root = tmp_path / "my plugins" / "cv-skills"
    shutil.copytree(REPO / "workflow", root / "workflow")
    dest = tmp_path / "wf"
    install_workflow(dest, root=root)
    argv = _exec_line(dest / "scripts/tailor/step.sh")
    assert argv[argv.index("--project") + 1] == str(root)


def test_cli_install_workflow(tmp_path, capsys):
    assert cli.main(["install-workflow", "--dest", str(tmp_path / "wf")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and len(out["files"]) == len(FILES)


def test_dest_unwritable(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert cli.main(["install-workflow", "--dest", str(blocker / "wf")]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["errors"][0]["code"] == "out_unwritable"


@needs_pawl
def test_installed_workflow_validates(tmp_path):
    dest = tmp_path / "wf"
    install_workflow(dest)
    r = subprocess.run(["pawl", "validate", "--path", str(dest / "tailor.yaml")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_installed_shim_runs(tmp_path):
    """The real shim, run from a CV repo root, reaches the adapters through uv."""
    dest = tmp_path / ".claude/workflows"
    install_workflow(dest)
    shutil.copy(REPO / "examples/master.yaml", tmp_path / "master.yaml")
    r = subprocess.run(
        [str(dest / "scripts/tailor/step.sh"), "floor", "master.yaml", "2"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip().splitlines()[-1].startswith("fits master_sha256=")
