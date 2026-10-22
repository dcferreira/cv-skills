"""`cv-skills review-page`: one static HTML file for the human review (step 7).

The CV is laid out from the tailored structure (master text at the selected
IDs + the listed swaps, exactly as `build` assembles it), so every swap can be
marked at its exact offsets. The page is self-contained: no scripts, no
external resources. All text is HTML-escaped before the small inline-markup
pass (bold, italic, code, http(s)/mailto links) runs.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from cv_skills.build import read_selection, tailored_yaml, title_changes
from cv_skills.cli import Failure
from cv_skills.cvtext import load_tailored, section_title
from cv_skills.fetch import check_url
from cv_skills.master import Master, load_master, strip_extra_keys
from cv_skills.outfile import check_out, write_out
from cv_skills.review import NONE_CATEGORIES
from cv_skills.selection import Selection, check_structure
from cv_skills.swaps import apply_swaps, check_swaps

_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC = re.compile(r"\*([^*]+)\*")
_DATES = ("start_date", "end_date", "date")
_NOT_HEADER = {"summary", "highlights", "details", "bullet", "pin", "tags"}


def _error(code: str, id: str, message: str) -> dict:
    return {"code": code, "id": id, "message": message}


# --- inputs ------------------------------------------------------------------


def _load_json_object(path: Path, code: str, what: str, fix: str) -> dict:
    path = Path(path)
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except OSError as e:
        why = f"cannot be read: {e.strerror or e}"
    except UnicodeDecodeError as e:
        why = f"is not UTF-8: {e}"
    except json.JSONDecodeError as e:
        why = f"is not valid JSON: {e}"
    else:
        if isinstance(data, dict):
            return data
        why = "must be a JSON object"
    raise Failure(2, [_error(code, str(path), f"{what} `{path}` {why}. {fix}")])


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def load_page_check(path: Path) -> dict:
    """The `page-check` JSON; extra keys are ignored, the five known ones are required."""
    fix = "Pass the JSON printed by `cv-skills page-check`."
    data = _load_json_object(path, "page_check_invalid", "Page-check file", fix)
    kinds = {
        "pages": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "limit": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "fits": lambda v: isinstance(v, bool),
        "last_page_remaining_mm": _is_num,
        "last_page_fill_ratio": _is_num,
    }
    bad = [k for k, ok in kinds.items() if k not in data or not ok(data[k])]
    if bad:
        raise Failure(
            2,
            [
                _error(
                    "page_check_invalid",
                    str(path),
                    f"Page-check file `{path}` has missing or wrongly typed keys: {', '.join(bad)}. {fix}",
                )
            ],
        )
    return data


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_str_list(v: object) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


_ROW_CHECKS = {
    "id": lambda v: isinstance(v, str),
    "kind": lambda v: v in ("must", "nice"),
    "text": lambda v: isinstance(v, str),
    "status": lambda v: v in ("met", "partial", "missing"),
    "claimed_status": lambda v: v in ("met", "partial", "missing"),
    "downgraded": lambda v: isinstance(v, bool),
    "evidence_quote": lambda v: isinstance(v, str),
    "reason": lambda v: isinstance(v, str),
}
_JUDGE_CHECKS = {k: _ROW_CHECKS[k] for k in ("id", "kind", "text")}
_REVIEW_CHECKS = {
    "tailored_sha256": lambda v: isinstance(v, str),
    "company": lambda v: isinstance(v, str),
    "role": lambda v: isinstance(v, str),
    "top_fixes": _is_str_list,
    "missed_requirements": _is_str_list,
    "gate": lambda v: v in ("pass", "fail"),
    "outcome": lambda v: v in ("pass", "redraft", "human"),
    "supported_must": _is_int,
    "total_must": _is_int,
    "unmet_must": _is_str_list,
}


def load_verified(path: Path) -> dict:
    """The verified review written by `check-review --out`; every field is type-checked."""
    fix = "Pass the `--out` file of `cv-skills check-review`, not the recruiter's review.json."
    data = _load_json_object(path, "review_invalid", "Verified review", fix)
    bad = [k for k, ok in _REVIEW_CHECKS.items() if k not in data or not ok(data[k])]
    rows = data.get("requirements")
    if not isinstance(rows, list):
        bad.append("requirements")
    else:
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                bad.append(f"requirements[{i}]")
                continue
            bad.extend(f"requirements[{i}].{k}" for k, ok in _ROW_CHECKS.items() if k not in row or not ok(row[k]))
    judge = data.get("for_you_to_judge")
    if not isinstance(judge, list):
        bad.append("for_you_to_judge")
    else:
        for i, item in enumerate(judge):
            if not isinstance(item, dict):
                bad.append(f"for_you_to_judge[{i}]")
                continue
            bad.extend(f"for_you_to_judge[{i}].{k}" for k, ok in _JUDGE_CHECKS.items() if k not in item or not ok(item[k]))
    if bad:
        raise Failure(
            2,
            [
                _error(
                    "review_invalid",
                    str(path),
                    f"Verified review `{path}` is not a check-review result (missing or wrongly typed:"
                    f" {', '.join(bad[:5])}). {fix}",
                )
            ],
        )
    return data


# --- text with marked swaps --------------------------------------------------


def _sentinels(*pools: str) -> tuple[str, str, str]:
    """Three private-use characters that occur in none of the page's texts."""
    used = "".join(pools)
    free = [c for c in map(chr, range(0xE000, 0xF8FF)) if c not in used]
    return free[0], free[1], free[2]


