"""`source-check`: does the master's text occur in the CV it was converted from?

This is the no-invention guarantee of `cv-skills:setup`. It guards the conversion only: it is not a
gate on later edits of the master, which are the user's own text.

Checked: every body string (summary, highlights, bullet, skills items, text entries) and the entry
fields company, position, institution, area, degree, name, title, authors, journal, number,
reversed_number and label. A company must also sit near its position, and an institution near its
area and degree (within WINDOW characters), so a promoted title or a swapped employer is caught.
Not checked: dates, `location`, the header (name, headline, contact) and link targets; dates and
contact fields change format in conversion ("May 2019" -> 2019-05), so the user checks those.

A string matches when it occurs in the source as a whole, starting and ending on a word boundary. It
is a guard, not proof: it cannot see two source bullets joined into one, or a sentence cut short.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from cv_skills.cli import Failure
from cv_skills.finalrender import _fold, _key, _matches
from cv_skills.master import Master, _sections, load_master
from cv_skills.render import pdf_text

ENTRY_FIELDS = (
    "company", "position", "institution", "area", "degree", "name",
    "title", "journal", "number", "reversed_number", "label",
)  # fmt: skip
# Fields that belong to one entry's heading and must occur together in the source.
TOGETHER = (("company", "position"), ("institution", "area", "degree"))
WINDOW = 150  # characters (whitespace and hyphens excluded) between fields of one entry's heading
SHORT = 3  # items of at most this many characters ("R", "Go", "C++") must stand alone as a token

_COMMENT = re.compile(r"(?<!\\)((?:\\\\)*)%[^\n]*")
_SKIPPED_BLOCKS = re.compile(r"\\iffalse\b.*?\\fi\b|\\begin\{comment\}.*?\\end\{comment\}", re.S)
_DOTLESS = re.compile(r"\\([ij])(?![A-Za-z])\s?")
_ACCENT = re.compile(r"""\\(["'`^~=.])\s*\{?([A-Za-z])\}?|\\([cvHuk])\s*\{([A-Za-z])\}""")
_COMBINING = {
    '"': "̈", "'": "́", "`": "̀", "^": "̂", "~": "̃", "=": "̄", ".": "̇",
    "c": "̧", "v": "̌", "H": "̋", "u": "̆", "k": "̨",
}  # fmt: skip
_ESCAPED = re.compile(r"\\([&%$#_{}])")
_SYMBOL = re.compile(r"\\(ss|aa|AA|ae|AE|oe|OE|o|O|l|L|sim|times|ldots|dots|textasciitilde)(?![A-Za-z])\s?")
_SYMBOLS = {
    "ss": "ß", "aa": "å", "AA": "Å", "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ", "o": "ø", "O": "Ø",
    "l": "ł", "L": "Ł", "sim": "~", "times": "×", "ldots": "…", "dots": "…", "textasciitilde": "~",
}  # fmt: skip
_LINK_URL = re.compile(r"\\href\s*(?:\[[^\]\n]*\])?\s*\{(?:[^{}\n]|\{\})*\}|\\hyperref\s*\[[^\]\n]*\]")
_COMMAND = re.compile(r"\\[A-Za-z]+\*?")
_LATEX_QUOTES = re.compile(r"``|''")
_PLACEHOLDER = {"&": "\ue000", "%": "\ue001", "$": "\ue002", "#": "\ue003", "_": "\ue004", "{": "\ue005", "}": "\ue006"}
_DIGIT_DASH = re.compile(r"(?<=\d)[-‐‑‒–—―−]+(?=\d)")


def latex_to_text(tex: str) -> str:
    """Approximate plain text of LaTeX source: comments gone, escapes, accents and symbols resolved, commands dropped.

    Deliberately lossy on layout and generous on content (macro definitions stay in as text): the
    check only asks whether a string occurs, so extra source text is harmless.
    """
    # Links first: a raw `%` or an accent-like escape inside a URL must not reach the comment and accent steps.
    # `\href{url}{text}` shows only `text`; `\url{u}` shows `u` and stays.
    text = _LINK_URL.sub(" ", tex)
    text = _COMMENT.sub(r"\1", text)
    text = _SKIPPED_BLOCKS.sub(" ", text)
    text = _DOTLESS.sub(r"\1", text)
    text = _ACCENT.sub(
        lambda m: unicodedata.normalize("NFC", (m.group(2) or m.group(4)) + _COMBINING[m.group(1) or m.group(3)]), text
    )
    text = _ESCAPED.sub(lambda m: _PLACEHOLDER[m.group(1)], text)
    text = text.replace("\\\\", " ").replace("~", " ")
    text = _SYMBOL.sub(lambda m: _SYMBOLS[m.group(1)], text)
    text = _COMMAND.sub(" ", text)
    text = _LATEX_QUOTES.sub('"', text)
    text = re.sub(r"[{}$\\]", " ", text)
    return text.translate({ord(v): k for k, v in _PLACEHOLDER.items()})


def _unreadable(path: Path, why: str) -> Failure:
    return Failure(
        2,
        [{"code": "source_unreadable", "id": str(path), "message": f"Cannot read the source CV `{path}`: {why}. Pass the file the master was converted from."}],
    )


def read_source(path: Path) -> tuple[str, str]:
    """(text, kind) of the source CV; kind is `tex`, `pdf`, `yaml` or `text`."""
    suffix = path.suffix.lower()
    if path.is_dir():
        raise _unreadable(path, "it is a directory")
    if not path.is_file():
        raise _unreadable(path, "no such file")
    if suffix == ".pdf":
        try:
            return pdf_text(path), "pdf"
        except Failure as f:
            if any(e["code"] == "render_failed" for e in f.errors):
                raise _unreadable(path, "pdftotext could not read it (is it a PDF?)")
            raise
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise _unreadable(path, e.strerror or str(e))
    except UnicodeDecodeError:
        raise _unreadable(path, "it is not UTF-8 text (is it a PDF with the wrong suffix?)")
    if suffix == ".tex":
        return latex_to_text(text), "tex"
    return text, "yaml" if suffix in (".yaml", ".yml") else "text"


def master_items(m: Master) -> tuple[list[tuple[str, str]], list[tuple[str, list[tuple[str, str]]]]]:
    """(ID, text) of everything compared, in master order; and per entry the heading fields that must occur together."""
    sections = {str(k): v for k, v in _sections(m.data).items()}
    items: list[tuple[str, str]] = []
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    for node in m.nodes.values():
        if node.kind == "entry" and not node.swappable:
            section, index = node.id.rsplit(".", 1)
            entry = sections[section][int(index)]
            fields = {f: entry[f] for f in ENTRY_FIELDS if isinstance(entry.get(f), str)}
            items += [(f"{node.id}.{f}", text) for f, text in fields.items()]
            if isinstance(entry.get("authors"), list):
                items += [(f"{node.id}.authors.{i}", a) for i, a in enumerate(entry["authors"]) if isinstance(a, str)]
            for names in TOGETHER:
                heading = [(f, fields[f]) for f in names if f in fields]
                if len(heading) >= 2:
                    groups.append((node.id, heading))
        elif node.swappable and node.text:
            items.append((node.id, node.text))
    return [(id, text) for id, text in items if _key(text)[0]], groups


def _prep(text: str) -> str:
    """Hyphens between digits carry meaning (2-3 is not 23): mark them so the comparison keeps them."""
    return _DIGIT_DASH.sub("•", text)


def _snippet(text: str) -> str:
    return " ".join(text.split())[:60]


def source_check(master: Path, source: Path) -> dict:
    m = load_master(master)
    text, kind = read_source(source)
    hay, bounds = _key(_prep(text))
    folded = " ".join(_fold(text).split())
    items, groups = master_items(m)

    def found(item: str) -> bool:
        if len(_key(item)[0]) <= SHORT:  # a lone "R" must not be found inside "R&D"
            token = re.escape(" ".join(_fold(item).split()))
            return re.search(rf"(?<![\w&]){token}(?![\w&])", folded) is not None
        return next(_matches(hay, bounds, _key(_prep(item))[0]), None) is not None

    errors = []
    missing = set()
    for id, item in items:
        if not found(item):
            missing.add(id)
            errors.append(
                {
                    "code": "text_not_in_source",
                    "id": id,
                    "message": f"`{id}` (\"{_snippet(item)}\") does not occur in the source CV. A conversion must"
                    f" copy the source wording: restore the original text or delete the item.",
                }
            )
    for entry_id, heading in groups:
        if any(f"{entry_id}.{f}" in missing for f, _ in heading):
            continue  # already reported on its own
        spots = [[at for at in _matches(hay, bounds, _key(_prep(t))[0])] for _, t in heading]
        if not any(all(any(abs(q - p) <= WINDOW for q in rest) for rest in spots[1:]) for p in spots[0]):
            names = ", ".join(f"{f} \"{_snippet(t)}\"" for f, t in heading)
            errors.append(
                {
                    "code": "text_not_in_source",
                    "id": entry_id,
                    "message": f"`{entry_id}`: {names} do not occur together in the source CV (within {WINDOW}"
                    f" characters), so a title or name may have been moved from another entry. Restore the"
                    f" original pairing.",
                }
            )
    if errors:
        raise Failure(1, errors)
    return {"ok": True, "checked_items": len(items), "source": kind}
