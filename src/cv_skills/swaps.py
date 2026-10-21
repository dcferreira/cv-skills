"""Term swaps: validation against the master and application with offsets.

Each swap's `from` is matched against the MASTER text of its target (exact,
case-sensitive, raw YAML string incl. markdown) and must occur exactly once.
Several non-overlapping swaps on one target apply together. Offsets are in
characters (Python str indices) of the target's tailored text.
"""

from __future__ import annotations

import unicodedata

from cv_skills.master import Master
from cv_skills.selection import Selection, Swap, selected_ids

ALLOWED = (
    "highlight (`<section>.<i>.highlights.<j>`), summary (`<section>.<i>.summary`),"
    " detail (`<section>.<i>.details.<k>`, one skills item), text entry (`<section>.<i>`"
    " of a plain-string entry), bullet (`<section>.<i>.bullet`)"
)


def _error(code: str, id: str, message: str) -> dict:
    return {"code": code, "id": id, "message": message}


def _occurrences(text: str, sub: str) -> list[int]:
    """Start offsets of every (possibly overlapping) occurrence of `sub` in `text`."""
    out, i = [], text.find(sub)
    while i != -1:
        out.append(i)
        i = text.find(sub, i + 1)
    return out


def _is_field(m: Master, id: str) -> bool:
    """True if `id` names a non-swappable master field (e.g. `experience.0.position`, `name`)."""
    cv = m.data.get("cv") or {}
    if id in cv and id != "sections":
        return True
    parts = id.split(".")
    if len(parts) != 3:
        return False
    eid = f"{parts[0]}.{parts[1]}"
    node = m.nodes.get(eid)
    if node is None or node.kind != "entry":
        return False
    entry = cv["sections"][parts[0]][int(parts[1])]
    return isinstance(entry, dict) and parts[2] in entry


def _forbidden(i: int, id: str, what: str) -> dict:
    return _error(
        "swap_forbidden_field",
        id,
        f"swaps[{i}]: `{id}` is {what}, which cannot be swapped. Swappable kinds: {ALLOWED}."
        f" Retarget the swap to one of those IDs (see `cv-skills ids`) or remove it.",
    )


def _target_errors(i: int, s: Swap, m: Master, selected: set[str]) -> list[dict]:
    """Target errors; [] means the target is a known, swappable, selected node."""
    id = s.target_id
    node = m.nodes.get(id)
    if node is None:
        if _is_field(m, id):
            return [_forbidden(i, id, "a fixed field")]
        return [
            _error(
                "swap_target_unknown",
                id,
                f"swaps[{i}]: target `{id}` is not an ID in the master. Use an ID from"
                f" `cv-skills ids` for this master, or remove the swap.",
            )
        ]
    if not node.swappable:
        what = "a section" if node.kind == "section" else f"a structured {node.kind} (not a plain-string text entry)"
        if node.kind == "position":
            # Only a title listed in the master may be printed: chosen with the entry's `position`, never swapped.
            what = (
                f"a job title (to use one of its `variants`, set entry `{node.parent}`'s"
                f" `\"position\"` in the selection instead)"
            )
        return [_forbidden(i, id, what)]
    if id not in selected:
        return [
            _error(
                "swap_target_not_selected",
                id,
                f"swaps[{i}]: target `{id}` is not in the selection (it or its entry was dropped)."
                + (
                    # A summary/bullet is not selectable itself: it comes with its entry.
                    f" Select entry `{node.parent}` (its {node.kind} is kept with it) or remove the swap."
                    if node.kind in ("summary", "bullet")
                    else f" Select `{id}` (and its entry) or remove the swap."
                ),
            )
        ]
    return []


