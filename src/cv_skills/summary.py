"""The summary paragraph written for one posting (WP17): its guide, `summary.json` and the deterministic check.

The guide (`summary-guide.md`, YAML front matter between `---` lines, then free text for the writer) names the
master section the paragraph replaces and its bounds. `summary.json` is `{"sentences": [{"text", "sources"}]}`.
`check_summary` holds every number (digits, or a number word) and every word with a capital letter (but an
ordinary first word, see `OPENERS`) to the TAILORED text of the sources the sentence cites: selected items,
selected entries' own fields (the chosen title included), the chosen title alone (`<entry>.position`) and the
guide's facts (`guide.facts.<n>`). Only `**bold**` markup is allowed. It proves where numbers and names come
from; it cannot prove that verbs or claims are faithful.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import ruamel.yaml
from ruamel.yaml.comments import CommentedSeq

from cv_skills.cli import Failure
from cv_skills.cvtext import normalise, strip_markup
from cv_skills.markupcheck import swallowed
from cv_skills.master import EXTRA_KEYS, Master, bold_keywords, markup_message, strip_extra_keys
from cv_skills.selection import Selection
from cv_skills.swaps import apply_swaps

MAX_SENTENCES = 5
DEFAULT_SENTENCES = 3
_GUIDE_KEYS = ("section", "max_chars", "max_sentences", "facts")
_SUMMARY_FIX = 'Write it as {"sentences": [{"text": "…", "sources": ["<id>", …]}]}.'
_GUIDE_FIX = "See the guide format in docs/contract.md."
# Whole tokens: word characters, joined only by `.`, `,` or `'` between them (`1.5`, `1,200`, `Node.js`, `Robin's`).
_TOKEN = re.compile(r"\w+(?:[.,']\w+)*")
# Fields of a structured entry that have their own IDs (or are never printed) and so are not its "own" text.
_CHILD_FIELDS = {"highlights", "details", "summary", "bullet", *EXTRA_KEYS}
# Numbers written as words: held to the cited sources like digits (case-insensitive). A closed list.
NUMBER_WORDS = frozenset(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen"
    " seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million"
    " billion dozen dozens half double triple twice once thrice"
    " first second third fourth fifth sixth seventh eighth ninth tenth".split()
)
# A sentence's capitalised first word is exempt when it is one of these (or in its sources, any case). A short,
# closed list of ordinary openers of a CV summary; anything else must be in the sources, like any capitalised word.
OPENERS = frozenset(
    "a an the i my with over across from for in on at as by data senior lead staff principal engineer engineering"
    " builder developer specialist experienced hands".split()
)
_TYPST_CMD = re.compile(r"#[^\W\d_]")  # RenderCV passes `#name` through as a Typst command
_BOLD = re.compile(r"\*\*[^*]+?\*\*")


@dataclass(frozen=True)
class Guide:
    path: Path
    section: str
    max_chars: int
    max_sentences: int
    facts: tuple[str, ...]
    body: str  # for the writer only; never interpreted


@dataclass(frozen=True)
class Sentence:
    text: str
    sources: tuple[str, ...]


def _error(code: str, id: str, message: str) -> dict:
    return {"code": code, "id": id, "message": message}


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _one_line_text(v: object) -> bool:
    """A non-empty, valid UTF-8 string with no control character (a line break included)."""
    if not isinstance(v, str) or not v.strip():
        return False
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any(ord(c) < 32 or ord(c) == 127 for c in v)


# --- guide -------------------------------------------------------------------


def section_text(m: Master, section: str) -> str:
    """The master text of a plain-text section: its entries joined by " "."""
    return " ".join(m.data["cv"]["sections"][section])


def _front_matter(text: str) -> tuple[str, str] | None:
    """(front matter, body) of a `---`-fenced file, or None if it has none."""
    lines = text.removeprefix("﻿").splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return None
    for k in range(1, len(lines)):
        if lines[k].rstrip() == "---":
            return "".join(lines[1:k]), "".join(lines[k + 1 :])
    return None


def load_guide(path: Path, m: Master) -> Guide:
    """Read and check the summary guide against the master; any problem is Failure(2) `guide_invalid`."""
    path = Path(path)

    def file_error(why: str) -> Failure:
        return Failure(2, [_error("guide_invalid", str(path), f"Summary guide `{path}` {why} {_GUIDE_FIX}")])

    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as e:
        raise file_error(f"cannot be read: {e.strerror or e}.")
    except UnicodeDecodeError as e:
        raise file_error(f"is not UTF-8: {e}.")
    parts = _front_matter(text)
    if parts is None:
        raise file_error("has no front matter: it must start with a `---` line, the settings, then a `---` line.")
    front, body = parts
    try:
        data = ruamel.yaml.YAML(typ="safe").load(front)
    except ruamel.yaml.YAMLError as e:
        raise file_error(f"has front matter that is not valid YAML ({' '.join(str(e).split())}).")
    if not isinstance(data, dict):
        raise file_error("has front matter that is not a mapping of settings (`section: <key>`, …).")

    errors: list[dict] = []

    def bad(key: str, why: str) -> None:
        errors.append(_error("guide_invalid", f"guide.{key}", f"Summary guide `{path}`: `{key}` {why}"))

    for key in data:
        if key not in _GUIDE_KEYS:
            bad(str(key), f"is not a known setting (allowed: {', '.join(_GUIDE_KEYS)}). Remove it.")

    sections = m.data["cv"].get("sections") or {}
    section = data.get("section")
    master_len: int | None = None
    if "section" not in data:
        bad("section", "is missing: name the master section the summary replaces, e.g. `section: summary`.")
    elif not isinstance(section, str) or section not in sections:
        known = ", ".join(f"`{k}`" for k in sections) or "(none)"
        bad("section", f"is {section!r}, which is not a section of the master (sections: {known}).")
    elif not sections[section] or not all(isinstance(e, str) for e in sections[section]):
        bad(
            "section",
            f"names `{section}`, which is not a section of plain-text entries only; the written summary replaces"
            f" text, so pick a section whose entries are all plain strings.",
        )
    else:
        master_len = len(section_text(m, section))

    max_chars = data.get("max_chars", master_len)
    if "max_chars" in data:
        if not _is_int(max_chars) or max_chars < 1:
            bad("max_chars", f"must be an integer of at least 1, not {max_chars!r}.")
        elif master_len is not None and max_chars > master_len:
            bad(
                "max_chars",
                f"is {max_chars}, above the length of the `{section}` section's master text ({master_len}"
                f" characters, entries joined by a space): the page fit was measured with that text. Use at most"
                f" {master_len}, or omit it.",
            )

    max_sentences = data.get("max_sentences", DEFAULT_SENTENCES)
    if not _is_int(max_sentences) or not 1 <= max_sentences <= MAX_SENTENCES:
        bad("max_sentences", f"must be an integer from 1 to {MAX_SENTENCES}, not {max_sentences!r}.")

    facts = data.get("facts", [])
    if not isinstance(facts, list):
        bad("facts", "must be a list of one-line strings (`facts:` then `  - 10 years of experience`).")
        facts = []
    for k, fact in enumerate(facts):
        if not _one_line_text(fact):
            errors.append(
                _error(
                    "guide_invalid",
                    f"guide.facts[{k}]",
                    f"Summary guide `{path}`: `facts[{k}]` is {fact!r}; each fact must be a non-empty one-line"
                    f" string (quote it if it is a number).",
                )
            )
    if errors:
        raise Failure(2, errors)
    return Guide(path, section, max_chars, max_sentences, tuple(facts), body)


def guide_check(guide_path: Path, master_path: Path) -> dict:
    """`cv-skills guide-check`: the ok JSON, or Failure."""
    from cv_skills.master import load_master

    m = load_master(Path(master_path))
    g = load_guide(Path(guide_path), m)
    return {
        "ok": True,
        "section": g.section,
        "max_chars": g.max_chars,
        "max_sentences": g.max_sentences,
        "facts": len(g.facts),
    }


# --- summary.json --------------------------------------------------------------


def load_summary(path: Path) -> list[Sentence]:
    """Read `summary.json`; bad JSON or shape is Failure(2) `summary_invalid` (every problem at once)."""
    path = Path(path)
    try:
        raw = json.loads(path.read_bytes().decode("utf-8"))
    except OSError as e:
        why = f"cannot be read: {e.strerror or e}"
    except UnicodeDecodeError as e:
        why = f"is not UTF-8: {e}"
    except json.JSONDecodeError as e:
        why = f"is not valid JSON: {e}"
    else:
        why = None
    if why is not None:
        raise Failure(2, [_error("summary_invalid", str(path), f"Summary file `{path}` {why}. {_SUMMARY_FIX}")])

    errors: list[dict] = []

    def bad(id: str, why: str) -> None:
        errors.append(_error("summary_invalid", id, f"Summary file `{path}`: `{id}` {why}. {_SUMMARY_FIX}"))

    if not isinstance(raw, dict):
        bad("(top level)", "must be an object")
        raise Failure(2, errors)
    for key in raw:
        if key != "sentences":
            bad(str(key), "is not a known key (allowed: sentences)")
    items = raw.get("sentences")
    if not isinstance(items, list):
        bad("sentences", "must be an array of {text, sources}")
        raise Failure(2, errors)
    out: list[Sentence] = []
    for i, item in enumerate(items):
        p = f"sentences[{i}]"
        if not isinstance(item, dict):
            bad(p, "must be an object {text, sources}")
            continue
        for key in item:
            if key not in ("text", "sources"):
                bad(f"{p}.{key}", "is not a known key (allowed: text, sources)")
        text, sources = item.get("text"), item.get("sources")
        ok = True
        if not isinstance(text, str) or (text.strip() and not _one_line_text(text)):
            bad(f"{p}.text", "must be one line of UTF-8 text (no line breaks or control characters)")
            ok = False
        if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
            bad(f"{p}.sources", "must be an array of ID strings")
            ok = False
        if ok:
            out.append(Sentence(text, tuple(sources)))
    if errors:
        raise Failure(2, errors)
    return out


def paragraph(sentences: list[Sentence]) -> str:
    """The written paragraph: the sentences' texts, trimmed, joined by " " (one plain-text entry)."""
    return " ".join(s.text.strip() for s in sentences)


