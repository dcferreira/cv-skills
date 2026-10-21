"""Master CV loading: validation, path IDs, master hash, extra-key stripping.

IDs are dot-separated paths, 0-based, stable within one master hash:
  section     `<section key>`
  entry       `<section>.<i>`
  bullet      `<section>.<i>.bullet`          (BulletEntry text)
  summary     `<section>.<i>.summary`
  highlight   `<section>.<i>.highlights.<j>`
  detail      `<section>.<i>.details.<k>`     (OneLine `details`, see split_details)
  position    `<section>.<i>.position`        (only on an entry with `title_variants`)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pydantic
import pydantic_core
import ruamel.yaml
from rendercv.exception import RenderCVInternalError, RenderCVUserError, RenderCVUserValidationError
from rendercv.renderer.templater.entry_templates_from_input import process_summary
from rendercv.renderer.templater.string_processor import clean_url
from rendercv.schema.models.cv.section import (
    dictionary_key_to_proper_section_title,
    get_entry_type_name_and_section_model,
)
from rendercv.schema.models.cv.social_network import SocialNetwork
from rendercv.schema.rendercv_model_builder import build_rendercv_dictionary_and_model
from rendercv.schema.yaml_reader import read_yaml
from ruamel.yaml.comments import CommentedMap

from cv_skills.cli import Failure
from cv_skills.markupcheck import swallowed

Kind = Literal["section", "entry", "highlight", "summary", "detail", "bullet", "position"]

EXTRA_KEYS = ("pin", "tags", "title_variants")
# Entry fields RenderCV 2.8 never runs through Markdown (`process_fields`'s `skipped`).
_UNPROCESSED_ENTRY_FIELDS = ("start_date", "end_date", "doi", "url")
WHY_STRINGS = "RenderCV 2.8 needs string values for entry keys (it crashes rendering anything else)."
VARIANTS_EXAMPLE = "`title_variants: Senior Analytics Engineer; Senior ML Engineer`"

# Fields that identify an entry, per RenderCV entry type, in label order.
_LABEL_FIELDS = (
    (("company", "position"), ("company", "position")),
    (("institution", "area"), ("institution", "degree", "area")),
    (("title", "authors"), ("title",)),
    (("label", "details"), ("label",)),
    (("bullet",), ("bullet",)),
    (("number",), ("number",)),
    (("reversed_number",), ("reversed_number",)),
    (("name",), ("name",)),
)


@dataclass(frozen=True)
class Node:
    id: str
    kind: Kind
    text: str | None
    pin: bool
    parent: str | None
    swappable: bool
    variants: tuple[str, ...] = ()  # a `position` node's alternative titles


@dataclass
class Master:
    path: Path
    hash: str
    data: CommentedMap
    nodes: dict[str, Node] = field(default_factory=dict)

    def children(self, id: str, kind: str) -> list[str]:
        """IDs of `id`'s direct children of `kind`, in master order."""
        return [n.id for n in self.nodes.values() if n.parent == id and n.kind == kind]

    def text_of(self, id: str) -> str:
        """Master text of a swappable node; KeyError for unknown or non-swappable IDs."""
        node = self.nodes.get(id)
        if node is None or not node.swappable or node.text is None:
            raise KeyError(id)
        return node.text


def split_details(s: str) -> list[str]:
    """Split a OneLine `details` string on commas outside `()`/`[]`; trim; drop empties."""
    items: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(depth - 1, 0)
        if ch == "," and depth == 0:
            items.append("".join(current))
            current = []
        else:
            current.append(ch)
    items.append("".join(current))
    return [item.strip() for item in items if item.strip()]


def split_variants(s: str) -> list[str]:
    """Split a `title_variants` string on `;`; trim. Empty items are kept (the master check rejects them)."""
    return [item.strip() for item in s.split(";")]


def strip_extra_keys(entry: dict) -> dict:
    """A copy of `entry` (order kept) without the cv-skills keys `pin`, `tags` and `title_variants`."""
    out = CommentedMap()
    for key, value in entry.items():
        if key not in EXTRA_KEYS:
            out[key] = value
    return out


