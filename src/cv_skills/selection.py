"""`selection.json`: schema parsing and structural checks against a master.

Shape (list order = output order; anything not listed is dropped):
  {"master_hash": "sha256:…",
   "sections": [{"id": "<section>", "entries": [
       {"id": "<section>.<i>", "highlights": [...], "details": [...], "position": "…"}]}],
   "swaps": [{"target_id": "…", "from": "…", "to": "…"}]}

`highlights` (resp. `details`) is required on an entry whose master entry has
them (`[]` drops all) and rejected on one that has none. `swaps` is optional.
`position` is optional: the entry's master `position` or one of its
`title_variants`, exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from cv_skills.cli import Failure
from cv_skills.master import Master


@dataclass
class EntrySel:
    id: str
    highlights: list[str] | None = None
    details: list[str] | None = None
    position: str | None = None  # the chosen title; None = the master's


@dataclass
class SectionSel:
    id: str
    entries: list[EntrySel]


@dataclass
class Swap:
    target_id: str
    from_: str
    to: str


@dataclass
class Selection:
    master_hash: str
    sections: list[SectionSel]
    swaps: list[Swap] = field(default_factory=list)


def _error(code: str, id: str, message: str) -> dict:
    return {"code": code, "id": id, "message": message}


# --- schema ---------------------------------------------------------------

_TOP_KEYS = {"master_hash": True, "sections": True, "swaps": False}  # key -> required
_SECTION_KEYS = {"id": True, "entries": True}
_ENTRY_KEYS = {"id": True, "highlights": False, "details": False, "position": False}
_SWAP_KEYS = {"target_id": True, "from": True, "to": True}


class _Schema:
    def __init__(self) -> None:
        self.errors: list[dict] = []

    def fail(self, path: str, message: str) -> None:
        path, message = _printable(path), _printable(message)
        self.errors.append(_error("selection_schema", path, f"selection.json `{path}`: {message}"))

    def utf8(self, value: str, path: str) -> bool:
        """False (and an error) if `value` cannot be encoded as UTF-8 (e.g. a lone surrogate `\\ud800`)."""
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            self.fail(
                path,
                f"string {_printable(repr(value))} is not valid Unicode text (it contains a lone"
                f" surrogate escape such as `\\ud800`). Write plain UTF-8 text.",
            )
            return False
        return True

    def keys(self, obj: object, path: str, spec: dict[str, bool], what: str) -> bool:
        """Check `obj` is an object with exactly the allowed keys; False if not an object."""
        if not isinstance(obj, dict):
            self.fail(path or "(top level)", f"{what} must be a JSON object, got {_jtype(obj)}.")
            return False
        allowed = ", ".join(f"`{k}`" for k in spec)
        for key in obj:
            if key not in spec:
                self.fail(
                    _join(path, str(key)),
                    f"unknown key `{key}` in {what}. Allowed keys: {allowed}. Remove it.",
                )
        for key, required in spec.items():
            if required and key not in obj:
                self.fail(_join(path, key), f"{what} is missing the required key `{key}`. Add it.")
        return True

    def string(self, obj: dict, key: str, path: str) -> str | None:
        if key not in obj:
            return None
        value = obj[key]
        if not isinstance(value, str):
            self.fail(_join(path, key), f"`{key}` must be a string, got {_jtype(value)}.")
            return None
        if not self.utf8(value, _join(path, key)):
            return None
        return value

    def string_list(self, obj: dict, key: str, path: str) -> list[str] | None:
        if key not in obj:
            return None
        value = obj[key]
        p = _join(path, key)
        if not isinstance(value, list):
            self.fail(p, f"`{key}` must be a list of ID strings, got {_jtype(value)}.")
            return None
        out = []
        for i, item in enumerate(value):
            if isinstance(item, str):
                if self.utf8(item, f"{p}[{i}]"):
                    out.append(item)
            else:
                self.fail(f"{p}[{i}]", f"`{key}` items must be ID strings, got {_jtype(item)}.")
        return out

    def list_of(self, obj: dict, key: str, path: str) -> list | None:
        if key not in obj:
            return None
        value = obj[key]
        if not isinstance(value, list):
            self.fail(_join(path, key), f"`{key}` must be a list, got {_jtype(value)}.")
            return None
        return value


def _printable(s: str) -> str:
    """`s` with anything UTF-8 cannot encode escaped, so error JSON stays emittable."""
    return s.encode("utf-8", "backslashreplace").decode("utf-8")


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _jtype(v: object) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "a boolean"
    if isinstance(v, (int, float)):
        return "a number"
    if isinstance(v, str):
        return "a string"
    if isinstance(v, list):
        return "a list"
    if isinstance(v, dict):
        return "an object"
    return type(v).__name__


def parse_selection(raw: object) -> Selection:
    """Validate the JSON shape of a selection; raises Failure(1, selection_schema errors)."""
    s = _Schema()
    if not s.keys(raw, "", _TOP_KEYS, "the selection"):
        raise Failure(1, s.errors)
    assert isinstance(raw, dict)

    master_hash = s.string(raw, "master_hash", "")

    sections: list[SectionSel] = []
    for i, sraw in enumerate(s.list_of(raw, "sections", "") or []):
        sp = f"sections[{i}]"
        if not s.keys(sraw, sp, _SECTION_KEYS, "a section"):
            continue
        entries: list[EntrySel] = []
        for j, eraw in enumerate(s.list_of(sraw, "entries", sp) or []):
            ep = f"{sp}.entries[{j}]"
            if not s.keys(eraw, ep, _ENTRY_KEYS, "an entry"):
                continue
            entries.append(
                EntrySel(
                    id=s.string(eraw, "id", ep) or "",
                    highlights=s.string_list(eraw, "highlights", ep),
                    details=s.string_list(eraw, "details", ep),
                    position=s.string(eraw, "position", ep),
                )
            )
        sections.append(SectionSel(id=s.string(sraw, "id", sp) or "", entries=entries))

    swaps: list[Swap] = []
    for i, wraw in enumerate(s.list_of(raw, "swaps", "") or []):
        wp = f"swaps[{i}]"
        if not s.keys(wraw, wp, _SWAP_KEYS, "a swap"):
            continue
        swaps.append(
            Swap(
                target_id=s.string(wraw, "target_id", wp) or "",
                from_=s.string(wraw, "from", wp) or "",
                to=s.string(wraw, "to", wp) or "",
            )
        )

    if s.errors:
        raise Failure(1, s.errors)
    return Selection(master_hash=master_hash or "", sections=sections, swaps=swaps)


# --- structure ------------------------------------------------------------

_NEVER_SELECTED = {"summary": "summary", "bullet": "bullet text", "position": "job title"}


def _position_errors(m: Master, eid: str, chosen: str | None, path: str) -> list[dict]:
    """Errors for an entry's `position`: it must be the master `position` or one of its `title_variants`."""
    if chosen is None:
        return []
    sid, index = eid.rsplit(".", 1)
    entry = m.data["cv"]["sections"][sid][int(index)]
    default = entry.get("position") if isinstance(entry, dict) else None
    if not isinstance(default, str):
        return [
            _error(
                "not_selectable",
                eid,
                f"Entry `{eid}` has no `position` in the master, so its selection must not carry a"
                f" `position` key. Remove `position` from entry `{eid}`.",
            )
        ]
    node = m.nodes.get(f"{eid}.position")
    allowed = [default, *(node.variants if node else ())]
    if chosen in allowed:
        return []
    listed = ", ".join(f"`{t}`" for t in allowed)
    return [
        _error(
            "position_not_allowed",
            path,
            f"`{path}`: {_printable(json.dumps(chosen, ensure_ascii=False))} is not a title entry `{eid}` may use."
            f" Allowed (exactly, case and spaces included): {listed}. Pick one of them, or remove `position` to"
            f" keep `{default}`. Other titles must be added to the entry's `title_variants` in the master.",
        )
    ]