# --- sources -------------------------------------------------------------------


def _master_entry(m: Master, eid: str) -> object:
    sid, i = eid.rsplit(".", 1)
    return m.data["cv"]["sections"][sid][int(i)]


def _field_strings(value: object) -> list[str]:
    if isinstance(value, list):
        return [s for v in value for s in _field_strings(v)]
    if isinstance(value, dict) or value is None:
        return []
    return [str(value)]


def source_texts(m: Master, sel: Selection, guide: Guide, texts: dict[str, str]) -> dict[str, str]:
    """Every citable ID and its tailored text, in selection order (facts last).

    Only valid for a selection that passed `check_structure` and `check_swaps`; `texts` from `apply_swaps`.
    """
    out: dict[str, str] = {}
    own = guide.section
    for s in sel.sections:
        if s.id == own:
            continue
        for e in s.entries:
            entry = _master_entry(m, e.id)
            if isinstance(entry, str):
                out[e.id] = texts.get(e.id, entry)
                continue
            chosen = e.position if e.position is not None else entry.get("position")
            fields = []
            for key, value in strip_extra_keys(entry).items():
                if key in _CHILD_FIELDS:
                    continue
                fields += [str(chosen)] if key == "position" else _field_strings(value)
            out[e.id] = " | ".join(fields)
            if isinstance(chosen, str):
                out[f"{e.id}.position"] = chosen
            for key in ("summary", "bullet"):
                if f"{e.id}.{key}" in m.nodes:
                    out[f"{e.id}.{key}"] = texts.get(f"{e.id}.{key}", m.text_of(f"{e.id}.{key}"))
            for cid in [*(e.highlights or []), *(e.details or [])]:
                out[cid] = texts.get(cid, m.text_of(cid))
    for k, fact in enumerate(guide.facts):
        out[f"guide.facts.{k}"] = fact
    return out