class _Renderer:
    def __init__(
        self, m: Master, texts: dict[str, str], records: list[dict], titles: list[dict], written=None, note=None
    ) -> None:
        self.m, self.texts = m, texts
        self.written = written  # (Guide, sentences) of a written summary, or None
        self.note = note  # (section, text): why that section is the master's own summary, or None
        self.titles = {t["entry"]: (k + 1, t) for k, t in enumerate(titles)}  # entry -> (T number, record)
        self.by_target: dict[str, list[dict]] = defaultdict(list)
        for r in records:
            self.by_target[r["target_id"]].append(r)
        self.originals = {r["index"] + 1: r["from"] for r in records}
        # Sentinels carry a swap through the escape and markup passes; never collide with real text.
        pool = [n.text or "" for n in m.nodes.values()] + list(texts.values())
        pool += [str(m.data["cv"].get(k, "")) for k in m.data["cv"]]
        pool += [r["to"] + r["from"] for r in records]
        pool += [s.text for s in written[1]] + list(written[0].facts) if written else []
        self.s_open, self.s_close, self.s_num = _sentinels(*pool)
        o, c, n = map(re.escape, (self.s_open, self.s_close, self.s_num))
        self._swap = re.compile(f"{o}(\\d+){n}(.*?){c}", re.DOTALL)
        self._link = re.compile(rf"\[([^\]]*)\]\((https?://[^)\s{o}{c}{n}]*|mailto:[^)\s{o}{c}{n}]*)\)")

    def _balanced(self, s: str) -> bool:
        """True if every swap opened inside `s` is also closed inside it."""
        depth = 0
        for ch in s:
            if ch == self.s_open:
                depth += 1
            elif ch == self.s_close:
                depth -= 1
                if depth < 0:
                    return False
        return depth == 0

    def _wrap(self, group: int, fmt: str):
        """sub callback: apply `fmt` unless the match would cut through a swap (then leave it literal)."""

        def repl(mo: re.Match) -> str:
            if not self._balanced(mo.group(group)):
                return mo.group(0)
            return fmt.format(*mo.groups())

        return repl

    def _marked(self, text: str, recs: list[dict]) -> str:
        """`text` with each swap's `to` wrapped in sentinels (offsets are the build's)."""
        out, pos = [], 0
        for r in sorted(recs, key=lambda r: r["start"]):
            out.append(text[pos : r["start"]])
            out.append(f"{self.s_open}{r['index'] + 1}{self.s_num}{text[r['start'] : r['end']]}{self.s_close}")
            pos = r["end"]
        out.append(text[pos:])
        return "".join(out)

    def inline(self, marked: str) -> str:
        s = html.escape(marked)
        s = _CODE.sub(self._wrap(1, "<code>{}</code>"), s)
        s = _BOLD.sub(self._wrap(2, "<strong>{1}</strong>"), s)
        s = _ITALIC.sub(self._wrap(1, "<em>{}</em>"), s)
        s = self._link.sub(self._wrap(1, '<a href="{1}">{0}</a>'), s)
        return self._swap.sub(self._mark, s)

    def _mark(self, mo: re.Match) -> str:
        n = int(mo.group(1))
        was = html.escape(self.originals[n])
        return f'<mark class="swap" data-n="{n}" title="was: {was}">{mo.group(2)}</mark><sup>[{n}]</sup>'

    def text(self, id: str) -> str:
        """Rendered HTML of the tailored text at `id` (swaps marked)."""
        text = self.texts.get(id) or self.m.text_of(id)
        return self.inline(self._marked(text, self.by_target.get(id, [])))

    def _field(self, eid: str, key: str, value: str) -> str:
        """HTML of one heading field; a chosen title is marked like a swap, numbered `[T<n>]`."""
        if key != "position" or eid not in self.titles:
            return html.escape(value)
        n, t = self.titles[eid]
        default, chosen = html.escape(t["default"]), html.escape(t["chosen"])
        return f'<mark class="swap title" title="default: {default}">{chosen}</mark><sup>[T{n}]</sup>'

    def entry(self, eid: str, master_entry: object, e) -> str:
        if isinstance(master_entry, str):
            return f'<p class="text">{self.text(eid)}</p>'
        entry = strip_extra_keys(master_entry)
        if f"{eid}.bullet" in self.m.nodes:
            return f'<p class="bullet">{self.text(f"{eid}.bullet")}</p>'
        if "details" in entry:
            items = ", ".join(self.text(d) for d in (e.details or []))
            label = html.escape(str(entry.get("label", "")))
            return f'<p class="oneline"><strong>{label}</strong> {items}</p>'
        scalars = {k: v for k, v in entry.items() if k not in _NOT_HEADER and not isinstance(v, dict)}
        scalars = {k: ", ".join(map(str, v)) if isinstance(v, list) else v for k, v in scalars.items()}
        main = [self._field(eid, k, str(v)) for k, v in scalars.items() if k not in _DATES and v not in (None, "")]
        dates = " – ".join(str(scalars[k]) for k in _DATES if k in scalars)
        title = main[0] if main else ""
        meta = " · ".join([*main[1:], *([html.escape(dates)] if dates else [])])
        parts = [f'<div class="entry"><div class="head"><span class="title">{title}</span><span class="meta">{meta}</span></div>']
        if f"{eid}.summary" in self.m.nodes:
            parts.append(f'<p class="summary">{self.text(f"{eid}.summary")}</p>')
        if e.highlights:
            parts.append("<ul>" + "".join(f"<li>{self.text(h)}</li>" for h in e.highlights) + "</ul>")
        parts.append("</div>")
        return "".join(parts)

    def cv(self, sel: Selection) -> str:
        cv = self.m.data["cv"]
        head = [f'<h1>{html.escape(str(cv.get("name", "")))}</h1>']
        if cv.get("headline"):
            head.append(f'<p class="headline">{html.escape(str(cv["headline"]))}</p>')
        contact = [str(v) for k, v in cv.items() if k not in ("name", "headline", "sections") and isinstance(v, (str, int))]
        if contact:
            head.append(f'<p class="contact">{" · ".join(html.escape(c) for c in contact)}</p>')
        body = []
        sections = cv.get("sections") or {}
        for s in sel.sections:
            body.append(f'<h2>{html.escape(section_title(s.id))}</h2>')
            if self.written and s.id == self.written[0].section:
                body.append(self.summary())
                continue
            if self.note and s.id == self.note[0]:
                body.append(f'<p class="summary-note">{html.escape(self.note[1])}</p>')
            for e in s.entries:
                body.append(self.entry(e.id, sections[s.id][int(e.id.rsplit(".", 1)[1])], e))
        return '<article class="cv">' + "".join(head + body) + "</article>"

    def summary(self) -> str:
        """The written summary: a badge, then each sentence marked with its sources and numbered `[S<n>]`."""
        spans = " ".join(
            f'<span class="written" title="sources: {html.escape(", ".join(s.sources))}">{self.inline(s.text.strip())}'
            f"</span><sup>[S{k}]</sup>"
            for k, s in enumerate(self.written[1], 1)
        )
        return f'<div class="written-summary"><span class="badge">written for this posting</span><p class="text">{spans}</p></div>'

    def summary_sources(self, sources: dict[str, str]) -> str:
        """Each sentence (`S<n>`) with its source IDs and their tailored texts."""
        items = []
        for k, s in enumerate(self.written[1], 1):
            lis = "".join(
                f"<li><code>{html.escape(src)}</code>: {self.inline(sources.get(src, ''))}</li>" for src in s.sources
            )
            items.append(f"<li><b>S{k}</b> {self.inline(s.text.strip())}<ul>{lis}</ul></li>")
        return f'<ol id="summary-sources">{"".join(items)}</ol>'


