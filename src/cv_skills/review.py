"""Recruiter review: schema, quote verification and the gate.

`requirements.json` (step 2) and `review.json` (step 5) are typed agent outputs;
both are validated strictly with precise errors. A recruiter's `met`/`partial`
only counts when its quote is found, after normalisation, inside ONE line of
the tailored CV; otherwise the requirement is `missing`. The gate passes iff
every must-have is met or partial. Only `evidence: "cv"` requirements are graded
and gated; an `evidence: "none"` one (a CV line can't show it) is listed for the
user to judge.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from cv_skills.cli import Failure
from cv_skills.cvtext import normalise

STATUSES = ("met", "partial", "missing")
KINDS = ("must", "nice")
EVIDENCE = ("cv", "none")
# What the extractor marks evidence "none": a CV line can't show it with a quote.
NONE_CATEGORIES = (
    "language fluency, generic soft skills, work authorisation or visa,"
    " willingness to relocate, travel or work a timezone overlap"
)
MAX_REDRAFTS = 2


def _error(code: str, id: str, message: str) -> dict:
    return {"code": code, "id": id, "message": message}


def _jtype(v: object) -> str:
    return {dict: "object", list: "array", str: "string", bool: "boolean", int: "number", float: "number"}.get(
        type(v), "null" if v is None else type(v).__name__
    )


def _is_text(v: object) -> bool:
    if not isinstance(v, str):
        return False
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


class _Schema:
    """Collects schema errors for one file under one error code."""

    def __init__(self, code: str, what: str, fix: str) -> None:
        self.code, self.what, self.fix = code, what, fix
        self.errors: list[dict] = []

    def fail(self, path: str, message: str) -> None:
        self.errors.append(_error(self.code, path, f"{self.what}: `{path}` {message}. {self.fix}"))

    def keys(self, obj: object, path: str, required: tuple[str, ...]) -> bool:
        if not isinstance(obj, dict):
            self.fail(path or "(top level)", f"must be an object, not {_jtype(obj)}")
            return False
        ok = True
        for k in obj:
            if k not in required:  # reported, but the known keys are still checked
                self.fail(f"{path}.{k}" if path else k, f"is not a known key (allowed: {', '.join(required)})")
        for k in required:
            if k not in obj:
                self.fail(f"{path}.{k}" if path else k, "is missing")
                ok = False
        return ok

    def text(self, obj: dict, key: str, path: str, *, allow_empty: bool = True) -> str | None:
        p = f"{path}.{key}" if path else key
        v = obj[key]
        if not _is_text(v):
            self.fail(p, f"must be a string of valid UTF-8 text, not {_jtype(v)}")
            return None
        if not allow_empty and not v.strip():
            self.fail(p, "must not be empty")
            return None
        return v

    def text_list(self, obj: dict, key: str) -> list[str] | None:
        v = obj[key]
        if not isinstance(v, list):
            self.fail(key, f"must be an array of strings, not {_jtype(v)}")
            return None
        out, ok = [], True
        for i, s in enumerate(v):
            if _is_text(s):
                out.append(s)
            else:
                self.fail(f"{key}[{i}]", f"must be a string of valid UTF-8 text, not {_jtype(s)}")
                ok = False
        return out if ok else None


@dataclass
class Requirement:
    id: str
    text: str
    kind: str
    evidence: str


@dataclass
class Requirements:
    company: str
    role: str
    items: list[Requirement]

    @property
    def graded(self) -> list[Requirement]:
        """The `evidence: "cv"` requirements: the ones the recruiter grades and the gate counts."""
        return [r for r in self.items if r.evidence == "cv"]


def _read_json(path: Path, code: str, what: str, fix: str) -> object:
    path = Path(path)
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except OSError as e:
        why = f"cannot be read: {e.strerror or e}"
    except UnicodeDecodeError as e:
        why = f"is not UTF-8: {e}"
    except json.JSONDecodeError as e:
        why = f"is not valid JSON: {e}"
    raise Failure(2, [_error(code, str(path), f"{what} `{path}` {why}. {fix}")])


def load_requirements(path: Path) -> Requirements:
    """Read `requirements.json`; any problem is Failure(2) `requirements_invalid`."""
    fix = "Rewrite it as {company, role, requirements: [{id, text, kind: must|nice, evidence: cv|none}]}."
    evidence_rule = f'"cv" (a CV line can show it) or "none" ({NONE_CATEGORIES})'
    raw = _read_json(path, "requirements_invalid", "Requirements file", fix)
    s = _Schema("requirements_invalid", f"Requirements file `{path}`", fix)
    items: list[Requirement] = []
    company = role = None
    if s.keys(raw, "", ("company", "role", "requirements")):
        company = s.text(raw, "company", "")
        role = s.text(raw, "role", "")
        lst = raw["requirements"]
        if not isinstance(lst, list):
            s.fail("requirements", f"must be an array, not {_jtype(lst)}")
        else:
            seen: set[str] = set()
            for i, r in enumerate(lst):
                p = f"requirements[{i}]"
                no_evidence = isinstance(r, dict) and "evidence" not in r
                if no_evidence:
                    # Its own message, not "is missing": such a file predates the field.
                    s.errors.append(
                        _error(
                            "requirements_invalid",
                            f"{p}.evidence",
                            f"Requirements file `{path}`: {p} has no `evidence`: add {evidence_rule}."
                            " A requirements.json written before cv-skills had this field needs a fresh run.",
                        )
                    )
                    r = {**r, "evidence": None}  # reported; the other keys are still checked
                if not s.keys(r, p, ("id", "text", "kind", "evidence")):
                    continue
                rid = s.text(r, "id", p, allow_empty=False)
                text = s.text(r, "text", p, allow_empty=False)
                kind, evidence = r["kind"], r["evidence"]
                if not no_evidence and evidence not in EVIDENCE:
                    s.fail(f"{p}.evidence", f"must be {evidence_rule}, not {evidence!r}")
                if rid is not None and rid in seen:
                    s.fail(f"{p}.id", f"repeats id `{rid}`")
                    continue
                if rid is not None:
                    seen.add(rid)  # even when another key is bad, so a duplicate is reported in the same pass
                if kind not in KINDS:
                    s.fail(f"{p}.kind", f"must be one of {', '.join(KINDS)}, not {kind!r}")
                elif rid is not None and text is not None and evidence in EVIDENCE:
                    items.append(Requirement(rid, text, kind, evidence))
    if s.errors:
        raise Failure(2, s.errors)
    return Requirements(company or "", role or "", items)


@dataclass
class ReviewItem:
    id: str
    status: str
    evidence_quote: str
    reason: str


@dataclass
class Review:
    items: list[ReviewItem]
    top_fixes: list[str] = field(default_factory=list)
    missed_requirements: list[str] = field(default_factory=list)


def parse_review(raw: object, reqs: Requirements) -> Review:
    """Validate the recruiter's output against the requirements; Failure(1) lists every problem."""
    fix = (
        "Rewrite review.json as {requirements: [{id, status: met|partial|missing, evidence_quote, reason}],"
        ' top_fixes: [str], missed_requirements: [str]} with one entry per requirement whose evidence is "cv"'
        ' (none for "none" ones).'
    )
    s = _Schema("review_schema", "Review", fix)
    items: list[ReviewItem] = []
    top_fixes: list[str] = []
    missed: list[str] = []
    errors: list[dict] = []
    if s.keys(raw, "", ("requirements", "top_fixes", "missed_requirements")):
        top_fixes = s.text_list(raw, "top_fixes") or []
        missed = s.text_list(raw, "missed_requirements") or []
        lst = raw["requirements"]
        if not isinstance(lst, list):
            s.fail("requirements", f"must be an array, not {_jtype(lst)}")
        else:
            known = {r.id for r in reqs.graded}
            unrated = {r.id for r in reqs.items} - known
            seen: set[str] = set()
            for i, r in enumerate(lst):
                p = f"requirements[{i}]"
                if not s.keys(r, p, ("id", "status", "evidence_quote", "reason")):
                    continue
                rid = s.text(r, "id", p)
                quote = s.text(r, "evidence_quote", p)
                reason = s.text(r, "reason", p)
                status = r["status"]
                if status not in STATUSES:
                    s.fail(f"{p}.status", f"must be one of {', '.join(STATUSES)}, not {status!r}")
                    continue
                if rid is None or quote is None or reason is None:
                    continue
                if rid in unrated:
                    errors.append(
                        _error(
                            "review_unrated_requirement",
                            rid,
                            f'`{rid}` is marked evidence "none" in requirements.json: leave it out; the user judges it.',
                        )
                    )
                elif rid not in known:
                    errors.append(
                        _error(
                            "review_unknown_requirement",
                            rid,
                            f"Review lists requirement `{rid}` ({p}), which is not in requirements.json."
                            f" Use only the ids {', '.join(sorted(known)) or '(none)'}.",
                        )
                    )
                elif rid in seen:
                    errors.append(
                        _error(
                            "review_duplicate_requirement",
                            rid,
                            f"Review lists requirement `{rid}` more than once ({p}). Keep exactly one entry per id.",
                        )
                    )
                else:
                    seen.add(rid)
                    items.append(ReviewItem(rid, status, quote, reason))
            if not errors and not s.errors:
                for r in reqs.graded:
                    if r.id not in seen:
                        errors.append(
                            _error(
                                "review_missing_requirement",
                                r.id,
                                f"Review has no entry for requirement `{r.id}`. Add one (status `missing`"
                                f" with an empty evidence_quote if nothing supports it).",
                            )
                        )
            else:
                # Still report ids that never appeared, once the entries are otherwise readable.
                for r in reqs.graded:
                    if r.id not in seen and not any(e["id"] == r.id for e in errors):
                        errors.append(
                            _error(
                                "review_missing_requirement",
                                r.id,
                                f"Review has no valid entry for requirement `{r.id}`. Add one.",
                            )
                        )
    errors = s.errors + errors
    if errors:
        raise Failure(1, errors)
    return Review(items, top_fixes, missed)