def _known(m: Master, guide: Guide, id: str) -> bool:
    """True if `id` names something citable in principle (a master item, entry, entry title or fact)."""
    node = m.nodes.get(id)
    if node is not None:
        return node.kind != "section"
    if id.endswith(".position"):
        eid = id.removesuffix(".position")
        node = m.nodes.get(eid)
        if node is not None and node.kind == "entry":
            entry = _master_entry(m, eid)
            return isinstance(entry, dict) and isinstance(entry.get("position"), str)
    return False


def _in_section(id: str, section: str) -> bool:
    return id == section or id.startswith(f"{section}.")


def tokens(s: str) -> list[str]:
    """Whole tokens of `s` after Markdown stripping, NFKC and dash/quote folding (dashes and `/` split)."""
    return _TOKEN.findall(normalise(s))


def _ordinary_opener(tok: str, folded: set[str]) -> bool:
    """A sentence's first token is exempt only if it is an ordinary word: no capital after its first letter, and
    either in the cited sources' text (any case) or one of a few common openers."""
    return not any(c.isupper() for c in tok[1:]) and (tok.lower() in folded or tok.lower() in OPENERS)


# The capitalised first-person pronoun is grammar, not a name: it carries no claim (curly quotes are folded first).
PRONOUNS = frozenset({"I", "I'm", "I've", "I'd", "I'll"})


def _unsupported(tok: str, first: bool, vocab: set[str], folded: set[str]) -> bool:
    """True if `tok` needs support its sources do not give. `folded` is `vocab` lowercased."""
    if tok in PRONOUNS:
        return False
    if tok.lower() in NUMBER_WORDS:
        return tok.lower() not in folded
    named = any(c.isupper() for c in tok) and not (first and _ordinary_opener(tok, folded))
    if named or any(c.isdigit() for c in tok):
        return not (tok in vocab or (tok.endswith("'s") and tok[:-2] in vocab))
    return False