# --- page --------------------------------------------------------------------

CSS = """
:root{--bg:#f6f5f2;--fg:#1d1d1b;--muted:#6a6a64;--card:#fff;--line:#d9d6cf;--swap:#ffe14d;--swap-fg:#1d1d1b;
--ok:#1f7a3d;--warn:#a86400;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141413;--fg:#ecebe6;--muted:#a3a29a;
--card:#1e1e1c;--line:#3a3a36;--swap:#7a5f00;--swap-fg:#fff3b0;--ok:#5cc47d;--warn:#e0a040;--bad:#f08a82}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:60rem;margin:0 auto;padding:1.5rem 16px}
.posting{margin:-.4rem 0 1.2rem;color:var(--muted);overflow-wrap:anywhere}.posting a{color:var(--fg)}
section{margin:0 0 2rem}h2.s{font-size:1.15rem;margin:0 0 .6rem}
.banner{padding:.8rem 1rem;border:2px solid var(--line);border-radius:8px;background:var(--card)}
.banner b.pass{color:var(--ok)}.banner b.fail{color:var(--bad)}.banner p.stale{color:var(--bad)}
.cv{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:1.2rem 1.5rem}
.cv h1{margin:0;font-size:1.6rem}.cv h2{font-size:1rem;text-transform:uppercase;letter-spacing:.06em;
border-bottom:1px solid var(--line);margin:1.2rem 0 .5rem}.headline,.contact,.meta{color:var(--muted)}
.head{display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap}.title{font-weight:600}
.cv ul{margin:.2rem 0 .6rem;padding-left:1.3rem}.cv p{margin:.3rem 0}
mark.swap{background:var(--swap);color:var(--swap-fg);border-bottom:2px solid var(--bad);padding:0 .1em;
border-radius:2px;cursor:help}
sup{color:var(--bad);font-weight:700;font-size:.7em}
table{border-collapse:collapse;width:100%;background:var(--card)}th,td{border:1px solid var(--line);
padding:.4rem .6rem;text-align:left;vertical-align:top}th{background:var(--bg)}
.overflow{overflow-x:auto}
.st{font-weight:700}.st.met{color:var(--ok)}.st.partial{color:var(--warn)}.st.missing{color:var(--bad)}
.note{color:var(--bad);font-size:.9em}blockquote{margin:0;color:var(--muted)}
del{color:var(--muted)}ins{background:var(--swap);color:var(--swap-fg);text-decoration:none;padding:0 .1em}
span.written{background:var(--swap);color:var(--swap-fg);border-bottom:2px dotted var(--bad);cursor:help}
.summary-note{color:var(--muted);font-size:.9em;font-style:italic}
.badge{display:inline-block;font-size:.75em;font-weight:700;text-transform:uppercase;letter-spacing:.05em;
color:var(--bad);border:1px solid var(--bad);border-radius:4px;padding:0 .4em}
"""


