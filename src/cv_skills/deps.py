"""`check-deps`: are RenderCV 2.8, `pdftotext` and `pawl` available?

pawl is looked up on PATH first, then in the Claude Code plugin cache, where the agent-pawl plugin
ships its binary; with several cached versions the highest plain numeric one wins (directories such as
`1.0.0-rc1` or a commit hash, and non-executable files, are ignored).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

from cv_skills.cli import Failure

RENDERCV_SERIES = "2.8"
_PAWL_GLOB = ".claude/plugins/cache/*/agent-pawl/*/bin/pawl"


def _rendercv_version() -> str | None:
    try:
        return metadata.version("rendercv")
    except metadata.PackageNotFoundError:
        return None


def _pawl_version(path: str) -> str:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return " ".join(out.split())


_VERSION_DIR = re.compile(r"\d+(\.\d+)*")


def _cached_pawl(home: Path) -> str | None:
    """The executable `.../agent-pawl/<version>/bin/pawl` with the highest plain numeric version, if any."""
    found = [
        (tuple(int(n) for n in p.parent.parent.name.split(".")), p)
        for p in home.glob(_PAWL_GLOB)
        if _VERSION_DIR.fullmatch(p.parent.parent.name) and p.is_file() and os.access(p, os.X_OK)
    ]
    return str(max(found)[1]) if found else None


def _error(id: str, message: str) -> dict:
    return {"code": "tool_missing", "id": id, "message": message}


def check_deps(
    *,
    which: Callable[[str], str | None] = shutil.which,
    home: Path | None = None,
    rendercv_version: Callable[[], str | None] = _rendercv_version,
    pawl_version: Callable[[str], str] = _pawl_version,
) -> dict:
    """`{"ok": True, "tools": [...]}`, or `Failure(2, ...)` with one `tool_missing` error per missing tool."""
    home = Path.home() if home is None else home
    tools: list[dict] = []
    errors: list[dict] = []

    version = rendercv_version()
    if version is not None and (version == RENDERCV_SERIES or version.startswith(RENDERCV_SERIES + ".")):
        tools.append({"name": "rendercv", "found": True, "detail": f"RenderCV {version}"})
    else:
        found = f"found version {version}" if version else "not installed"
        errors.append(
            _error(
                "rendercv",
                f"RenderCV {RENDERCV_SERIES} is required ({found}). Run `uv sync` in the cv-skills plugin"
                f" directory; it pins RenderCV {RENDERCV_SERIES}.",
            )
        )

    pdftotext = which("pdftotext")
    if pdftotext:
        tools.append({"name": "pdftotext", "found": True, "detail": pdftotext})
    else:
        errors.append(
            _error(
                "pdftotext",
                "`pdftotext` is not on PATH. Install poppler (e.g. `apt install poppler-utils`,"
                " `pacman -S poppler`, `brew install poppler`).",
            )
        )

    pawl = which("pawl")
    if not pawl:
        pawl = _cached_pawl(home)
    if pawl:
        detail = pawl_version(pawl)
        tools.append({"name": "pawl", "found": True, "detail": f"{pawl} ({detail})" if detail else pawl})
    else:
        errors.append(
            _error(
                "pawl",
                "`pawl` was found neither on PATH nor in the plugin cache. Install the agent-pawl Claude Code"
                " plugin (it bundles the binary).",
            )
        )

    if errors:
        raise Failure(2, errors)
    return {"ok": True, "tools": tools}