def check_swaps(sel: Selection, m: Master) -> list[dict]:
    """All swap errors of `sel` against `m` (does not raise; [] = valid)."""
    errors: list[dict] = []
    selected = selected_ids(sel)
    spans: dict[str, list[tuple[int, int, int]]] = {}  # target -> (start, end, index)

    for i, s in enumerate(sel.swaps):
        id = s.target_id
        t_errors = _target_errors(i, s, m, selected)
        errors.extend(t_errors)
        if not s.from_.strip():
            errors.append(
                _error(
                    "swap_from_empty",
                    id,
                    f"swaps[{i}] on `{id}`: `from` is empty or whitespace-only. Give the exact master text to replace.",
                )
            )
        if not s.to.strip():
            errors.append(
                _error(
                    "swap_to_empty",
                    id,
                    f"swaps[{i}] on `{id}`: `to` is empty or whitespace-only. Give the replacement text; to drop"
                    f" content, deselect the item instead of swapping it to nothing.",
                )
            )
        node = m.nodes.get(id)
        # A swap may change what an item says, not the CV's shape: one bullet
        # stays one bullet (no line breaks), one skills item stays one item.
        bad = sorted({f"U+{ord(ch):04X}" for ch in s.to if unicodedata.category(ch) == "Cc"})
        if bad:
            errors.append(
                _error(
                    "swap_to_multiline",
                    id,
                    f"swaps[{i}] on `{id}`: `to` contains a line break or control character"
                    f" ({', '.join(bad)}). A swap must stay on one line: it may change what an item"
                    f" says, not split it or add structure. Remove newlines, tabs and other control"
                    f" characters from `to`.",
                )
            )
        if node is not None and node.kind == "detail" and "," in s.to:
            errors.append(
                _error(
                    "swap_to_comma_in_detail",
                    id,
                    f"swaps[{i}] on `{id}`: `to` contains a comma, but a skills-item swap must stay"
                    f" one item (items are comma-separated). No commas: reword `to` without them,"
                    f" e.g. with \"and\".",
                )
            )
        if not s.from_.strip() or node is None or not node.swappable:
            continue
        text = m.text_of(id)
        found = _occurrences(text, s.from_)
        if not found:
            errors.append(
                _error(
                    "swap_from_not_found",
                    id,
                    f"swaps[{i}] on `{id}`: `from` {s.from_!r} does not occur in the master text"
                    f" {text!r}. Matching is exact and case-sensitive on the raw master text"
                    f" (incl. markdown such as `**`); copy `from` from that text.",
                )
            )
        elif len(found) > 1:
            errors.append(
                _error(
                    "swap_from_ambiguous",
                    id,
                    f"swaps[{i}] on `{id}`: `from` {s.from_!r} occurs {len(found)} times in the"
                    f" master text {text!r}. Include more surrounding words in `from` (and `to`)"
                    f" so it matches exactly once.",
                )
            )
        else:
            spans.setdefault(id, []).append((found[0], found[0] + len(s.from_), i))

    for id, target_spans in spans.items():
        ordered = sorted(target_spans)
        for (a_start, a_end, a_i), (b_start, b_end, b_i) in zip(ordered, ordered[1:]):
            if b_start < a_end:
                errors.append(
                    _error(
                        "swap_overlap",
                        id,
                        f"swaps[{a_i}] and swaps[{b_i}] on `{id}` replace overlapping master text"
                        f" ({sel.swaps[a_i].from_!r} and {sel.swaps[b_i].from_!r}). Merge them into"
                        f" one swap or make their `from` texts not overlap.",
                    )
                )
    return errors


def apply_swaps(sel: Selection, m: Master) -> tuple[dict[str, str], list[dict]]:
    """Tailored text per swapped target and one record per swap (in `sel.swaps` order).

    Only valid when `check_swaps(sel, m)` returned no errors.
    """
    by_target: dict[str, list[tuple[int, int]]] = {}  # target -> (master start, index)
    for i, s in enumerate(sel.swaps):
        by_target.setdefault(s.target_id, []).append((m.text_of(s.target_id).index(s.from_), i))

    texts: dict[str, str] = {}
    records: dict[int, dict] = {}
    for target, items in by_target.items():
        master_text = m.text_of(target)
        out: list[str] = []
        pos = 0  # position in master text
        length = 0  # length of tailored text built so far
        for start, i in sorted(items):
            s = sel.swaps[i]
            kept = master_text[pos:start]
            out.append(kept)
            length += len(kept)
            out.append(s.to)
            records[i] = {
                "index": i,
                "target_id": target,
                "from": s.from_,
                "to": s.to,
                "start": length,
                "end": length + len(s.to),
            }
            length += len(s.to)
            pos = start + len(s.from_)
        out.append(master_text[pos:])
        texts[target] = "".join(out)
    return texts, [records[i] for i in sorted(records)]
