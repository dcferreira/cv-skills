"""`page-check`: render a tailored CV and report how many pages it takes and how much room is left."""

from __future__ import annotations

import tempfile
from pathlib import Path

from cv_skills.cli import Failure
from cv_skills.render import page_margins, pdf_pages, render_pdf

MM_PER_PT = 25.4 / 72
BODY_EDGE_TOLERANCE_PT = 2.0


def limit_error(value: object) -> Failure:
    return Failure(
        2, [{"code": "limit_invalid", "id": "--limit", "message": f"`--limit` must be an integer >= 1, got `{value}`."}]
    )


def page_check(yaml: Path, limit: int = 2) -> dict:
    """Measure the rendered CV against `limit` pages.

    Free space is measured from the lowest body text on the last page to the bottom margin edge. Text
    inside the bottom margin (the page footer) is not body text and is ignored.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise limit_error(limit)
    top, bottom = page_margins(yaml)
    with tempfile.TemporaryDirectory(prefix="cv-skills-") as work:
        pages = pdf_pages(render_pdf(yaml, Path(work)))
    last = pages[-1]
    edge = last.height - bottom
    # Body text ends at or above the margin edge (small tolerance: poppler boxes are font bounding boxes).
    # The footer ends below it even when its top pokes above, as happens with a small bottom margin.
    body = [ln for ln in last.lines if ln.y1 <= edge + BODY_EDGE_TOLERANCE_PT]
    lowest = max((ln.y1 for ln in body), default=top)
    usable = edge - top
    return {
        "ok": True,
        "pages": len(pages),
        "limit": limit,
        "fits": len(pages) <= limit,
        "last_page_remaining_mm": round(max(edge - lowest, 0.0) * MM_PER_PT, 1),
        "last_page_fill_ratio": round(min(max((lowest - top) / usable, 0.0), 1.0), 3),
    }