def _child_errors(
    m: Master,
    eid: str,
    key: str,
    kind: str,
    chosen: list[str] | None,
    seen: set[str],
) -> list[dict]:
    """Errors for an entry's `highlights`/`details` list (`key`), children of `kind`."""
    errors: list[dict] = []
    available = m.children(eid, kind)
    example = f"{eid}.{key}.0"
    if chosen is None:
        if available:
            errors.append(
                _error(
                    f"{key}_missing",
                    eid,
                    f"Entry `{eid}` has {key} in the master but its selection has no `{key}` key."
                    f" List the {key} to keep in output order (e.g. [\"{example}\"]),"
                    f" or write `\"{key}\": []` to drop them all deliberately.",
                )
            )
        return errors
    if not available:
        errors.append(
            _error(
                "not_selectable",
                eid,
                f"Entry `{eid}` has no {key} in the master, so its selection must not carry a"
                f" `{key}` key. Remove `{key}` from entry `{eid}`.",
            )
        )
        return errors
    for cid in chosen:
        errors.extend(_id_errors(m, cid, kind, eid, f"`{key}` of entry `{eid}`", seen))
    return errors


def _id_errors(m: Master, id: str, kind: str, parent: str | None, where: str, seen: set[str]) -> list[dict]:
    """Errors for one listed ID that should be a `kind` node under `parent`."""
    node = m.nodes.get(id)
    if node is None:
        return [
            _error(
                "unknown_id",
                id,
                f"`{id}` (in {where}) is not an ID in the master. Use an ID from"
                f" `cv-skills ids` for this master, or remove it.",
            )
        ]
    if node.kind in _NEVER_SELECTED:
        return [
            _error(
                "not_selectable",
                id,
                f"`{id}` (in {where}) is the {_NEVER_SELECTED[node.kind]} of entry `{node.parent}`;"
                f" it is kept automatically with its entry and cannot be selected. Remove it;"
                f" select entry `{node.parent}` instead."
                + (
                    f" To use one of its title variants, set that entry's `\"position\"` to it."
                    if node.kind == "position"
                    else ""
                ),
            )
        ]
    if node.kind != kind or node.parent != parent:
        home = (
            f"inside entry `{node.parent}`"
            if node.kind in ("highlight", "detail")
            else f"in the `entries` of section `{node.parent}`"
            if node.kind == "entry"
            else "in the top-level `sections` list"
        )
        return [
            _error(
                "wrong_parent",
                id,
                f"`{id}` is a {node.kind} and belongs {home}, but it is listed in {where}."
                f" Move it there or remove it.",
            )
        ]
    if id in seen:
        return [
            _error(
                "duplicate_id",
                id,
                f"`{id}` is listed more than once (again in {where}). Keep only one occurrence.",
            )
        ]
    seen.add(id)
    return []