def entry_label(entry: object) -> str:
    """Short label for an entry: the string itself, or its identifying fields joined."""
    if isinstance(entry, str):
        return entry
    for required, fields in _LABEL_FIELDS:
        if all(k in entry for k in required):
            return ", ".join(str(entry[f]) for f in fields if entry.get(f))
    return ""


def _error(code: str, id: str, message: str) -> dict:
    return {"code": code, "id": id, "message": message}


def _sections(data: object) -> dict:
    cv = data.get("cv") if isinstance(data, dict) else None
    sections = cv.get("sections") if isinstance(cv, dict) else None
    return sections if isinstance(sections, dict) else {}


def _rule_errors(data: object) -> list[dict]:
    """cv-skills rules on top of RenderCV's schema (all violations, not first-only)."""
    errors: list[dict] = []
    for key, entries in _sections(data).items():
        key = str(key)
        if "." in key:
            errors.append(
                _error(
                    "section_key_has_dot",
                    key,
                    f"Section `{key}`: section keys must not contain `.` because IDs are"
                    f" dot-separated paths. Rename it, e.g. to `{key.replace('.', '_')}`.",
                )
            )
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            eid = f"{key}.{i}"
            if "pin" in entry and entry["pin"] != "yes":
                errors.append(
                    _error(
                        "pin_not_yes",
                        eid,
                        f"Entry `{eid}`: `pin` must be the string yes; got {entry['pin']!r}."
                        f" Write `pin: yes` to pin the entry, or delete the key to unpin it."
                        f" {WHY_STRINGS}",
                    )
                )
            if "tags" in entry and not isinstance(entry["tags"], str):
                errors.append(
                    _error(
                        "tags_not_string",
                        eid,
                        f"Entry `{eid}`: `tags` must be one comma-separated string, e.g."
                        f" `tags: ml, infra`; got a {type(entry['tags']).__name__}."
                        f" {WHY_STRINGS}",
                    )
                )
            if "title_variants" in entry:
                errors.extend(_variant_errors(entry, f"{eid}.title_variants"))
    return errors


def _variant_errors(entry: dict, id: str) -> list[dict]:
    """`title_variants_invalid`: one error per violation of the `title_variants` rules."""

    def bad(why: str) -> dict:
        return _error("title_variants_invalid", id, f"`{id}`: {why} Write it like {VARIANTS_EXAMPLE}.")

    value = entry["title_variants"]
    if not isinstance(value, str):
        # One string, not a YAML list (WHY_STRINGS); `;` because titles may contain commas.
        got = "list" if isinstance(value, list) else "mapping" if isinstance(value, dict) else type(value).__name__
        return [bad(f"must be one `;`-separated string of titles; got a {got}. {WHY_STRINGS}")]
    position = entry.get("position")
    if not isinstance(position, str) or not position.strip():
        return [
            bad("lists alternatives to the entry's `position`, but the entry has no `position`. Add one or delete the key.")
        ]
    errors: list[dict] = []
    seen: set[str] = set()
    for k, item in enumerate(split_variants(value)):
        if not item:
            errors.append(bad(f"item {k} is empty (a doubled, leading or trailing `;`)."))
        elif any(c in item for c in "\r\n"):
            errors.append(bad(f"item {k} has a line break; a job title is one line."))
        elif item == position.strip():
            errors.append(bad(f"`{item}` is the entry's `position`, which is always allowed: list only alternatives."))
        elif item in seen:
            errors.append(bad(f"lists `{item}` twice."))
        seen.add(item)
    return errors


def bold_keywords(data: object) -> list[str]:
    """The master's `settings.bold_keywords` (strings only), which RenderCV bolds in the body."""
    settings = data.get("settings") if isinstance(data, dict) else None
    keywords = settings.get("bold_keywords") if isinstance(settings, dict) else None
    return [k for k in keywords if isinstance(k, str)] if isinstance(keywords, list) else []