def must_split(verified: dict) -> tuple[int, int]:
    """(met, partial): the verified review's must-haves by status after verification."""
    musts = [r["status"] for r in verified["requirements"] if r["kind"] == "must"]
    return musts.count("met"), musts.count("partial")


def _banner(verified: dict, page_check: dict, stale: bool = False) -> str:
    gate = verified["gate"]
    met, partial = must_split(verified)
    word = "PASS" if gate == "pass" else "FAIL"
    pc = page_check
    fit = "fits" if pc["fits"] else "does NOT fit"
    return (
        '<div class="banner">'
        f'<p><b class="{gate}">Gate: {word}</b> — {verified["supported_must"]} of {verified["total_must"]}'
        f" must-haves supported by a verified quote ({met} met, {partial} partial)."
        + (f" Unmet: {html.escape(', '.join(verified['unmet_must']))}." if verified["unmet_must"] else "")
        + "</p>"
        + (
            '<p class="stale">The recruiter graded an earlier version of this CV (its review could not run again);'
            " its quotes were re-checked against this one, but what it judged missing may now be there.</p>"
            if stale
            else ""
        )
        + f'<p>Pages: {pc["pages"]} of {pc["limit"]} allowed ({fit}). Space left on the last page: '
        f'{pc["last_page_remaining_mm"]:g} mm ({round(pc["last_page_fill_ratio"] * 100)}% full).</p>'
        "</div>"
    )


