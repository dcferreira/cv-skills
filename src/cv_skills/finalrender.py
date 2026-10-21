"""`final-render`: PDF + Markdown from the approved tailored YAML, after a text-extraction check.

The check is the ATS guarantee: every body string of the CV (summaries, highlights, bullets, skills
items, text entries) must appear in what `pdftotext` extracts, in YAML order. Header fields (name,
headline, contact) are out of scope for that check; a second check rejects private-use
characters anywhere in the text (icon-font glyphs), header included.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Iterator
from pathlib import Path

from ruamel.yaml import YAML

from cv_skills.cli import Failure
from cv_skills.master import split_details
from cv_skills.render import _model, pdf_pages, pdf_text, render_pdf

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_DASHES = str.maketrans(dict.fromkeys("‐‑‒–—―−", "-"))
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MARKDOWN_MARKS = re.compile(r"\*\*|__|`|\*")
# Whitespace and hyphens carry no weight when comparing: a line break inside "machine-learning" may or
# may not keep the hyphen in the extracted text, and a hyphenated word wraps as "ma-\nchine". They still
# mark word boundaries, so that a short item ("Go") is not found inside a longer word ("Google").
_DROPPED = "-­"


# Variation selectors are invisible; RenderCV prints a literal `*` as #sym.ast.basic, which pdftotext
# extracts as `*` + U+FE0E.
_VARIATION_SELECTORS = dict.fromkeys(range(0xFE00, 0xFE10))


def _fold(text: str) -> str:
    """NFKC (ligatures), quotes and dashes folded, variation selectors dropped, Markdown marks and links stripped."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).translate(_DASHES).translate(_VARIATION_SELECTORS)
    text = _MARKDOWN_LINK.sub(r"\1", text)
    return _MARKDOWN_MARKS.sub("", text)


def _key(text: str) -> tuple[str, list[bool]]:
    """The folded text without whitespace and hyphens, and for each gap (len + 1 of them) whether it is a word boundary."""
    key: list[str] = []
    bounds: list[bool] = []
    dropped = True  # the start counts as a boundary
    for ch in _fold(text):
        if ch.isspace() or ch in _DROPPED:
            dropped = True
            continue
        bounds.append(dropped or not key[-1].isalnum() or not ch.isalnum())
        key.append(ch)
        dropped = False
    bounds.append(True)
    return "".join(key), bounds


def normalise(text: str) -> str:
    """Canonical form for comparing CV text with PDF text.

    NFKC (ligatures), quotes and dashes folded, Markdown marks and links stripped, then all whitespace
    and hyphens removed (line wrapping and hyphenation). Strict otherwise: any other difference fails.
    """
    return _key(text)[0]


def expected_items(yaml: Path) -> list[tuple[str, str]]:
    """(path ID in the tailored YAML, text) for every body string under `cv.sections`, in YAML order."""
    _model(yaml)  # RenderCV-valid, so the shapes below can be relied on
    data = YAML(typ="safe").load(yaml.read_text(encoding="utf-8"))
    items: list[tuple[str, str]] = []
    for section, entries in (data["cv"].get("sections") or {}).items():
        for i, entry in enumerate(entries or []):
            base = f"{section}.{i}"
            if isinstance(entry, str):
                items.append((base, entry))
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("summary") is not None:
                items.append((f"{base}.summary", str(entry["summary"])))
            for j, h in enumerate(entry.get("highlights") or []):
                items.append((f"{base}.highlights.{j}", str(h)))
            if entry.get("bullet") is not None:
                items.append((f"{base}.bullet", str(entry["bullet"])))
            if isinstance(entry.get("details"), str):
                for k, d in enumerate(split_details(entry["details"])):
                    items.append((f"{base}.details.{k}", d))
    return [(id, text) for id, text in items if normalise(text)]


def _matches(hay: str, bounds: list[bool], needle: str, start: int = 0) -> Iterator[int]:
    """Start offsets of `needle` in `hay` at or after `start` that begin and end on word boundaries."""
    at = hay.find(needle, start)
    while at >= 0:
        if bounds[at] and bounds[at + len(needle)]:
            yield at
        at = hay.find(needle, at + 1)


