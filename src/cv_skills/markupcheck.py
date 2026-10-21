"""Text RenderCV 2.8 loses right after inline markup.

RenderCV turns `**x**`/`__x__` into `#strong[x]`, `*x*` into `#emph[x]` and
`[t](u)` into `#link("u")[t]`. In Typst markup a `#` expression continues into
a directly following `;` (which ends it and is dropped from the PDF), `(` (call
arguments) or `.` + letter (field access); those two make the render fail. Markup
nested in another's content behaves the same. Inline code is immune. `swallowed`
runs RenderCV's own string pipeline and scans its output.
"""

from __future__ import annotations

from rendercv.renderer.templater.markdown_parser import markdown_to_typst
from rendercv.renderer.templater.string_processor import make_keywords_bold

_EXPRS = ("#strong[", "#emph[", "#link(")


def _block_end(t: str, i: int) -> int:
    """Index just past the `[...]` content block starting at `t[i] == "["` (nesting, `\\` escapes)."""
    depth = 0
    while i < len(t):
        ch = t[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(t)


def _string_end(t: str, i: int) -> int:
    """Index just past the quoted string starting at `t[i] == '"'`."""
    i += 1
    while i < len(t):
        if t[i] == "\\":
            i += 2
            continue
        if t[i] == '"':
            return i + 1
        i += 1
    return len(t)


def _expr_at(t: str, i: int) -> tuple[str, int, str, str] | None:
    """(kind, end, content, url) of a markup expression starting at `t[i]`, else None."""
    for prefix in _EXPRS:
        if not t.startswith(prefix, i):
            continue
        if prefix == "#link(":
            j = i + len(prefix)
            if not t.startswith('"', j):
                return None
            url_end = _string_end(t, j)
            url = t[j + 1 : url_end - 1].replace('\\"', '"')
            if not t.startswith(")[", url_end):
                return None
            start = url_end + 1
            end = _block_end(t, start)
            return "link", end, t[start + 1 : end - 1], url
        start = i + len(prefix) - 1
        end = _block_end(t, start)
        return prefix[1:-1], end, t[start + 1 : end - 1], ""
    return None


def _source(t: str) -> str:
    """Typst markup back to the Markdown it reads as: nested markup rebuilt, escapes undone."""
    out: list[str] = []
    i = 0
    while i < len(t):
        expr = _expr_at(t, i)
        if expr is not None:
            kind, end, content, url = expr
            inner = _source(content)
            out.append(
                f"**{inner}**" if kind == "strong" else f"*{inner}*" if kind == "emph" else f"[{inner}]({url})"
            )
            i = end
        elif t[i] == "\\" and i + 1 < len(t):
            out.append(t[i + 1])
            i += 2
        else:
            out.append(t[i])
            i += 1
    return "".join(out)


def _lost_after(t: str, end: int) -> str | None:
    """The text Typst swallows right after an expression ending at `end`, if any."""
    nxt = t[end : end + 1]
    if nxt in (";", "("):
        return nxt
    if nxt == "." and t[end + 1 : end + 2].isalpha():
        return t[end : end + 2]
    return None


def swallowed(text: str, bold_keywords: list[str] | None = None) -> list[tuple[str, str]]:
    """(markup as Markdown, swallowed text) for each place RenderCV/Typst would lose text in `text`.

    `bold_keywords` is the master's `settings.bold_keywords` (RenderCV bolds them first).
    """
    t = markdown_to_typst(make_keywords_bold(text, list(bold_keywords or [])))
    found: list[tuple[str, str]] = []
    i = 0
    while i < len(t):
        ch = t[i]
        if ch == "\\":
            i += 2
        elif ch == "`":  # raw/inline code: contents are literal
            close = t.find("`", i + 1)
            i = len(t) if close == -1 else close + 1
        elif (expr := _expr_at(t, i)) is not None:
            _, end, content, _ = expr
            lost = _lost_after(t, end)
            if lost is not None:
                found.append((_source(t[i:end]), lost))
            i = end - 1 - len(content)  # scan on inside the content block (markup nests), not the URL
        else:
            i += 1
    return found