def _changes(records: list[dict], titles: list[dict], summary_row: str = "") -> str:
    """The swaps, numbered as marked, then one `T<n>` row per chosen title (default -> chosen), then the
    written summary's row (`summary_row`, already HTML)."""
    if not records and not titles and not summary_row:
        return '<p id="changes">No term swaps: the CV is master text only.</p>'
    rows = "".join(
        f"<tr><td>{r['index'] + 1}</td><td><code>{html.escape(r['target_id'])}</code></td>"
        f"<td><del>{html.escape(r['from'])}</del></td><td><ins>{html.escape(r['to'])}</ins></td></tr>"
        for r in records
    )
    rows += "".join(
        f'<tr class="title"><td>T{k}</td><td><code>{html.escape(t["entry"])}.position</code> (title)</td>'
        f"<td><del>{html.escape(t['default'])}</del></td><td><ins>{html.escape(t['chosen'])}</ins></td></tr>"
        for k, t in enumerate(titles, 1)
    )
    rows += summary_row
    return (
        '<div class="overflow"><table id="changes"><tr><th>#</th><th>Where</th><th>Original</th><th>Now</th></tr>'
        + rows
        + "</table></div>"
    )


def _requirements(verified: dict) -> str:
    rows = []
    for r in verified["requirements"]:
        status = f'<span class="st {r["status"]}">{html.escape(r["status"])}</span>'
        note = ""
        if r["downgraded"]:
            status = f'<del>{html.escape(r["claimed_status"])}</del> {status}'
            note = '<div class="note">quote not found in the CV: counted as missing</div>'
        quote = f"<blockquote>{html.escape(r['evidence_quote'])}</blockquote>" if r["evidence_quote"].strip() else "—"
        rows.append(
            f"<tr><td>{html.escape(r['id'])}</td><td>{html.escape(r['kind'])}</td><td>{html.escape(r['text'])}</td>"
            f"<td>{status}</td><td>{quote}{note}</td><td>{html.escape(r['reason'])}</td></tr>"
        )
    return (
        '<div class="overflow"><table><tr><th>ID</th><th>Kind</th><th>Requirement</th><th>Status</th>'
        "<th>Evidence quote</th><th>Reason</th></tr>" + "".join(rows) + "</table></div>"
    )


def _to_judge(verified: dict) -> str:
    items = verified["for_you_to_judge"]
    if not items:
        return "<p>None.</p>"
    lis = "".join(
        f"<li>{html.escape(r['id'])} {'<b>must</b>' if r['kind'] == 'must' else html.escape(r['kind'])}"
        f" {html.escape(r['text'])}</li>"
        for r in items
    )
    return f"<ul>{lis}</ul><p>A CV line can't show these ({NONE_CATEGORIES}); they are not in the gate.</p>"


def _list(tag: str, items: list[str], empty: str) -> str:
    if not items:
        return f"<p>{empty}</p>"
    return f"<{tag}>" + "".join(f"<li>{html.escape(i)}</li>" for i in items) + f"</{tag}>"