def check_structure(sel: Selection, m: Master) -> list[dict]:
    """All structural errors of `sel` against `m` (does not raise; [] = valid)."""
    errors: list[dict] = []
    if sel.master_hash != m.hash:
        errors.append(
            _error(
                "hash_mismatch",
                "master_hash",
                f"`master_hash` is {sel.master_hash!r} but the master's hash is {m.hash!r}:"
                f" the selection was drafted against a different master version. Re-run"
                f" `cv-skills ids` on the current master and redo the selection with its IDs and hash.",
            )
        )

    seen: set[str] = set()
    selected_entries: set[str] = set()
    for i, s in enumerate(sel.sections):
        sec_errors = _id_errors(m, s.id, "section", None, "the top-level `sections` list", seen)
        errors.extend(sec_errors)
        if sec_errors:
            continue
        if not s.entries:
            errors.append(
                _error(
                    "empty_section",
                    s.id,
                    f"Section `{s.id}` is listed with no entries. Select at least one entry,"
                    f" or remove section `{s.id}` from `sections` to drop it.",
                )
            )
        for j, e in enumerate(s.entries):
            e_errors = _id_errors(m, e.id, "entry", s.id, f"the `entries` of section `{s.id}`", seen)
            errors.extend(e_errors)
            if e_errors:
                continue
            selected_entries.add(e.id)
            errors.extend(_child_errors(m, e.id, "highlights", "highlight", e.highlights, seen))
            errors.extend(_child_errors(m, e.id, "details", "detail", e.details, seen))
            errors.extend(_position_errors(m, e.id, e.position, f"sections[{i}].entries[{j}].position"))

    listed_sections = {s.id for s in sel.sections}
    for node in m.nodes.values():
        if node.kind == "entry" and node.pin and node.id not in selected_entries:
            why = (
                f"its section `{node.parent}` is not in `sections`"
                if node.parent not in listed_sections
                else f"it is not in the `entries` of section `{node.parent}`"
            )
            errors.append(
                _error(
                    "pin_missing",
                    node.id,
                    f"Entry `{node.id}` ({node.text}) is pinned (`pin: yes`) and must be in every"
                    f" tailored CV, but {why}. Add {{\"id\": \"{node.id}\", …}} to section"
                    f" `{node.parent}` (its highlights/details may still be chosen freely).",
                )
            )
    return errors


def selected_ids(sel: Selection) -> set[str]:
    """Every selected ID, incl. the implied `summary`/`bullet` of each selected entry."""
    ids: set[str] = set()
    for s in sel.sections:
        ids.add(s.id)
        for e in s.entries:
            ids.update({e.id, f"{e.id}.summary", f"{e.id}.bullet"})
            ids.update(e.highlights or [])
            ids.update(e.details or [])
    return ids
