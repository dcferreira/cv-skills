"""`cv-skills build`: master + selection -> tailored RenderCV YAML + swap positions.

The output contains only master text and the listed swaps: every string in the
tailored `cv.sections` is a master string (or a master skills item) at a
selected ID, with exactly the swaps that target it applied, and each entry's
`position` is its master title or the one of its `title_variants` the selection
chose. `pin`/`tags`/`title_variants` are stripped; non-section `cv` fields,
`design`, `locale` and `settings` are copied verbatim. Nothing is written unless
the whole build succeeds.
"""

from __future__ import annotations

import io
import json
import operator
import os
import tempfile
from collections import Counter
from functools import reduce
from pathlib import Path

import ruamel.yaml
from rendercv.exception import RenderCVUserError, RenderCVUserValidationError
from rendercv.renderer.templater.entry_templates_from_input import process_summary
from rendercv.schema.rendercv_model_builder import build_rendercv_dictionary_and_model
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from cv_skills.cli import Failure
from cv_skills.markupcheck import swallowed
from cv_skills.master import Master, bold_keywords, entry_label, load_master, markup_message, strip_extra_keys
from cv_skills.selection import Selection, check_structure, parse_selection
from cv_skills.swaps import apply_swaps, check_swaps


def _error(code: str, id: str, message: str) -> dict:
    return {"code": code, "id": id, "message": message}


def read_selection(path: Path) -> Selection:
    """Read and parse `selection.json`; unreadable/unparsable -> Failure(2), schema -> Failure(1)."""
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
        return parse_selection(raw)
    raise Failure(
        2,
        [_error("selection_invalid", str(path), f"Selection file `{path}` {why}. Write it as UTF-8 JSON.")],
    )


def _entry(eid: str, master_entry: object, e, texts: dict[str, str], m: Master) -> object:
    """One tailored entry: master entry minus extra keys, children selected, swaps applied, title chosen."""
    if isinstance(master_entry, str):
        return texts.get(eid, master_entry)
    out = strip_extra_keys(master_entry)
    if e.position is not None:
        out["position"] = e.position  # checked against the master's titles (keeps its slot)
    for key in ("summary", "bullet"):
        if f"{eid}.{key}" in m.nodes:
            out[key] = texts.get(f"{eid}.{key}", m.text_of(f"{eid}.{key}"))
    if e.highlights is not None:
        if e.highlights:
            out["highlights"] = CommentedSeq(texts.get(h, m.text_of(h)) for h in e.highlights)
        else:
            del out["highlights"]
    if e.details is not None:
        out["details"] = _details(e, texts, m)
    return out


def title_changes(sel: Selection, m: Master) -> list[dict]:
    """One `{"entry", "default", "chosen"}` per selected entry whose chosen title is not its master `position`."""
    master_sections = m.data["cv"].get("sections") or {}
    out = []
    for s in sel.sections:
        for e in s.entries:
            if e.position is None:
                continue  # the default; `check_structure` rejects `position` on an entry without one
            default = master_sections[s.id][int(e.id.rsplit(".", 1)[1])]["position"]
            if e.position != default:
                out.append({"entry": e.id, "default": default, "chosen": e.position})
    return out


def _details(e, texts: dict[str, str], m: Master) -> str:
    """The skills line of a selected entry: its selected items (swaps applied) joined as in the master."""
    return ", ".join(texts.get(d, m.text_of(d)) for d in e.details)


def _selection_markup_errors(sel: Selection, m: Master, texts: dict[str, str]) -> list[dict]:
    """`selection_markup_swallows_text`: a skills line whose kept items pair markup up anew
    (e.g. `*x, y*z, w*(q)` without `*x`) so that RenderCV loses text after it."""
    keywords = bold_keywords(m.data)
    master_sections = m.data["cv"].get("sections") or {}
    errors: list[dict] = []
    for i, s in enumerate(sel.sections):
        for j, e in enumerate(s.entries):
            if not e.details:
                continue
            label = entry_label(master_sections[s.id][int(e.id.rsplit(".", 1)[1])])
            for markup, lost in swallowed(_details(e, texts, m), keywords):
                errors.append(
                    _error(
                        "selection_markup_swallows_text",
                        f"sections[{i}].entries[{j}].details",
                        f"`{e.id}` ({label}) skills line as selected: {markup_message(markup, lost)} The markup"
                        f" pairs up across the items kept: select its items differently, or fix the markup in the"
                        f" master.",
                    )
                )
    return errors


def _swap_markup_errors(sel: Selection, m: Master, texts: dict[str, str]) -> list[dict]:
    """`swap_markup_swallows_text`: a swap that makes RenderCV lose text after inline markup.

    An occurrence counts only if the master text of its target lacks it. It is blamed on
    each swap that creates it on its own; what only their combination creates, on every
    swap of the target.
    """
    keywords = bold_keywords(m.data)
    by_target: dict[str, list[int]] = {}
    for i, s in enumerate(sel.swaps):
        by_target.setdefault(s.target_id, []).append(i)

    def created(target: str, text: str) -> Counter[tuple[str, str]]:
        def found(s: str) -> Counter[tuple[str, str]]:
            # A summary is rendered as one `!!! summary` block (as in the master check).
            return Counter(swallowed(process_summary(s) if target.endswith(".summary") else s, keywords))

        return found(text) - found(m.text_of(target))

    blamed: list[tuple[int, str, list[tuple[str, str]]]] = []
    for target, indices in by_target.items():
        master = m.text_of(target)
        together = created(target, texts[target])
        if not together:
            continue
        alone = {i: created(target, master.replace(sel.swaps[i].from_, sel.swaps[i].to, 1)) & together for i in indices}
        blamed += [(i, target, list(c.elements())) for i, c in alone.items() if c]
        only_combined = together - reduce(operator.or_, alone.values(), Counter())
        if only_combined:
            blamed += [(i, target, list(only_combined.elements())) for i in indices]
    return [
        _error(
            "swap_markup_swallows_text",
            f"swaps[{i}]",
            f"swaps[{i}] on `{target}`: {markup_message(markup, lost)} Change the swap so the punctuation"
            f" is not right after the markup, or drop it.",
        )
        for i, target, found in sorted(blamed)
        for markup, lost in found
    ]