def _markup_not_allowed(text: str) -> list[str]:
    """What `text` has of the markup a summary may not use (only `**bold**` passes through to RenderCV/Typst)."""
    found = []
    if _TYPST_CMD.search(text):
        found.append("`#` before a letter (a Typst command)")
    for ch, what in (("_", "`_`"), ("[", "`[`"), ("]", "`]`"), ("`", "a backtick")):
        if ch in text:
            found.append(what)
    if "*" in _BOLD.sub("", text):
        found.append("`*` outside a balanced `**bold**` pair")
    return found


def _markup_errors(m: Master, sentences: list[Sentence]) -> list[dict]:
    """WP14 on the paragraph as RenderCV prints it; blamed on a sentence when it alone has the occurrence."""
    keywords = bold_keywords(m.data)
    errors: list[dict] = []
    alone = Counter()
    for i, s in enumerate(sentences):
        found = Counter(swallowed(s.text.strip(), keywords))
        alone |= found
        for markup, lost in found.elements():
            errors.append(_markup_error(f"sentences[{i}]", markup, lost))
    for markup, lost in (Counter(swallowed(paragraph(sentences), keywords)) - alone).elements():
        errors.append(_markup_error("sentences", markup, lost))
    return errors


def _markup_error(id: str, markup: str, lost: str) -> dict:
    return _error(
        "markup_swallows_text",
        id,
        f"`{id}`: {markup_message(markup, lost)} Put a space before the character, move it inside the markup, or"
        f" drop the markup.",
    )


def check_summary(m: Master, sel: Selection, guide: Guide, sentences: list[Sentence]) -> list[dict]:
    """Every exit-1 problem of the written summary (does not raise; [] = it passes).

    `sel` must already pass `check_structure` and `check_swaps`.
    """
    texts, _ = apply_swaps(sel, m)
    citable = source_texts(m, sel, guide, texts)
    errors: list[dict] = []
    if not sentences:
        errors.append(
            _error(
                "summary_empty",
                "sentences",
                f"The summary has no sentences. Write 1 to {guide.max_sentences} sentences, each with its sources.",
            )
        )
    if len(sentences) > guide.max_sentences:
        errors.append(
            _error(
                "summary_too_many_sentences",
                "sentences",
                f"The summary has {len(sentences)} sentences; the guide allows at most {guide.max_sentences}."
                f" Merge or drop sentences.",
            )
        )
    for i, s in enumerate(sentences):
        p = f"sentences[{i}]"
        if not s.text.strip():
            errors.append(_error("summary_empty", p, f"`{p}` has no text. Write the sentence or remove it."))
            continue
        if not s.sources:
            errors.append(
                _error(
                    "summary_source_missing",
                    f"{p}.sources",
                    f"`{p}` cites no sources. List the IDs (from `cv-skills ids`, selected) or `guide.facts.<n>`"
                    f" its content comes from.",
                )
            )
        vocab: set[str] = set()
        cited: list[str] = []
        for k, src in enumerate(s.sources):
            sp = f"{p}.sources[{k}]"
            if src in citable:
                vocab.update(tokens(citable[src]))
                cited.append(src)
            elif _in_section(src, guide.section):
                errors.append(
                    _error(
                        "summary_source_not_selected",
                        sp,
                        f"`{sp}` is `{src}`, in the summary's own section `{guide.section}`, which the written"
                        f" summary replaces: it cannot be a source. Cite the selected item the claim comes from,"
                        f" or a guide fact.",
                    )
                )
            elif _known(m, guide, src):
                errors.append(
                    _error(
                        "summary_source_not_selected",
                        sp,
                        f"`{sp}` is `{src}`, which is not in the selection (it or its entry was dropped), so the"
                        f" summary cannot draw on it. Cite a selected item instead.",
                    )
                )
            else:
                errors.append(
                    _error(
                        "summary_source_unknown",
                        sp,
                        f"`{sp}` is `{src}`, which is not a citable ID: use a selected highlight, detail, summary,"
                        f" bullet, plain-text entry, entry or `<entry>.position` ID from `cv-skills ids`, or"
                        f" `guide.facts.<n>` (the guide has {len(guide.facts)} facts). Sections are not sources.",
                    )
                )
        markup = _markup_not_allowed(s.text)
        if markup:
            errors.append(
                _error(
                    "summary_markup_not_allowed",
                    p,
                    f"`{p}` has {', '.join(markup)}. The summary may use inline **bold** only: RenderCV passes"
                    f" other markup (italics, links, code, `_`, `#` commands) through to Typst. Remove it.",
                )
            )
        folded = {t.lower() for t in vocab}
        unsupported: list[str] = []
        for j, tok in enumerate(tokens(s.text)):
            if _unsupported(tok, j == 0, vocab, folded) and tok not in unsupported:
                unsupported.append(tok)
        for tok in unsupported:
            listed = ", ".join(f"`{c}`" for c in cited) or "(no valid sources)"
            errors.append(
                _error(
                    "summary_unsupported_token",
                    p,
                    f"`{p}`: `{tok}` is not in the text of its sources ({listed}). Every number (digits, or a"
                    f" number word such as \"twelve\" or \"dozens\") and every word with a capital letter must"
                    f" appear, as a whole word, in a cited source; a sentence's first word is exempt only if it is"
                    f" an ordinary word (in the sources in any case, or a common opener such as \"Data\" or"
                    f" \"Experienced\"). Cite the source it comes from, use its exact wording, or drop it (a figure"
                    f" computed from the CV, e.g. years from dates, needs a guide fact).",
                )
            )
    written = len(strip_markup(paragraph(sentences)))
    if written > guide.max_chars:
        errors.append(
            _error(
                "summary_too_long",
                "sentences",
                f"The summary is {written} characters (Markdown stripped, sentences joined by a space); the guide"
                f" allows {guide.max_chars}. Shorten it by {written - guide.max_chars}.",
            )
        )
    return errors + _markup_errors(m, sentences)