def markup_message(markup: str, lost: str) -> str:
    """The shared explanation of one swallowed occurrence (follows the ID)."""
    effect = "it is dropped from the PDF" if lost == ";" else "the render fails"
    return f"Typst reads the `{lost}` right after `{markup}` as part of the markup, so {effect}."


def _inside(markup: str, s: str) -> str:
    """`markup` with `s` moved just inside its closing delimiter."""
    if markup.startswith("**"):
        return f"{markup[:-2]}{s}**"
    if markup.startswith("["):
        k = markup.rfind("](")
        return f"{markup[:k]}{s}{markup[k:]}"
    return f"{markup[:-1]}{s}*"


def _markup_fix(markup: str, lost: str) -> str:
    if lost == ";":
        return f"Move the `;` inside the markup (`{_inside(markup, ';')}`) or drop the markup."
    if lost == "(":
        return "Put a space before the `(`, move the parenthesised text inside the markup, or drop the markup."
    return f"Move the `{lost[0]}` and the word after it inside the markup (e.g. `**Node.js**`) or drop the markup."


def _strings(value: object, id: str):
    """(path ID, string) for every string in `value`, recursing into lists and mappings."""
    if isinstance(value, str):
        yield id, value
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _strings(item, f"{id}.{i}")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{id}.{key}")


def _url(value: object) -> str | None:
    """`value` as RenderCV holds a URL field (a normalised pydantic `HttpUrl`), or None if it is not one."""
    try:
        return str(pydantic.HttpUrl(str(value)))
    except pydantic.ValidationError:
        return None


def _design_flag(data: object, *path: str) -> bool:
    node = data.get("design") if isinstance(data, dict) else None
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    return node is True


def _header_strings(cv: dict, data: object):
    """(path ID, text as RenderCV prints it, gets bold_keywords) for the header.

    Mirrors RenderCV 2.8: `process_model` runs name and headline through bold_keywords and
    Markdown, `parse_connections` builds the contact bodies (Markdown only). `photo`, URLs,
    network names and icons are not printed as text.
    """
    for key in ("name", "headline"):
        for id, s in _strings(cv.get(key), key):
            yield id, s, True
    for key in ("location", "email", "phone"):
        for id, s in _strings(cv.get(key), key):
            yield id, s, False
    for id, s in _strings(cv.get("website"), "website"):
        if (url := _url(s)) is not None:
            yield id, clean_url(url), False
    as_urls = _design_flag(data, "header", "connections", "display_urls_instead_of_usernames")
    networks = cv.get("social_networks")
    for i, sn in enumerate(networks if isinstance(networks, list) else []):
        if not isinstance(sn, dict) or not isinstance(sn.get("username"), str):
            continue
        if sn.get("network") == "Google Scholar" and not as_urls:
            continue  # printed as the literal "Google Scholar"
        body = sn["username"]
        if as_urls:
            try:
                body = clean_url(SocialNetwork(network=sn.get("network"), username=body).url)
            except pydantic.ValidationError:
                continue  # RenderCV rejects it (master_invalid)
        yield f"social_networks.{i}.username", body, False
    custom = cv.get("custom_connections")
    for i, cc in enumerate(custom if isinstance(custom, list) else []):
        if isinstance(cc, dict):
            for id, s in _strings(cc.get("placeholder"), f"custom_connections.{i}.placeholder"):
                yield id, s, False


def _is_publication(entry: dict) -> bool:
    try:
        return get_entry_type_name_and_section_model(entry)[0] == "PublicationEntry"
    except pydantic_core.PydanticCustomError:
        return False


