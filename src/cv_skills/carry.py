"""Carry a selection onto a changed master: the `tailor` workflow's `from_selection` (a fresh run that starts
from an earlier run's reviewed selection instead of a new draft).

IDs are positions (`experience.0.highlights.2`), so a master edit can shift them. Items are matched by their
master text instead: a selected item is carried to the item of the new master with the same text, in the matched
parent. An item whose text is gone is carried only when it was edited in place (the lists align around it, and
exactly as many new texts stand where the old ones were) and is then reported as `changed`; anything else is
`dropped` and reported. The
result is an ordinary selection of the new master: `build` checks it like any draft, so the CV stays master text
plus listed swaps whatever this module does.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from collections import defaultdict, deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from cv_skills.cli import Failure
from cv_skills.master import Master, _sections, load_master, split_variants
from cv_skills.selection import Selection, check_structure
from cv_skills.summary import Sentence
from cv_skills.swaps import check_swaps

SNAPSHOT = "master.snapshot.yaml"  # the master a run was built from, in its application folder
_CHILD_KINDS = ("highlight", "detail")
_ENTRY_SUFFIXES = ("summary", "bullet", "position")  # IDs that follow their entry


def _error(code: str, id: str, message: str) -> dict:
    return {"code": code, "id": id, "message": message}


@dataclass
class Carried:
    selection: dict  # selection.json for the new master
    summary: dict | None  # summary.json with its sources remapped, or None (not carried)
    report: dict  # carry.json: what changed, what was dropped, what is new in the master


def _pair(old: list[str], new: list[str]) -> tuple[dict[int, int], set[int]]:
    """Old index -> new index, by equal text (the k-th copy to the k-th copy, wherever it moved). Then, where the
    two lists align around a block that was replaced (difflib), the unmatched old items of that block pair in
    order with its unmatched new ones, but only when there are as many of each: those were edited in place.
    Returns the pairs and the old indices edited in place."""
    free: dict[str, deque[int]] = defaultdict(deque)
    for j, text in enumerate(new):
        free[text].append(j)
    pairs = {i: free[text].popleft() for i, text in enumerate(old) if free[text]}
    taken = set(pairs.values())
    edited = set()
    for op, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if op != "replace":
            continue
        olds = [i for i in range(i1, i2) if i not in pairs]
        news = [j for j in range(j1, j2) if j not in taken]
        if olds and len(olds) == len(news):
            for i, j in zip(olds, news):
                pairs[i] = j
                taken.add(j)
                edited.add(i)
    return pairs, edited


class _Map:
    """Old ID -> new ID for every section, entry, highlight and skills item, and the IDs edited in place."""

    def __init__(self, old: Master, new: Master) -> None:
        self.old, self.new = old, new
        self.ids: dict[str, str] = {}
        self.edited: set[str] = set()
        for sid, node in old.nodes.items():
            if node.kind == "section" and sid in new.nodes and new.nodes[sid].kind == "section":
                self.ids[sid] = sid
                self._children(sid, sid, "entry")
        for oe, ne in list(self.ids.items()):
            if old.nodes[oe].kind == "entry":
                for kind in _CHILD_KINDS:
                    self._children(oe, ne, kind)

    def _children(self, old_parent: str, new_parent: str, kind: str) -> None:
        olds, news = self.old.children(old_parent, kind), self.new.children(new_parent, kind)
        pairs, edited = _pair([self.old.nodes[i].text or "" for i in olds], [self.new.nodes[j].text or "" for j in news])
        for i, j in pairs.items():
            self.ids[olds[i]] = news[j]
            if i in edited:
                self.edited.add(olds[i])

    def target(self, id: str) -> str | None:
        """The new ID of any old ID, including an entry's `.summary`, `.bullet` and `.position`."""
        if id in self.ids:
            return self.ids[id]
        head, _, tail = id.rpartition(".")
        if tail in _ENTRY_SUFFIXES and head in self.ids:
            return f"{self.ids[head]}.{tail}"
        return None

    def new_items(self) -> list[dict]:
        """Selectable items of the new master with no counterpart in the old one (a new entry, not its children)."""
        image = set(self.ids.values())
        out = []
        for id, node in self.new.nodes.items():
            if node.kind not in ("section", "entry", *_CHILD_KINDS) or id in image:
                continue
            if node.parent is not None and node.parent not in image:
                continue  # inside a new section or entry, which is listed itself
            item = {"id": id}
            if node.text is not None:
                item["text"] = node.text
            out.append(item)
        return out


def _master_entry(m: Master, eid: str) -> object:
    section, _, i = eid.rpartition(".")
    return _sections(m.data)[section][int(i)]


def _titles(m: Master, eid: str) -> list[str]:
    """The titles an entry may show: its `position`, then its `title_variants`."""
    e = _master_entry(m, eid)
    if not isinstance(e, dict) or not e.get("position"):
        return []
    variants = e.get("title_variants")
    return [str(e["position"]), *(split_variants(variants) if isinstance(variants, str) else [])]


