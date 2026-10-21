"""The CV as text lines: what the recruiter reads and what its quotes are checked against.

One line per field, bullet, skills item, summary or text entry of the TAILORED
YAML. `normalise` makes a quote and a line comparable (whitespace, dashes,
quotes, markup); a quote must sit inside a single normalised line.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path

import ruamel.yaml

from rendercv.schema.models.cv.section import dictionary_key_to_proper_section_title

from cv_skills.cli import Failure
from cv_skills.master import split_details

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
_QUOTES = {
    **dict.fromkeys(map(ord, "‘’‚‛"), "'"),
    **dict.fromkeys(map(ord, "“”„‟"), '"'),
}
# Invisible; a quote copied from the PDF carries U+FE0E after a literal `*` (as final-render folds it).
_VARIATION_SELECTORS = dict.fromkeys(range(0xFE00, 0xFE10))
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC = re.compile(r"\*([^*]+)\*")
_CODE = re.compile(r"`([^`]*)`")


def strip_markup(s: str) -> str:
    """`s` with `**x**`, `__x__`, `*x*`, `` `x` `` and `[t](u)` reduced to their text, nothing else changed
    (the `cv.txt` view: copyable character for character, and a quote from it matches the normalised line)."""
    s = _LINK.sub(r"\1", s)
    s = _BOLD.sub(r"\2", s)
    s = _ITALIC.sub(r"\1", s)
    return _CODE.sub(r"\1", s)


def normalise(s: str, *, markup: bool = True) -> str:
    """Comparable form of `s`: NFKC, variation selectors dropped, dashes/quotes unified, whitespace collapsed.

    With `markup` (default) `**x**`, `__x__`, `*x*`, `` `x` `` and `[t](u)` are reduced to their text;
    without it they are kept (used to match a quote copied verbatim from a raw line).
    """
    s = unicodedata.normalize("NFKC", s).translate(_VARIATION_SELECTORS)
    if markup:
        s = strip_markup(s)
    s = s.translate(_DASHES).translate(_QUOTES)
    return " ".join(s.split())


def section_title(key: str) -> str:
    """The heading RenderCV prints for a section key (so the text matches the shipped CV)."""
    return dictionary_key_to_proper_section_title(str(key))


def _tailored_error(path: Path, why: str) -> Failure:
    return Failure(
        2,
        [
            {
                "code": "tailored_invalid",
                "id": str(path),
                "message": f"Tailored YAML `{path}` {why}. Write it with `cv-skills build` and pass that file.",
            }
        ],
    )


def load_tailored(path: Path) -> dict:
    """Read a tailored (or master) YAML file; Failure(2) `tailored_invalid` if unusable."""
    path = Path(path)
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as e:
        raise _tailored_error(path, f"cannot be read: {e.strerror or e}")
    except UnicodeDecodeError as e:
        raise _tailored_error(path, f"is not UTF-8: {e}")
    try:
        data = ruamel.yaml.YAML(typ="safe").load(text)
    except ruamel.yaml.YAMLError:
        raise _tailored_error(path, "is not valid YAML")
    if not isinstance(data, dict) or not isinstance(data.get("cv"), dict):
        raise _tailored_error(path, "must be a YAML mapping with a top-level `cv` mapping")
    problem = _structure_problem(data["cv"])
    if problem:
        raise _tailored_error(path, problem)
    return data


def _text_problem(value: object, where: str) -> str | None:
    """First string under `value` that is not valid UTF-8 text (e.g. a lone surrogate)."""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return f"has `{where}` with text that is not valid UTF-8 (a lone surrogate?)"
    elif isinstance(value, Mapping):
        for k, v in value.items():
            if (p := _text_problem(v, f"{where}.{k}")) is not None:
                return p
    elif isinstance(value, list):
        for i, v in enumerate(value):
            if (p := _text_problem(v, f"{where}[{i}]")) is not None:
                return p
    return None


def _structure_problem(cv: Mapping) -> str | None:
    """Why `cv` is not a tailored CV (sections must map string keys to lists of entries), or None."""
    if (p := _text_problem(cv, "cv")) is not None:
        return p
    sections = cv.get("sections")
    if sections is None:
        return None
    if not isinstance(sections, Mapping):
        return "has `cv.sections` that is not a mapping of section name to a list of entries"
    for key, entries in sections.items():
        if not isinstance(key, str):
            return f"has a section key `{key}` that is not a string"
        if not isinstance(entries, list):
            return f"has `cv.sections.{key}` that is not a list of entries"
        for i, entry in enumerate(entries):
            if not isinstance(entry, (str, Mapping)):
                return f"has `cv.sections.{key}[{i}]` that is neither a text entry nor a mapping"
    return None


def _one_line(s: str) -> str:
    """A string on one physical line, so a CV line is a text line (quotes never span lines)."""
    return " ".join(s.splitlines())


def _walk(value: object) -> list[str]:
    if isinstance(value, Mapping):
        return [line for v in value.values() for line in _walk(v)]
    if isinstance(value, list):
        return [line for v in value for line in _walk(v)]
    return [_one_line(str(value))] if value not in (None, "") else []


def _entry_lines(entry: object) -> list[str]:
    if not isinstance(entry, Mapping):
        return _walk(entry)
    out: list[str] = []
    for key, value in entry.items():
        if key == "details" and isinstance(value, str):
            out.extend(_one_line(item) for item in split_details(value))
        else:
            out.extend(_walk(value))
    return out


def cv_lines(doc: Mapping, exclude_section: str | None = None) -> list[str]:
    """Every string of the tailored `cv`, in document order, one CV line each.

    `exclude_section` leaves that section out, heading and lines (the written summary is never recruiter evidence);
    a key the CV does not have changes nothing.
    """
    cv = doc["cv"]
    lines: list[str] = []
    for key, value in cv.items():
        if key != "sections":
            lines.extend(_walk(value))
    for name, entries in (cv.get("sections") or {}).items():
        if name == exclude_section:
            continue
        lines.append(section_title(name))
        for entry in entries:
            lines.extend(_entry_lines(entry))
    return lines


def cv_text(doc: Mapping) -> str:
    """The CV lines joined by newlines: the text the recruiter is shown."""
    return "\n".join(cv_lines(doc))