def _carry(report: dict | None) -> str:
    """The carried run's report (carry.json): where the selection came from, what the master changed, and the
    master items new since then, which a change request can add by ID."""
    if report is None:
        return ""
    e = html.escape

    def rows(cells: list[tuple[str, ...]]) -> str:
        if not cells:
            return "<p>None.</p>"
        return "<table>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in cells) + "</table>"

    first_new = report.get("new") or []
    example = f" (e.g. “add {e(first_new[0]['id'])}”)" if first_new else ""
    not_carried = (
        [(f"<code>{e(x['old_id'])}</code>", e(str(x["was"])), e(str(x["why"]))) for x in report.get("dropped", [])]
        + [
            (f"swap {int(x['index'])}", f"{e(str(x['from']))} → {e(str(x['to']))}", e(str(x["why"])))
            for x in report.get("swaps_dropped", [])
        ]
        + [
            (f"<code>{e(x['entry'])}</code> (title)", e(str(x["was"])), f"back to the default, {e(str(x['now']))}")
            for x in report.get("titles_reset", [])
        ]
    )
    summary = str(report.get("summary", "none"))
    return (
        '<section class="carry"><h2 class="s">Carried from an earlier run</h2>'
        f"<p>This run started from the selection reviewed in <code>{e(str(report.get('from', '')))}</code>, moved onto"
        f" the current master by matching each item's text, not redrafted. At the start of this run (before any change"
        f" request), {int(report.get('kept', 0))} items were as reviewed there."
        + ("" if summary == "none" else f" Written summary: {e(summary)}.")
        + "</p><h3>Changed in the master (carried, with the new text)</h3>"
        + rows(
            [(f"<code>{e(x['id'])}</code>", f"<del>{e(str(x['was']))}</del>", f"<ins>{e(str(x['now']))}</ins>") for x in report.get("changed", [])]
        )
        + "<h3>Not carried</h3>"
        + rows(not_carried)
        + f"<h3>New in the master since then</h3><p>Not selected: add any of them by ID in a change request{example}.</p>"
        + rows([(f"<code>{e(x['id'])}</code>", e(str(x.get("text", "")))) for x in first_new])
        + "</section>"
    )


def _posting(url: str | None) -> str:
    """The link to the original posting, under the title. A link loads nothing: the page stays self-contained."""
    if not url:
        return ""
    u = html.escape(url, quote=True)
    return f'<p class="posting">Posting: <a href="{u}" target="_blank" rel="noopener noreferrer">{u}</a></p>'


def render_page(
    m: Master,
    sel: Selection,
    texts: dict[str, str],
    records: list[dict],
    verified: dict,
    page_check: dict,
    posting_url: str | None = None,
    written=None,
    summary_note: tuple[str, str] | None = None,
    stale_review: bool = False,
    carry: dict | None = None,
) -> str:
    """The complete review page. `written` is the checked (Guide, sentences) of a written summary, or None;
    `summary_note` is (section, text), a line shown at that section when it holds the master's own summary."""
    titles = title_changes(sel, m)  # recomputed as `build` does
    r = _Renderer(m, texts, records, titles, written, None if written is not None else summary_note)
    title = html.escape(f"{verified.get('company', '')} — {verified.get('role', '')}".strip(" —") or "CV review")
    summary_row = summary_section = intro = ""
    if written is not None:
        from cv_skills.summary import paragraph, section_text, source_texts

        guide, sentences = written
        summary_row = (
            f'<tr class="summary"><td>S</td><td><code>{html.escape(guide.section)}</code> (summary)</td>'
            f"<td><del>{r.inline(section_text(m, guide.section))}</del></td>"
            f"<td><ins>{r.inline(paragraph(sentences))}</ins></td></tr>"
        )
        summary_section = (
            f'<section><h2 class="s">Summary sources</h2><p>The {html.escape(section_title(guide.section))} section'
            f" was written for this posting. Its numbers and capitalised words were checked against the sources each"
            f" sentence cites; whether its claims are fair (e.g. \"led\" vs \"contributed\") is for you to judge.</p>"
            f"{r.summary_sources(source_texts(m, sel, guide, texts))}</section>"
        )
        intro = (
            " The text marked \"written for this posting\" is an agent-written summary: hover a sentence for its"
            " sources, or see Summary sources below."
        )
    changes = len(records) + len(titles) + (1 if written is not None else 0)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>CV review: {title}</title><style>{CSS}</style></head><body><main>"
        f"<h1>CV review: {title}</h1>{_posting(posting_url)}"
        f"<section>{_banner(verified, page_check, stale_review)}</section>{_carry(carry)}"
        f'<section><h2 class="s">Tailored CV</h2><p>Highlighted text was swapped, or is a job title chosen from'
        f" the master's variants; hover it (or see the list below) for the original wording.{intro}</p>{r.cv(sel)}</section>"
        f'<section><h2 class="s">Changes ({changes})</h2>{_changes(records, titles, summary_row)}</section>'
        f"{summary_section}"
        f'<section><h2 class="s">Requirements</h2>{_requirements(verified)}</section>'
        f'<section><h2 class="s">For you to judge</h2>{_to_judge(verified)}</section>'
        f'<section><h2 class="s">Top fixes</h2>{_list("ol", verified["top_fixes"], "None suggested.")}</section>'
        f'<section><h2 class="s">Requirements the extractor may have missed</h2>'
        f'{_list("ul", verified["missed_requirements"], "None suggested.")}</section>'
        "</main></body></html>\n"
    )