def assemble(sel: Selection, m: Master, texts: dict[str, str]) -> CommentedMap:
    """The tailored document: master top level, `cv.sections` rebuilt from the selection."""
    master_sections = m.data["cv"].get("sections") or {}
    sections = CommentedMap()
    for s in sel.sections:
        entries = CommentedSeq()
        for e in s.entries:
            index = int(e.id.rsplit(".", 1)[1])
            entries.append(_entry(e.id, master_sections[s.id][index], e, texts, m))
        sections[s.id] = entries

    cv = CommentedMap()
    for key, value in m.data["cv"].items():
        cv[key] = sections if key == "sections" else value
    doc = CommentedMap()
    for key, value in m.data.items():
        doc[key] = cv if key == "cv" else value
    return doc


def _dump(doc: CommentedMap) -> str:
    yaml = ruamel.yaml.YAML()
    yaml.width = 4096  # keep long lines unwrapped
    yaml.allow_unicode = True
    buf = io.StringIO()
    yaml.dump(doc, buf)
    return buf.getvalue()


def _validate_output(text: str) -> None:
    """RenderCV-validate the tailored YAML; failure is a cv-skills bug (Failure(2))."""
    try:
        build_rendercv_dictionary_and_model(text)
    except RenderCVUserValidationError as e:
        details = "; ".join(
            f"`{'.'.join(str(p) for p in ve.schema_location or ())}`: {ve.message}" for ve in e.validation_errors
        )
    except RenderCVUserError as e:
        details = str(e.message)
    else:
        return
    raise Failure(
        2,
        [
            _error(
                "internal_invalid_output",
                "",
                f"The tailored YAML failed RenderCV validation ({details}). This is a cv-skills bug,"
                f" not a selection problem; nothing was written. Please report it with the master"
                f" and selection.",
            )
        ],
    )


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file + rename; the file gets the usual `0o666 & ~umask` mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o666 & ~_umask())  # mkstemp creates 0600
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _check_out_not_input(out: Path, inputs: dict[str, Path]) -> None:
    """Refuse an `--out` that resolves to an input file (it would be overwritten)."""
    target = out.resolve()
    for flag, p in inputs.items():
        if target == p.resolve():
            raise Failure(
                2,
                [
                    _error(
                        "out_is_input",
                        str(out),
                        f"`--out {out}` is the same file as `{flag} {p}`; writing it would overwrite"
                        f" the input. Choose a different `--out` path.",
                    )
                ],
            )


def tailored_yaml(sel: Selection, m: Master, texts: dict[str, str], written=None) -> str:
    """The tailored YAML text `build` writes: `assemble`, then the written summary (if any) in its section."""
    doc = assemble(sel, m, texts)
    if written is not None:
        from cv_skills.summary import apply_summary

        apply_summary(doc, *written)
    return _dump(doc)


def build(
    master_path: Path,
    selection_path: Path,
    out_path: Path,
    summary_path: Path | None = None,
    guide_path: Path | None = None,
) -> dict:
    """Build the tailored CV; returns the ok JSON. Raises Failure (nothing written).

    With `summary_path` and `guide_path` (both or neither) the guide's section becomes the checked written summary.
    """
    from cv_skills.summary import load_written, summary_record, written_errors

    out_path = Path(out_path)
    inputs = {"--master": Path(master_path), "--selection": Path(selection_path)}
    inputs.update({flag: Path(p) for flag, p in (("--summary", summary_path), ("--guide", guide_path)) if p is not None})
    _check_out_not_input(out_path, inputs)
    m = load_master(Path(master_path))
    sel = read_selection(Path(selection_path))
    written = load_written(summary_path, guide_path, m)
    errors = check_structure(sel, m) + check_swaps(sel, m)
    if errors:
        raise Failure(1, errors)
    texts, records = apply_swaps(sel, m)
    errors = _swap_markup_errors(sel, m, texts) or _selection_markup_errors(sel, m, texts)
    if errors:
        raise Failure(1, errors)
    if written is not None and (errors := written_errors(m, sel, written)):
        raise Failure(1, errors)
    text = tailored_yaml(sel, m, texts, written)
    _validate_output(text)
    titles = title_changes(sel, m)
    result = {"ok": True, "out": str(out_path), "master_hash": m.hash, "swaps": records, "titles": titles}
    if written is not None:
        result["summary"] = summary_record(*written)
    # Serialise before writing: if the result cannot be emitted, nothing is written.
    json.dumps(result, ensure_ascii=False).encode("utf-8")
    try:
        _write_atomic(out_path, text)
    except OSError as e:
        raise Failure(
            2,
            [
                _error(
                    "out_unwritable",
                    str(out_path),
                    f"Cannot write `--out {out_path}`: {e.strerror or e}. Choose a writable file path"
                    f" (not a directory).",
                )
            ],
        )
    return result