def load_review(path: Path, reqs: Requirements) -> Review:
    """Read and validate `review.json`; unreadable/unparsable is Failure(2) `review_invalid`."""
    raw = _read_json(path, "review_invalid", "Review file", "Write it as UTF-8 JSON.")
    return parse_review(raw, reqs)


def verify(review: Review, reqs: Requirements, lines: list[str]) -> list[dict]:
    """Verified records of the `evidence: "cv"` requirements, in requirements.json order.

    `met`/`partial` stays only if the normalised quote has a letter or digit and is a
    substring of one normalised CV line; otherwise `missing`, `downgraded`.
    """
    norm_lines = [normalise(line) for line in lines]
    raw_lines = [normalise(line, markup=False) for line in lines]  # for a quote copied with its markup
    by_id = {i.id: i for i in review.items}
    out = []
    for r in reqs.graded:
        item = by_id[r.id]
        status, downgraded = item.status, False
        if status != "missing":
            q = normalise(item.evidence_quote)
            raw_q = normalise(item.evidence_quote, markup=False)
            found = any(c.isalnum() for c in q) and (any(q in line for line in norm_lines) or any(raw_q in line for line in raw_lines))
            if not found:
                status, downgraded = "missing", True
        out.append(
            {
                "id": r.id,
                "kind": r.kind,
                "text": r.text,
                "status": status,
                "claimed_status": item.status,
                "downgraded": downgraded,
                "evidence_quote": item.evidence_quote,
                "reason": item.reason,
            }
        )
    return out


