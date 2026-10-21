"""`app-dir`: the name of a new application folder, `<date>-<company>-<role>[-N]`. Creates nothing."""

from __future__ import annotations

import re
import unicodedata
from datetime import date as _date
from pathlib import Path

from cv_skills.cli import Failure
from cv_skills.review import load_requirements

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def slug(text: str, cap: int = 40) -> str:
    """Lowercase ASCII words joined by `-`, at most `cap` chars; `unknown` if nothing is left."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    words = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return words[:cap].strip("-") or "unknown"


def _check_date(value: str) -> None:
    # fullmatch: `$` would accept a trailing newline.
    if DATE_RE.fullmatch(value):
        try:
            _date.fromisoformat(value)
            return
        except ValueError:
            pass
    raise Failure(
        2, [{"code": "usage", "id": "--date", "message": f"`--date` must be a real date as YYYY-MM-DD, got `{value}`."}]
    )


def app_dir(requirements: Path, parent: Path, date: str) -> dict:
    """The first free `<parent>/<date>-<company>-<role>` (then `-2`, `-3`, ...). Only reads `parent`."""
    reqs = load_requirements(requirements)
    _check_date(date)
    name = f"{date}-{slug(reqs.company)}-{slug(reqs.role)}"
    path = parent / name
    n = 2
    while path.exists():
        path = parent / f"{name}-{n}"
        n += 1
    return {"ok": True, "dir": str(path)}