def carry_selection(old: Master, new: Master, sel: Selection, sentences: list[Sentence] | None = None) -> Carried:
    """`sel`, a valid selection of `old`, as a selection of `new`. Raises Failure(1) if `sel` is not one."""
    if sel.master_hash != old.hash:
        raise Failure(
            1,
            [
                _error(
                    "hash_mismatch",
                    "master_hash",
                    f"The selection is for master {sel.master_hash}, not {old.hash}: it can only be carried from the"
                    " master it was made for.",
                )
            ],
        )
    if errors := check_structure(sel, old) + check_swaps(sel, old):
        raise Failure(1, errors)
    ids = _Map(old, new)
    changed: list[dict] = []
    dropped: list[dict] = []
    titles_reset: list[dict] = []

    def carry(id: str, why: str) -> str | None:
        nid = ids.ids.get(id)
        if nid is None:
            dropped.append({"old_id": id, "was": old.nodes[id].text or id, "why": why})
        elif id in ids.edited:
            changed.append({"old_id": id, "id": nid, "was": old.nodes[id].text, "now": new.nodes[nid].text})
        return nid

    sections = []
    for s in sel.sections:
        if s.id not in ids.ids:
            for e in s.entries:
                dropped.append({"old_id": e.id, "was": old.nodes[e.id].text or e.id, "why": f"section `{s.id}` is gone"})
            continue
        entries = []
        for e in s.entries:
            ne = carry(e.id, "not in the new master (removed, or changed and moved)")
            if ne is None:
                continue
            out: dict = {"id": ne}
            for key, kind, chosen in (("highlights", "highlight", e.highlights), ("details", "detail", e.details)):
                if new.children(ne, kind):
                    kept = [carry(c, "not in the new master (removed, or changed and moved)") for c in chosen or []]
                    out[key] = [c for c in kept if c is not None]
                else:
                    for c in chosen or []:
                        carry(c, "not in the new master (removed, or changed and moved)")
            if e.position is not None:
                allowed = _titles(new, ne)
                if e.position in allowed:
                    if e.position != allowed[0]:
                        out["position"] = e.position
                else:
                    titles_reset.append({"entry": ne, "was": e.position, "now": allowed[0] if allowed else ""})
            entries.append(out)
        if entries:
            sections.append({"id": s.id, "entries": entries})

    selected = {e["id"] for s in sections for e in s["entries"]}
    selected |= {c for s in sections for e in s["entries"] for k in ("highlights", "details") for c in e.get(k, [])}
    swaps, swaps_dropped = [], []
    for n, sw in enumerate(sel.swaps, start=1):
        record = {"index": n, "target_id": sw.target_id, "from": sw.from_, "to": sw.to}
        target = ids.target(sw.target_id)
        owner = target.rpartition(".")[0] if target and target.rpartition(".")[2] in _ENTRY_SUFFIXES else target
        if target is None or target not in new.nodes or owner not in selected:
            swaps_dropped.append(record | {"why": f"its target `{sw.target_id}` was not carried"})
        elif new.text_of(target).count(sw.from_) != 1:
            swaps_dropped.append(
                record | {"why": f"\"{sw.from_}\" no longer occurs exactly once in `{target}`'s master text"}
            )
        else:
            swaps.append({"target_id": target, "from": sw.from_, "to": sw.to})

    summary, summary_note = None, "none"
    if sentences is not None:
        lost = [src for st in sentences for src in st.sources if not src.startswith("guide.") and ids.target(src) is None]
        if lost:
            summary_note = f"not carried: it cites {', '.join(dict.fromkeys(lost))}, not in the new master"
        else:
            summary = {
                "sentences": [
                    {"text": st.text, "sources": [src if src.startswith("guide.") else ids.target(src) for src in st.sources]}
                    for st in sentences
                ]
            }
            stale = [src for st in sentences for src in st.sources if src in ids.edited]
            summary_note = "carried" + (f"; it cites changed items: {', '.join(dict.fromkeys(stale))}" if stale else "")

    selection = {"master_hash": new.hash, "sections": sections}
    if swaps:
        selection["swaps"] = swaps
    report = {
        "old_master_hash": old.hash,
        "master_hash": new.hash,
        "kept": len(selected) - len(changed),
        "changed": changed,
        "dropped": dropped,
        "swaps_dropped": swaps_dropped,
        "titles_reset": titles_reset,
        "new": ids.new_items(),
        "summary": summary_note,
    }
    return Carried(selection, summary, report)


def _git_versions(master: Path):
    """Every committed version of `master` (newest first), as bytes; nothing outside a git repo."""
    cwd, name = master.resolve().parent, master.name
    try:
        log = subprocess.run(
            ["git", "log", "--all", "--format=%H", "--", name], cwd=cwd, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return
    for rev in log.stdout.split() if log.returncode == 0 else []:
        show = subprocess.run(["git", "show", f"{rev}:./{name}"], cwd=cwd, capture_output=True, timeout=30)
        if show.returncode == 0:
            yield show.stdout


def old_master(folder: Path, master: Path, want_hash: str) -> Master:
    """The master a selection in `folder` was made for (`want_hash`): the folder's snapshot, else a committed
    version of `master` with that hash. Raises Failure(1) `carry_master_not_found` when neither has it."""
    want = want_hash.removeprefix("sha256:")
    snapshot = Path(folder) / SNAPSHOT
    candidates = [snapshot.read_bytes()] if snapshot.is_file() else []
    for raw in [*candidates, *_git_versions(Path(master))]:
        if hashlib.sha256(raw).hexdigest() == want:
            with tempfile.TemporaryDirectory(prefix="cv-skills-") as work:
                path = Path(work) / Path(master).name
                path.write_bytes(raw)
                m = load_master(path)
            return m
    raise Failure(
        1,
        [
            _error(
                "carry_master_not_found",
                str(folder),
                f"No copy of the master this selection was made for ({want_hash}): `{snapshot}` is missing or"
                f" differs, and no committed version of `{master}` has that hash. Save that master as `{snapshot}`"
                f" (e.g. `git show <commit>:{Path(master).name} > {snapshot}`), then start the run again.",
            )
        ],
    )