def decide(verified: list[dict], redrafts_done: int, prev_supported: int | None) -> dict:
    """The gate and what happens next: `pass`, `redraft` or `human`."""
    musts = [v for v in verified if v["kind"] == "must"]
    unmet = [v["id"] for v in musts if v["status"] == "missing"]
    supported = len(musts) - len(unmet)
    if not unmet:
        gate, outcome = "pass", "pass"
    else:
        gate = "fail"
        progressing = prev_supported is None or supported > prev_supported
        outcome = "redraft" if redrafts_done < MAX_REDRAFTS and progressing else "human"
    return {
        "gate": gate,
        "outcome": outcome,
        "supported_must": supported,
        "total_must": len(musts),
        "unmet_must": unmet,
    }


def check_review(
    review_path: Path,
    requirements_path: Path,
    tailored_path: Path,
    out_path: Path,
    *,
    redrafts_done: int = 0,
    prev_supported: int | None = None,
    exclude_section: str | None = None,
) -> dict:
    """Verify the review against the tailored CV, write the verified review, return the summary JSON.

    With `exclude_section`, quotes are verified against the CV lines without that section (`cv_lines`).
    Raises Failure (nothing written). A failed gate is not a failure: see `decide`.
    """
    from cv_skills.cvtext import cv_lines, load_tailored
    from cv_skills.outfile import check_out, write_out

    if redrafts_done > 0 and prev_supported is None:
        raise Failure(
            2,
            [
                _error(
                    "usage",
                    "",
                    "check-review: `--prev-supported` is required when `--redrafts-done` is above 0."
                    " Pass the supported_must of the previous round.",
                )
            ],
        )
    out_path = Path(out_path)
    check_out(out_path, {"--review": review_path, "--requirements": requirements_path, "--tailored": tailored_path})
    reqs = load_requirements(requirements_path)
    lines = cv_lines(load_tailored(tailored_path), exclude_section=exclude_section)
    review = load_review(review_path, reqs)
    verified = verify(review, reqs, lines)
    decision = decide(verified, redrafts_done, prev_supported)
    # Out of the gate and the counts: the user judges what a CV line can't show.
    to_judge = [{"id": r.id, "kind": r.kind, "text": r.text} for r in reqs.items if r.evidence == "none"]
    document = {
        "company": reqs.company,
        "role": reqs.role,
        "tailored_sha256": hashlib.sha256(Path(tailored_path).read_bytes()).hexdigest(),
        "requirements": verified,
        "for_you_to_judge": to_judge,
        "top_fixes": review.top_fixes,
        "missed_requirements": review.missed_requirements,
        **decision,
    }
    write_out(out_path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    return {
        "ok": True,
        "out": str(out_path),
        **decision,
        "downgraded": [v["id"] for v in verified if v["downgraded"]],
        "for_you_to_judge": to_judge,
    }
