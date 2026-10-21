"""RenderCV rendering and poppler measurement, shared by `page-check` and `final-render`."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from rendercv.exception import RenderCVUserError, RenderCVUserValidationError
from rendercv.schema.rendercv_model_builder import build_rendercv_dictionary_and_model

from cv_skills.cli import Failure
from cv_skills.master import _one_line

XHTML = "{http://www.w3.org/1999/xhtml}"
PT_PER_UNIT = {"in": 72.0, "cm": 72.0 / 2.54, "mm": 72.0 / 25.4, "pt": 1.0}


@dataclass(frozen=True)
class Line:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass(frozen=True)
class Page:
    width: float
    height: float
    lines: list[Line]


def _error(code: str, id: str, message: str) -> Failure:
    return Failure(2, [{"code": code, "id": id, "message": message}])


def _model(yaml: Path):
    """The validated RenderCV model of `yaml`; Failure(2, yaml_invalid) when it cannot be read or validated."""
    try:
        text = yaml.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise _error("yaml_invalid", str(yaml), f"Cannot read `{yaml}`: {_one_line(e)}. Pass the tailored YAML written by `build`.")
    try:
        return build_rendercv_dictionary_and_model(text, input_file_path=yaml)[1]
    except RenderCVUserValidationError as e:
        errors = []
        for ve in e.validation_errors:
            loc = ".".join(str(p) for p in ve.schema_location) if ve.schema_location else ""
            where = f" (line {ve.yaml_location[0][0]})" if ve.yaml_location else ""
            errors.append(
                {
                    "code": "yaml_invalid",
                    "id": str(yaml),
                    "message": f"`{yaml}` is not a valid RenderCV file at `{loc or 'top level'}`{where}: {' '.join(ve.message.split())}"
                    f" Pass the tailored YAML written by `build`.",
                }
            )
        raise Failure(2, errors) from e
    except RenderCVUserError as e:
        raise _error("yaml_invalid", str(yaml), f"`{yaml}` is not a valid RenderCV file: {' '.join(e.message.split())}") from e
    except Exception as e:
        raise _error(
            "yaml_invalid", str(yaml), f"`{yaml}` could not be read as a RenderCV file ({_one_line(e)}). Pass the tailored YAML written by `build`."
        ) from e


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise _error("tool_missing", name, f"`{name}` is not on PATH. Install poppler (e.g. `apt install poppler-utils`, `brew install poppler`).")
    return path


def render_pdf(yaml: Path, workdir: Path, markdown: bool = False) -> Path:
    """Render `yaml` with RenderCV into `workdir` as `cv.pdf` (and `cv.md` when `markdown`); return the PDF path.

    Only `cv.pdf`, its Typst source `cv.typ` (RenderCV builds the PDF from it) and, when asked, `cv.md` are written, all inside `workdir`.
    """
    _model(yaml)
    workdir.mkdir(parents=True, exist_ok=True)
    pdf = workdir / "cv.pdf"
    cmd = [sys.executable, "-m", "rendercv", "render", str(yaml.resolve()), "--pdf-path", str(pdf),
           "--typst-path", str(workdir / "cv.typ"), "-nopng", "-nohtml", "--quiet"]
    if markdown:
        cmd += ["--markdown-path", str(workdir / "cv.md")]
    else:
        cmd += ["-nomd"]
    run = subprocess.run(cmd, capture_output=True, text=True, cwd=yaml.parent)
    if run.returncode != 0 or not pdf.is_file():
        detail = " ".join((run.stderr or run.stdout).split())[-300:]
        raise _error("render_failed", str(yaml), f"RenderCV failed to render `{yaml}` (exit {run.returncode}): {detail}")
    return pdf


def pdf_pages(pdf: Path) -> list[Page]:
    """Pages of `pdf` with their text lines (poppler `pdftotext -bbox-layout`, units: PDF points)."""
    exe = _require_tool("pdftotext")
    run = subprocess.run([exe, "-bbox-layout", str(pdf), "-"], capture_output=True, text=True)
    if run.returncode != 0:
        raise _error("render_failed", str(pdf), f"pdftotext failed on `{pdf}`: {' '.join(run.stderr.split())}")
    root = ET.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", run.stdout))
    pages = []
    for p in root.iter(XHTML + "page"):
        lines = []
        for ln in p.iter(XHTML + "line"):
            words = " ".join((w.text or "") for w in ln.iter(XHTML + "word"))
            lines.append(Line(*(float(ln.get(k)) for k in ("xMin", "yMin", "xMax", "yMax")), words))
        pages.append(Page(float(p.get("width")), float(p.get("height")), lines))
    return pages


def page_margins(yaml: Path) -> tuple[float, float]:
    """(top, bottom) page margins of the CV's design, in PDF points."""
    page = _model(yaml).design.page
    return _points(page.top_margin), _points(page.bottom_margin)


def _points(value: str) -> float:
    m = re.fullmatch(r"\s*([0-9.]+)\s*(in|cm|mm|pt)\s*", str(value))
    if m is None:
        raise _error("render_failed", "", f"Unsupported margin value `{value}` in the design; use in, cm, mm or pt.")
    return float(m.group(1)) * PT_PER_UNIT[m.group(2)]


def pdf_text(pdf: Path) -> str:
    """The PDF's text in reading order, as an ATS would extract it (`pdftotext`, no -layout)."""
    exe = _require_tool("pdftotext")
    run = subprocess.run([exe, str(pdf), "-"], capture_output=True, text=True)
    if run.returncode != 0:
        raise _error("render_failed", str(pdf), f"pdftotext failed on `{pdf}`: {' '.join(run.stderr.split())}")
    return run.stdout
