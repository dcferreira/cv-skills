"""`--out` handling shared by the review subcommands (same rules as `build`)."""

from __future__ import annotations

from pathlib import Path

from cv_skills.build import _check_out_not_input, _write_atomic
from cv_skills.cli import Failure


def check_out(out: Path, inputs: dict[str, Path]) -> None:
    """Failure(2) `out_is_input` if `out` resolves to one of the input files."""
    _check_out_not_input(Path(out), {flag: Path(p) for flag, p in inputs.items()})


def write_out(out: Path, text: str) -> None:
    """Atomic write; Failure(2) `out_unwritable` if it cannot be written."""
    try:
        _write_atomic(Path(out), text)
    except OSError as e:
        raise Failure(
            2,
            [
                {
                    "code": "out_unwritable",
                    "id": str(out),
                    "message": f"Cannot write `--out {out}`: {e.strerror or e}. Choose a writable file path"
                    f" (not a directory).",
                }
            ],
        )