def check_text(items: list[tuple[str, str]], extracted: str) -> list[dict]:
    """Errors for items absent from `extracted` (`text_missing`) or out of YAML order (`text_order`)."""
    hay, bounds = _key(extracted)
    errors: list[dict] = []
    cursor = 0
    previous = ""
    used: list[tuple[int, int]] = []  # spans claimed by earlier items: a duplicate string needs its own copy
    for id, text in items:
        needle = normalise(text)
        at = next(_matches(hay, bounds, needle, cursor), -1)
        if at >= 0:
            cursor, previous = at + len(needle), id
            used.append((at, cursor))
            continue
        snippet = " ".join(text.split())[:60]
        unclaimed = any(not any(s < at + len(needle) and at < e for s, e in used) for at in _matches(hay, bounds, needle))
        if unclaimed:
            errors.append(
                {
                    "code": "text_order",
                    "id": id,
                    "message": f"`{id}` (\"{snippet}\") appears in the PDF text before `{previous or 'the start'}`, so the reading"
                    f" order is garbled. The theme or layout extracts out of order; use a single-column theme.",
                }
            )
        else:
            errors.append(
                {
                    "code": "text_missing",
                    "id": id,
                    "message": f"`{id}` (\"{snippet}\") is not in the PDF text. The PDF does not carry this text as extractable text;"
                    f" check the theme for icon fonts or images.",
                }
            )
    return errors


def check_private_use(extracted: str) -> list[dict]:
    """One `text_private_use` error if the extracted text, header included, has private-use characters.

    Icon fonts put their glyphs in the Unicode private-use areas (category Co: U+E000..F8FF and the
    supplementary planes), so an ATS reads them as junk characters in the contact line.
    """
    found = sorted({ord(c) for c in extracted if unicodedata.category(c) == "Co"})
    if not found:
        return []
    shown = ", ".join(f"U+{cp:04X}" for cp in found[:8]) + (f" and {len(found) - 8} more" if len(found) > 8 else "")
    return [
        {
            "code": "text_private_use",
            "id": "",
            "message": f"The PDF text contains private-use characters ({shown}), which are icon-font glyphs an ATS reads as junk."
            f" Set `design.header.connections.show_icons: false` (or pick a theme without icon fonts).",
        }
    ]


def _out_unwritable(out_dir: Path, why: str) -> Failure:
    return Failure(2, [{"code": "out_unwritable", "id": str(out_dir), "message": f"Cannot write to `{out_dir}`: {why}."}])


def _write_outputs(files: dict[str, Path], out_dir: Path) -> None:
    """Put each `{name: source}` file into `out_dir`, all or nothing as far as the file system allows.

    Targets are checked and every file is staged next to its target before any is renamed into place,
    so a problem with the second file leaves the first untouched. An `out_dir` created here is removed
    again on failure.
    """
    created = not out_dir.exists()
    staged: dict[Path, Path] = {}
    try:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            for name in files:
                if (out_dir / name).is_dir():
                    raise _out_unwritable(out_dir, f"`{name}` is a directory")
            mask = os.umask(0)
            os.umask(mask)
            for name, src in files.items():
                fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{name}.", suffix=".tmp")
                os.close(fd)
                staged[out_dir / name] = Path(tmp)
                shutil.copyfile(src, tmp)
                os.chmod(tmp, 0o666 & ~mask)
            for target, tmp in staged.items():
                os.replace(tmp, target)
        except OSError as e:
            raise _out_unwritable(out_dir, e.strerror or str(e)) from e
    except BaseException:
        for tmp in staged.values():
            tmp.unlink(missing_ok=True)
        if created:
            try:
                out_dir.rmdir()
            except OSError:
                pass
        raise


def final_render(yaml: Path, out_dir: Path) -> dict:
    """Render `yaml` to `out_dir/cv.pdf` and `out_dir/cv.md`, only if the text-extraction check passes."""
    items = expected_items(yaml)
    with tempfile.TemporaryDirectory(prefix="cv-skills-") as work:
        pdf = render_pdf(yaml, Path(work), markdown=True)
        extracted = pdf_text(pdf)
        errors = check_text(items, extracted) + check_private_use(extracted)
        if errors:
            raise Failure(2, errors)
        pages = len(pdf_pages(pdf))
        _write_outputs({"cv.pdf": pdf, "cv.md": Path(work) / "cv.md"}, out_dir)
    return {
        "ok": True,
        "pdf": str(out_dir / "cv.pdf"),
        "markdown": str(out_dir / "cv.md"),
        "pages": pages,
        "checked_items": len(items),
    }