def _entry_strings(entry: object, eid: str):
    """(path ID, text as RenderCV prints it) for one entry: RenderCV 2.8's `render_entry_templates`
    and `process_fields` (which skips the url, doi and date fields)."""
    if not isinstance(entry, dict):
        yield from _strings(entry, eid)
        return
    for key, value in entry.items():
        if key in EXTRA_KEYS or key in _UNPROCESSED_ENTRY_FIELDS:
            continue
        for id, s in _strings(value, f"{eid}.{key}"):
            # A summary is printed as one `!!! summary` block, so its markup may span lines.
            yield id, process_summary(s) if key == "summary" else s
    if isinstance(entry.get("title_variants"), str):
        # A chosen variant is printed in the `position` slot.
        for k, item in enumerate(split_variants(entry["title_variants"])):
            yield f"{eid}.title_variants.{k}", item
    if _is_publication(entry):
        # A publication prints its DOI, else its URL, as a Markdown link (`process_url`).
        if isinstance(entry.get("doi"), str):
            yield f"{eid}.doi", f"[{entry['doi']}](https://doi.org/{entry['doi']})"
        elif "url" in entry and (url := _url(entry["url"])) is not None:
            yield f"{eid}.url", f"[{clean_url(url)}]({url})"


def _rendered_strings(data: object):
    """(path ID, text as RenderCV prints it, gets bold_keywords) for every master string RenderCV renders."""
    cv = data.get("cv") if isinstance(data, dict) else None
    if not isinstance(cv, dict):
        return
    yield from _header_strings(cv, data)
    for key, entries in _sections(data).items():
        sid = str(key)
        yield sid, dictionary_key_to_proper_section_title(sid), True
        for i, entry in enumerate(entries if isinstance(entries, list) else []):
            for id, s in _entry_strings(entry, f"{sid}.{i}"):
                yield id, s, True


def _markup_errors(data: object) -> list[dict]:
    """`markup_swallows_text`: one error per text RenderCV would lose after inline markup."""
    keywords = bold_keywords(data)
    return [
        _error("markup_swallows_text", id, f"`{id}`: {markup_message(markup, lost)} {_markup_fix(markup, lost)}")
        for id, s, uses_keywords in _rendered_strings(data)
        for markup, lost in swallowed(s, keywords if uses_keywords else None)
    ]


def _rendercv_errors(text: str, path: Path) -> list[dict]:
    try:
        build_rendercv_dictionary_and_model(text, input_file_path=path)
    except RenderCVUserValidationError as e:
        out = []
        for ve in e.validation_errors:
            loc = ".".join(str(p) for p in ve.schema_location) if ve.schema_location else ""
            where = f" (line {ve.yaml_location[0][0]})" if ve.yaml_location else ""
            out.append(
                _error(
                    "master_invalid",
                    loc,
                    f"Master is not a valid RenderCV 2.8 file at `{loc or 'top level'}`{where}:"
                    f" {ve.message} Fix the master so `rendercv render` accepts it.",
                )
            )
        return out
    except RenderCVUserError as e:
        return [_error("master_invalid", "", f"Master is not a valid RenderCV file: {e.message}")]
    except Exception as e:
        # RenderCV crashes (KeyError, RenderCVInternalError, …) on some invalid
        # masters, e.g. a non-string text entry or a non-string section key.
        return [
            _error(
                "master_invalid",
                "",
                f"RenderCV 2.8 could not validate the master ({_one_line(e)}). Look for non-string"
                f" values where RenderCV expects text (section keys, text entries, highlights) and"
                f" fix the master so `rendercv render` accepts it.",
            )
        ]
    return []


def _one_line(e: BaseException) -> str:
    """`<Type>: <message>` collapsed to one line."""
    msg = getattr(e, "message", None) or str(e)
    return f"{type(e).__name__}: {' '.join(str(msg).split())}"


def _yaml_problem(e: ruamel.yaml.YAMLError) -> str:
    """The ruamel problem with its 1-based location (and context), on one line."""
    problem = getattr(e, "problem", None)
    mark = getattr(e, "problem_mark", None)
    if not problem or mark is None:
        return " ".join(str(e).split()) or "parse error"
    out = f"{problem} at line {mark.line + 1}, column {mark.column + 1}"
    context = getattr(e, "context", None)
    cmark = getattr(e, "context_mark", None)
    if context and cmark is not None:
        out += f" ({context} started at line {cmark.line + 1}, column {cmark.column + 1})"
    return out


