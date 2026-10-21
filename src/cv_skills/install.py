"""`install-workflow`: copy the `tailor` workflow and its shims into a CV repo's `.claude/workflows/`.

pawl resolves workflows only from a repo's (or the user's) `.claude/workflows/`, never from a plugin,
so the cv-skills:tailor skill runs this on every run. The shims get this plugin's root written in.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from cv_skills.cli import Failure
from cv_skills.deps import _cached_pawl
from cv_skills.outfile import write_out

# The uv project root, which is also the plugin root.
ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = "@CV_SKILLS_ROOT@"
# Paths under `workflow/` in the plugin, and under `--dest` once installed.
FILES = ("tailor.yaml", "scripts/tailor/step.sh", "scripts/tailor/cv.sh")


def _live_tailor_runs(dest: Path) -> list[str]:
    """Ids of `tailor` runs pawl reports as live (running or blocked) for the working copy holding `dest`.

    Without a pawl to ask, or if it fails, nothing is reported: the check is a guard, not a dependency.
    """
    pawl = shutil.which("pawl") or _cached_pawl(Path.home())
    # Ask from the repo root: without .git/.jj, pawl keys runs by the cwd itself.
    start = dest.parent.parent if dest.parts[-2:] == (".claude", "workflows") else dest
    cwd = next((p for p in (start, *start.parents) if p.is_dir()), None)
    if pawl is None or cwd is None:
        return []
    try:
        r = subprocess.run([pawl, "status", "--json"], cwd=cwd, capture_output=True, text=True, timeout=30)
        runs = json.loads(r.stdout)["runs"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return []
    def is_tailor(run: dict) -> bool:
        return run.get("workflow") == "tailor" or str(run.get("workflow_path", "")).endswith("/tailor.yaml")

    # A run this pawl cannot read (written by a newer pawl, or damaged) may still be live: count it.
    live = ("running", "blocked", "newer-format", "unreadable")
    return [
        str(run.get("run_id"))
        for run in runs
        if isinstance(run, dict) and is_tailor(run) and run.get("status") in live
    ]


def install_workflow(dest: Path, root: Path = ROOT) -> dict:
    """Write every file in FILES to `dest`, shims with `root` filled in and mode 0755. Idempotent.

    pawl refuses to resume a run whose workflow file changed, so a change under a live `tailor` run is
    refused (`run_live`, nothing written); the same bytes are always fine.
    """
    dest = Path(dest)
    texts = {}
    for name in FILES:
        text = (root / "workflow" / name).read_text(encoding="utf-8")
        if name.endswith(".sh"):
            text = text.replace(PLACEHOLDER, shlex.quote(str(root)))
        texts[name] = text
    # pawl pins only the workflow file; the shims carry the plugin root and may follow an update.
    workflow = dest / "tailor.yaml"
    changed = workflow.is_file() and workflow.read_bytes() != texts["tailor.yaml"].encode("utf-8")
    live = _live_tailor_runs(dest) if changed else []
    if live:
        raise Failure(
            2,
            [
                {
                    "code": "run_live",
                    "id": str(workflow),
                    "message": f"tailor run {', '.join(live)} is still live and pawl only resumes it on the workflow"
                    " it started with. Resume it with `pawl run tailor` (no reinstall), or end it"
                    " (`pawl abandon --run <id>`) before installing this version.",
                }
            ],
        )
    written = []
    for name, text in texts.items():
        target = dest / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # write_out reports it as out_unwritable
        write_out(target, text)
        if name.endswith(".sh"):
            os.chmod(target, 0o755)
        written.append(str(target))
    return {"ok": True, "files": written}