def section_selected_errors(sel: Selection, guide: Guide) -> list[dict]:
    """`summary_written_section_not_selected`: the written summary needs its section in the selection."""
    if any(s.id == guide.section for s in sel.sections):
        return []
    return [
        _error(
            "summary_written_section_not_selected",
            "guide.section",
            f"The guide's section `{guide.section}` is not in the selection, so the written summary has nowhere"
            f" to go. Select the section (any of its entries), or build without `--summary`.",
        )
    ]


def apply_summary(doc, guide: Guide, sentences: list[Sentence]) -> None:
    """Replace the guide's section in the assembled tailored `doc` with one plain-text entry: the paragraph."""
    doc["cv"]["sections"][guide.section] = CommentedSeq([paragraph(sentences)])


def summary_record(guide: Guide, sentences: list[Sentence]) -> dict:
    """The `summary` key of `build`'s result."""
    return {"section": guide.section, "sentences": [{"text": s.text, "sources": list(s.sources)} for s in sentences]}


def summary_check(master_path: Path, selection_path: Path, guide_path: Path, summary_path: Path) -> dict:
    """`cv-skills summary-check`: the ok JSON, or Failure (2: unusable input, 1: the summary fails)."""
    from cv_skills.build import read_selection
    from cv_skills.master import load_master
    from cv_skills.selection import check_structure
    from cv_skills.swaps import check_swaps

    m = load_master(Path(master_path))
    guide = load_guide(Path(guide_path), m)
    sentences = load_summary(Path(summary_path))
    sel = read_selection(Path(selection_path))
    errors = check_structure(sel, m) + check_swaps(sel, m)
    if errors:
        raise Failure(1, errors)
    errors = check_summary(m, sel, guide, sentences)
    if errors:
        raise Failure(1, errors)
    return {
        "ok": True,
        "section": guide.section,
        "sentences": len(sentences),
        "sources": sum(len(s.sources) for s in sentences),
        "chars": len(strip_markup(paragraph(sentences))),
        "max_chars": guide.max_chars,
    }


def load_written(summary_path: Path | None, guide_path: Path | None, m: Master) -> tuple[Guide, list[Sentence]] | None:
    """The guide and sentences of `--summary J --guide G` (both or neither, else `usage`); None without them."""
    if summary_path is None and guide_path is None:
        return None
    if summary_path is None or guide_path is None:
        given, missing = ("--summary", "--guide") if guide_path is None else ("--guide", "--summary")
        raise Failure(
            2,
            [_error("usage", "", f"`{given}` needs `{missing}` too: pass both (a written summary) or neither.")],
        )
    guide = load_guide(Path(guide_path), m)
    return guide, load_summary(Path(summary_path))


def written_errors(m: Master, sel: Selection, written: tuple[Guide, list[Sentence]]) -> list[dict]:
    """What `build` and `review-page` reject in a written summary: its section unselected, or the check's errors."""
    guide, sentences = written
    return section_selected_errors(sel, guide) + check_summary(m, sel, guide, sentences)