def _build_nodes(data: CommentedMap) -> dict[str, Node]:
    nodes: dict[str, Node] = {}

    def add(
        id: str, kind: Kind, text: str | None, parent: str | None, swappable: bool, pin: bool = False, variants=()
    ) -> None:
        nodes[id] = Node(id, kind, text, pin, parent, swappable, tuple(variants))

    for key, entries in _sections(data).items():
        sid = str(key)
        add(sid, "section", None, None, False)
        for i, entry in enumerate(entries):
            eid = f"{sid}.{i}"
            if isinstance(entry, str):
                add(eid, "entry", entry, sid, True)
                continue
            add(eid, "entry", entry_label(entry), sid, False, pin=entry.get("pin") == "yes")
            if isinstance(entry.get("title_variants"), str):
                variants = split_variants(entry["title_variants"])
                add(f"{eid}.position", "position", entry["position"], eid, False, variants=variants)
            if isinstance(entry.get("bullet"), str):
                add(f"{eid}.bullet", "bullet", entry["bullet"], eid, True)
            if isinstance(entry.get("summary"), str) and entry["summary"]:
                add(f"{eid}.summary", "summary", entry["summary"], eid, True)
            for j, h in enumerate(entry.get("highlights") or []):
                add(f"{eid}.highlights.{j}", "highlight", h, eid, True)
            if "label" in entry and isinstance(entry.get("details"), str):
                for k, item in enumerate(split_details(entry["details"])):
                    add(f"{eid}.details.{k}", "detail", item, eid, True)
    return nodes


def load_master(path: Path) -> Master:
    """Load and validate a master file; raises Failure(2, errors) if unusable."""
    path = Path(path)
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except OSError as e:
        raise Failure(2, [_error("master_invalid", str(path), f"Cannot read master file `{path}`: {e.strerror or e}.")])
    except UnicodeDecodeError as e:
        raise Failure(2, [_error("master_invalid", str(path), f"Master file `{path}` is not UTF-8: {e}.")])

    def invalid(why: str) -> Failure:
        return Failure(2, [_error("master_invalid", str(path), f"Master file `{path}` {why}")])

    try:
        data = read_yaml(text)
    except ruamel.yaml.YAMLError as e:
        raise invalid(f"is not valid YAML: {_yaml_problem(e)}. Fix the YAML syntax there.")
    except RenderCVUserError as e:
        raise invalid(f"is not a usable RenderCV file: {e.message}")
    except RenderCVInternalError:
        # read_yaml raises this only when the whole document is a single string.
        data = text
    except Exception as e:
        raise invalid(f"could not be read as RenderCV YAML ({_one_line(e)}).")
    if not isinstance(data, dict):
        top = "list" if isinstance(data, list) else "string" if isinstance(data, str) else type(data).__name__
        raise invalid(
            f"must be a YAML mapping with a top-level `cv` key, but its top level is"
            f" a {top}. Write it as a RenderCV file (`cv:`, `design:`, …)."
        )

    errors = _rule_errors(data) + _markup_errors(data) + _rendercv_errors(text, path)
    if errors:
        raise Failure(2, errors)

    return Master(
        path=path,
        hash="sha256:" + hashlib.sha256(raw).hexdigest(),
        data=data,
        nodes=_build_nodes(data),
    )


def ids_listing(m: Master) -> dict:
    """The `cv-skills ids` JSON: master hash and every node in master order."""
    ids = []
    for n in m.nodes.values():
        item: dict = {"id": n.id, "kind": n.kind}
        if n.text is not None:
            item["text"] = n.text
        if n.pin:
            item["pin"] = True
        if n.variants:
            item["variants"] = list(n.variants)
        ids.append(item)
    return {"master_hash": m.hash, "ids": ids}