def review_page(
    master_path: Path,
    selection_path: Path,
    tailored_path: Path,
    review_path: Path,
    page_check_path: Path,
    out_path: Path,
    posting_url: str | None = None,
    *,
    summary_path: Path | None = None,
    guide_path: Path | None = None,
    summary_note: tuple[str, str] | None = None,
    stale_review: bool = False,
    carry: dict | None = None,
) -> dict:
    """Write the review page; returns the ok JSON. Raises Failure (nothing written).

    With `summary_path` and `guide_path` (both or neither, as passed to `build`) the written summary is checked,
    expected in the tailored YAML, and marked on the page. Without them, `summary_note` (section, text) is a line
    shown at that section (if selected): why the CV keeps its own summary.
    """
    from cv_skills.summary import load_written, written_errors

    if posting_url is not None:
        check_url(posting_url, "--posting-url ")
    out_path = Path(out_path)
    inputs = {
        "--master": master_path,
        "--selection": selection_path,
        "--tailored": tailored_path,
        "--review": review_path,
        "--page-check": page_check_path,
    }
    inputs.update({flag: p for flag, p in (("--summary", summary_path), ("--guide", guide_path)) if p is not None})
    check_out(out_path, inputs)
    m = load_master(Path(master_path))
    sel = read_selection(Path(selection_path))
    written = load_written(summary_path, guide_path, m)
    errors = check_structure(sel, m) + check_swaps(sel, m)
    if errors:
        raise Failure(1, errors)
    if written is not None and (errors := written_errors(m, sel, written)):
        raise Failure(1, errors)
    texts, records = apply_swaps(sel, m)
    expected = tailored_yaml(sel, m, texts, written)
    tailored_path = Path(tailored_path)
    load_tailored(tailored_path)  # tailored_invalid if unreadable
    if tailored_path.read_bytes().decode("utf-8") != expected:
        raise Failure(
            1,
            [
                _error(
                    "tailored_mismatch",
                    str(tailored_path),
                    f"`{tailored_path}` is not what `cv-skills build` writes for this master and selection,"
                    f" so the page would not show what ships. Re-run `cv-skills build` and pass its output.",
                )
            ],
        )
    verified = load_verified(Path(review_path))
    page_check = load_page_check(Path(page_check_path))
    if verified["tailored_sha256"] != hashlib.sha256(tailored_path.read_bytes()).hexdigest():
        raise Failure(
            1,
            [
                _error(
                    "review_stale",
                    str(review_path),
                    f"`{review_path}` was verified against a different tailored YAML than `{tailored_path}`."
                    f" Re-run `cv-skills check-review` on the current tailored YAML, then pass its `--out`.",
                )
            ],
        )
    page = render_page(m, sel, texts, records, verified, page_check, posting_url, written, summary_note, stale_review, carry)
    write_out(out_path, page)
    result = {"ok": True, "out": str(out_path), "swaps": len(records)}
    if written is not None:
        result["summary_sentences"] = len(written[1])
    return result
