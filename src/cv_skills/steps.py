"""`python -m cv_skills.steps <name> <args...>`: the pawl step adapters of the `tailor` workflow.

pawl reads only the LAST non-empty stdout line of a deterministic step: `TOKEN [payload]` for a step
with outcomes, else the payload alone (a JSON object, or `k=v` pairs for `emits: pairs`). So every
adapter prints its log lines (the cv-skills result or error JSON) first and that one line last. A
postcondition adapter passes with exit 0; on exit 1 its stdout, the cv-skills error JSON, is what the
agent is told to fix. Any other problem is exit 1 (pawl `failure`). Paths are relative to the cwd,
the CV repo root; `A` is the application folder.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from cv_skills.appdir import app_dir
from cv_skills.build import _write_atomic, build, read_selection
from cv_skills.carry import SNAPSHOT, carry_selection, old_master
from cv_skills.cli import Failure, emit
from cv_skills.cvtext import cv_lines, load_tailored, strip_markup
from cv_skills.fetch import check_url, fetch_posting
from cv_skills.finalrender import final_render
from cv_skills.floor import pin_floor
from cv_skills.master import load_master
from cv_skills.pagecheck import page_check
from cv_skills.review import check_review
from cv_skills.reviewpage import must_split, review_page
from cv_skills.summary import load_guide, load_summary, summary_check

APPLICATIONS = Path("applications")
MARKER = ".cv-skills-run"  # holds the `.tmp-<run_id>` name the folder had before `name-folder`
UNUSABLE = {"url_invalid", "input_unreadable", "posting_empty"}  # the user must supply the posting


def _usage(message: str) -> Failure:
    return Failure(1, [{"code": "usage", "id": "", "message": message}])


def _int(value: str, what: str) -> int:
    if not (value.isascii() and value.isdigit() and len(value) <= 9):
        raise _usage(f"{what} must be a non-negative integer, got `{value}`.")
    return int(value)


def _bool(value: str, what: str) -> bool:
    if value not in ("true", "false"):
        raise _usage(f"{what} must be `true` or `false`, got `{value}`.")
    return value == "true"


def _final(token: str | None = None, payload: dict | None = None) -> None:
    """The line pawl reads: `token`, `token {json}`, or `{json}` alone."""
    parts = ([token] if token else []) + ([json.dumps(payload, ensure_ascii=False)] if payload is not None else [])
    sys.stdout.write(" ".join(parts) + "\n")


def _rejected(f: Failure) -> int:
    emit({"ok": False, "errors": f.errors})
    return 1


# --- 0. floor, 1. fetch -------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def step_floor(master: str, limit: str, summary_guide: str | None = None, max_bullets: str | None = None) -> int:
    n = _int(limit, "LIMIT")
    _max_bullets(max_bullets)  # pawl does not range-check integer args: block here, before any agent
    # A summary guide file turns the written summary on (WP17), and is pinned like the master. An invalid
    # one is exit 1: the run blocks here, before any fetch or agent, with the guide's errors.
    guide_sha = section = ""
    if summary_guide and Path(summary_guide).is_file():
        guide = load_guide(Path(summary_guide), load_master(Path(master)))
        guide_sha, section = _sha256(Path(summary_guide)), guide.section
    # Every draft must keep the guide's section (`summary_section_missing`), so the floor counts it.
    result = pin_floor(Path(master), n, section or None)
    emit(result)
    # The master is pinned from here on (`pinned`): agents must not change what the CV is built from.
    line = f"{'fits' if result['fits'] else 'too_long'} master_sha256={_sha256(Path(master))}"
    if summary_guide is not None:
        line += f" guide_sha256={guide_sha} summary_section={section}"
    _final(line)
    return 0


def _source_url(posting: str, posting_url: str) -> str:
    """The posting's web address for the review page: `posting_url` (already checked), else `posting` if it
    is a valid http(s) URL, else "" (a bad `posting` URL is fetch's `unusable`, not this)."""
    if posting_url:
        return posting_url
    try:
        check_url(posting)
    except Failure:
        return ""
    return posting


def _is_url(posting: str) -> bool:
    """Route `posting` as a URL when its scheme is http(s), in any case (as `check_url` reads it)."""
    return urlsplit(posting).scheme.lower() in ("http", "https")


def _carry_inputs(a: Path, from_selection: str, posting: str, posting_url: str) -> None:
    """A carried run (`from_selection`) reuses the earlier run's posting.txt and requirements.json, so the
    requirements, and so the recruiter's gate, are the ones its selection was reviewed against."""
    old = Path(from_selection).parent
    missing = [p for p in (Path(from_selection), old / "posting.txt", old / "requirements.json") if not p.is_file()]
    if missing:
        raise Failure(
            1,
            [
                {
                    "code": "carry_source_invalid",
                    "id": str(p),
                    "message": f"`{p}` is missing: from_selection= must name the selection.json of an earlier run's"
                    " application folder, next to its posting.txt and requirements.json.",
                }
                for p in missing
            ],
        )
    record, url = old / "posting-url.txt", _source_url(posting, posting_url)
    was = record.read_text(encoding="utf-8").strip() if record.is_file() else ""
    if url and was and url != was:
        raise Failure(
            1,
            [
                {
                    "code": "carry_posting_mismatch",
                    "id": str(record),
                    "message": f"from_selection= is from a run for {was}, but posting is {url}. A carried run reuses"
                    " that run's posting and requirements: pass the same posting, or start a run without from_selection.",
                }
            ],
        )
    app_dir(old / "requirements.json", APPLICATIONS, date.today().isoformat())  # requirements_invalid if not usable
    shutil.copyfile(old / "posting.txt", a / "posting.txt")
    shutil.copyfile(old / "requirements.json", a / "requirements.json")


def step_fetch(posting: str, run_id: str, posting_url: str = "", master: str = "", from_selection: str = "") -> int:
    if posting_url:
        try:
            check_url(posting_url, "posting_url=")
        except Failure as f:  # exit 1: the run blocks here; a run keeps its arguments, so resuming can't fix it
            for e in f.errors:
                e["message"] += " A run keeps its arguments: start again with `pawl run tailor --fresh posting=… posting_url=<fixed>`."
            raise
    name = f".tmp-{run_id}"
    a = APPLICATIONS / name
    a.mkdir(parents=True, exist_ok=True)
    (a / MARKER).write_text(name, encoding="utf-8")
    # A/posting-url.txt (one line) is the review page's link to the posting; a re-run without a URL drops
    # an earlier attempt's, so the same arguments always leave the same folder.
    url, record = _source_url(posting, posting_url), a / "posting-url.txt"
    if url:
        _write_atomic(record, url + "\n")
    else:
        record.unlink(missing_ok=True)
    if master:
        # The master this run is built from (pinned from here on), so a later run can carry this one's selection
        # onto an edited master (`from_selection`): carry matches items by their text in this copy.
        shutil.copyfile(master, a / SNAPSHOT)
    if from_selection:
        _carry_inputs(a, from_selection, posting, posting_url)
        _final("carried", {"app_dir": str(a)})
        return 0
    try:
        if _is_url(posting):
            emit(fetch_posting(posting, None, a / "posting.txt"))
        else:
            emit(fetch_posting(None, Path(posting), a / "posting.txt"))
    except Failure as f:
        if f.code == 3:
            token = "fetch_failed"
        elif all(e["code"] in UNUSABLE for e in f.errors):
            token = "unusable"
        else:
            raise
        emit({"ok": False, "errors": f.errors})
        _final(token, {"app_dir": str(a)})
        return 0
    _final("fetched", {"app_dir": str(a)})
    return 0


def step_posting_ok(a: str) -> int:
    with tempfile.TemporaryDirectory(prefix="cv-skills-") as work:
        emit(fetch_posting(None, Path(a) / "posting.browser.txt", Path(work) / "posting.txt"))
    return 0


def step_use_browser_text(a: str) -> int:
    emit(fetch_posting(None, Path(a) / "posting.browser.txt", Path(a) / "posting.txt"))
    _final(payload={})
    return 0


def step_use_file(path: str, a: str) -> int:
    emit(fetch_posting(None, Path(path), Path(a) / "posting.txt"))
    _final(payload={})
    return 0


# --- 2. requirements, folder name ----------------------------------------------


def step_check_requirements(a: str) -> int:
    emit(app_dir(Path(a) / "requirements.json", APPLICATIONS, date.today().isoformat()))
    return 0


def _named(folder: str, token: str | None) -> None:
    # requirements.json is pinned from here on (`pinned`), so the recruiter's gate cannot move.
    _final(token, {"app_dir": folder, "requirements_sha256": _sha256(Path(folder) / "requirements.json")})


def step_name_folder(a: str, from_selection: str | None = None) -> int:
    """Names the folder; with FROM_SELECTION (the workflow arg, "" = none) routes to `draft` or `carry`."""
    token = None if from_selection is None else "carry" if from_selection else "draft"
    path = Path(a)
    if not path.name.startswith(".tmp-"):
        _named(a, token)  # already named
        return 0
    if path.exists():
        target = app_dir(path / "requirements.json", path.parent, date.today().isoformat())["dir"]
        os.rename(path, target)
        _named(target, token)
        return 0
    # A re-run after a crash between the rename and pawl recording it: the marker names the old folder.
    for d in sorted(path.parent.iterdir()) if path.parent.is_dir() else []:
        marker = d / MARKER
        if d.is_dir() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == path.name:
            _named(str(d), token)
            return 0
    raise Failure(
        1,
        [
            {
                "code": "app_dir_missing",
                "id": a,
                "message": f"Application folder `{a}` does not exist and no folder in `{path.parent}` came from it.",
            }
        ],
    )


# --- 3-4. draft -----------------------------------------------------------------


def _too_long(pc: dict, message: str) -> int:
    """Exit 1 with a `page_limit` error (what the agent is told to fix) and the page-check numbers."""
    emit(
        {
            "ok": False,
            "errors": [{"code": "page_limit", "id": "", "message": message}],
            **{k: pc[k] for k in ("pages", "limit", "last_page_remaining_mm", "last_page_fill_ratio")},
        }
    )
    return 1


def _max_bullets(value: str | None) -> int | None:
    """MAX_BULLETS: an integer >= 1, or None (not passed: no ceiling)."""
    if value is None:
        return None
    n = _int(value, "MAX_BULLETS")
    if n < 1:
        raise _usage(f"MAX_BULLETS must be at least 1, got `{value}`.")
    return n


def _too_many_highlights(a: str, max_bullets: int | None) -> list[dict]:
    """One `too_many_highlights` error per entry listing more than `max_bullets` highlights (WP20): no one role takes
    all the space. An unreadable selection.json is left to `build`, which reports it."""
    if max_bullets is None:
        return []
    try:
        sel = json.loads((Path(a) / "selection.json").read_text(encoding="utf-8"))
        entries = [e for s in sel["sections"] for e in s["entries"]]
    except (OSError, ValueError, TypeError, KeyError):
        return []
    errors = []
    for e in entries:
        hl = e.get("highlights") if isinstance(e, dict) else None
        if isinstance(hl, list) and len(hl) > max_bullets:
            errors.append(
                {
                    "code": "too_many_highlights",
                    "id": str(e.get("id", "")),
                    "message": f"`{e.get('id')}` lists {len(hl)} bullets; the limit is at most {max_bullets} per entry."
                    f" Keep the {max_bullets} strongest for this posting and drop the rest.",
                }
            )
    return errors


def _check_selection(a: str, section: str, max_bullets: str | None = None) -> None:
    """Checks on selection.json beyond `build`'s, exit 1 so the agent fixes them. With a summary guide in use, the
    selection must keep its section (WP19): the user chose a written summary, so a draft may not drop it to save
    space. No entry may list more than MAX_BULLETS highlights (WP20)."""
    ceiling = _max_bullets(max_bullets)
    errors = []
    if section and not _section_selected(a, section):
        errors.append(
            {
                "code": "summary_section_missing",
                "id": section,
                "message": f"selection.json has no `{section}` section, but a summary guide is in use: keep the"
                f" `{section}` section (the written summary goes there) and drop other content to fit the page.",
            }
        )
    errors += _too_many_highlights(a, ceiling)
    if errors:
        raise Failure(1, errors)


def step_check_draft(
    a: str, master: str, limit: str, request: str | None = None, section: str = "", max_bullets: str | None = None
) -> int:
    """The draft's postcondition, and apply_changes' with REQUEST: the summary_request it reported, which must be
    a known value (exit 1, so the agent retries, rather than a run blocked later in build-changes). The draft step
    passes REQUEST `none`. SECTION is the summary guide's section ("" = no guide), which the selection must keep.
    MAX_BULLETS (>= 1) caps each entry's highlights; not passed, there is no cap."""
    n = _int(limit, "LIMIT")
    _check_selection(a, section, max_bullets)
    if request is not None and request not in ("none", *_SUMMARY_REQUESTS):
        raise Failure(
            1,
            [
                {
                    "code": "summary_request_invalid",
                    "id": "summary_request",
                    "message": f"summary_request is `{request}`; return one of \"none\", \"rewrite\", \"original\" or"
                    ' "written" (exactly, lowercase).',
                }
            ],
        )
    with tempfile.TemporaryDirectory(prefix="cv-skills-") as work:
        out = Path(work) / "tailored.yaml"
        build(Path(master), Path(a) / "selection.json", out)
        pc = page_check(out, n)
    if not pc["fits"]:
        return _too_long(
            pc,
            f"The CV is {pc['pages']} pages; the limit is {n}. Drop or shorten content"
            " (bullets, entries, skills items) until page-check says fits.",
        )
    emit(pc)
    return 0


def _cv_text(tailored: Path, section: str) -> str:
    """cv.txt as `cv-text` writes it, without the written summary's `section` (never recruiter evidence)."""
    lines = [strip_markup(line) for line in cv_lines(load_tailored(tailored), exclude_section=section or None)]
    return "\n".join(lines) + "\n"


def cv_text_sha(tailored: Path, section: str) -> str:
    """The hash of the text the recruiter reads for `tailored`, computed from the YAML (not from cv.txt, which
    agents could write), so the workflow knows whether a review graded the CV now under review."""
    return hashlib.sha256(_cv_text(tailored, section).encode("utf-8")).hexdigest()


def _cv_txt(app: Path, tailored: Path, section: str) -> None:
    _write_atomic(app / "cv.txt", _cv_text(tailored, section))


def _build_draft(a: str, master: str, limit: str, section: str = "", max_bullets: str | None = None) -> None:
    """tailored.yaml, page-check.json and cv.txt in `a`, as `build`, `page-check` and `cv-text` write them."""
    n = _int(limit, "LIMIT")
    app = Path(a)
    _check_selection(a, section, max_bullets)  # the postcondition checked it; this step never builds without it
    emit(build(Path(master), app / "selection.json", app / "tailored.yaml"))
    pc = page_check(app / "tailored.yaml", n)
    _write_atomic(app / "page-check.json", json.dumps(pc, ensure_ascii=False) + "\n")
    emit(pc)
    _cv_txt(app, app / "tailored.yaml", section)


_ROUND = re.compile(r"review\.round([0-9]+)\.json")


def _rounds(app: Path) -> dict[int, Path]:
    """The set-aside reviews in `app`, by round number."""
    return {int(m[1]): p for p in app.glob("review.round*.json") if (m := _ROUND.fullmatch(p.name))}


def _set_aside_review(app: Path) -> None:
    """Rename `review.json` to the next `review.round<N>.json`, so the recruiter that runs next always
    writes a new file and never sees a stale review. The round files stay as the run's history."""
    review = app / "review.json"
    if review.exists():
        os.rename(review, app / f"review.round{max(_rounds(app), default=0) + 1}.json")


def _latest_review(app: Path) -> Path:
    """`review.json`, else the last set-aside round (the recruiter ran out of visits after its review was
    set aside). With neither, `review.json`, which check_review reports as `review_invalid`."""
    review = app / "review.json"
    found = _rounds(app)
    return found[max(found)] if not review.exists() and found else review


def step_build_draft(a: str, master: str, limit: str, section: str = "", max_bullets: str | None = None) -> int:
    _build_draft(a, master, limit, section, max_bullets)
    _set_aside_review(Path(a))  # the recruiter always runs next
    _final(payload={})
    return 0


# --- 5-6. review ----------------------------------------------------------------


def _check(
    a: str,
    review: Path,
    out: Path,
    redrafts_done: int = 0,
    prev_supported: int | None = None,
    section: str = "",
    tailored: Path | None = None,
) -> dict:
    app = Path(a)
    result = check_review(
        review,
        app / "requirements.json",
        tailored or app / "tailored.yaml",
        out,
        redrafts_done=redrafts_done,
        prev_supported=prev_supported,
        exclude_section=section or None,
    )
    emit(result)
    return result


def step_check_review_shape(a: str, section: str = "") -> int:
    with tempfile.TemporaryDirectory(prefix="cv-skills-") as work:
        _check(a, Path(a) / "review.json", Path(work) / "review.verified.json", section=section)
    return 0


def step_check_review(a: str, redrafts: str, prev: str, after_changes: str, section: str = "") -> int:
    after = _bool(after_changes, "AFTER_CHANGES")
    done = 0 if after else _int(redrafts, "REDRAFTS")
    prev_supported = None if after or done == 0 else _int(prev, "PREV")
    result = _check(a, Path(a) / "review.json", Path(a) / "review.verified.json", done, prev_supported, section)
    outcome = result["outcome"]
    if outcome == "redraft" and after:
        outcome = "human"  # a review after the user's changes goes back to the user, never to the drafter
    line = f"redraft redrafts_done={done + 1} prev_supported={result['supported_must']}" if outcome == "redraft" else outcome
    # The text this review graded: build-changes sends a changed CV back to the recruiter, and the review page
    # says when the review shown is of an earlier version (the gate must describe the CV under review).
    _final(f"{line} reviewed_cv_sha256={cv_text_sha(Path(a) / 'tailored.yaml', section)}")
    return 0


def _summary(verified: dict, pc: dict, stale: bool = False) -> str:
    met, partial = must_split(verified)
    split = f"{met} met, {partial} partial" + (
        f"; unmet: {', '.join(verified['unmet_must'])}" if verified["unmet_must"] else ""
    )
    which = " (from a review of an earlier version of this CV)" if stale else ""
    gate = (
        f"Recruiter gate{which}: {verified['gate']}, {verified['supported_must']} of {verified['total_must']}"
        f" must-haves supported ({split})."
    )
    judge = [r["id"] for r in verified["for_you_to_judge"]]
    if judge:
        gate += f" For you to judge: {', '.join(judge)}."
    pages = f"{pc['pages']} page{'' if pc['pages'] == 1 else 's'}"
    return f"{gate} {pages} (limit {pc['limit']}), {pc['last_page_remaining_mm']:g} mm left on the last page."


_UNFINISHED = "the summary step did not finish"
# The workflow's `summary_status` (set only by deterministic steps) -> why the CV shows its own summary. Each text
# is true whenever that status is the run's: `pending`, `applied` and `none` never reach the page with a guide's
# section and no written summary, so should one get there it claims no more than "did not finish".
SUMMARY_STATUSES = {
    "original": "you chose the CV's original summary",
    "section_dropped": "the summary's section is not in this selection",
    "writer_failed": "the writer could not produce a summary that passes the checks",
    "build_failed": "building the CV with the written summary failed (see the build_summary step's output)",
    "cap_reached": "the summary steps reached their round limit",
    "not_reached": "the run did not reach the summary step (the draft step reached its round limit)",
    "pending": _UNFINISHED,
    "applied": _UNFINISHED,
    "none": _UNFINISHED,
}
_FAILED_STATUSES = ("writer_failed", "build_failed", "cap_reached")  # summary-status REASON


def step_review_page(
    a: str,
    master: str,
    posting: str = "",
    posting_url: str = "",
    summary_guide: str = "",
    applied: str = "",
    section: str = "",
    status: str = "",
    reviewed: str = "",
    carry_sha: str = "",
) -> int:
    """REVIEWED is check-review's `reviewed_cv_sha256` ("" = not known): a review of other text is marked as of an
    earlier version. CARRY_SHA is the carry step's hash of carry.json ("" = not a carried run), shown on the page."""
    app = Path(a)
    stale = bool(reviewed) and reviewed != cv_text_sha(app / "tailored.yaml", section)
    if carry_sha:
        report = app / "carry.json"
        if not report.is_file() or _sha256(report) != carry_sha:
            raise Failure(
                1,
                [
                    {
                        "code": "carry_report_changed",
                        "id": str(report),
                        "message": f"`{report}` is not the report the carry step wrote; it is not shown.",
                    }
                ],
            )
    # The written summary is on the page only once `build-summary` put it in tailored.yaml (`applied`).
    written = _bool(applied, "SUMMARY_APPLIED") if applied else False
    if status and status not in SUMMARY_STATUSES:
        raise _usage(f"STATUS must be one of {', '.join(SUMMARY_STATUSES)}, got `{status}`.")
    summary = {"summary_path": app / "summary.json", "guide_path": Path(summary_guide)} if written else {}
    if section and not written:
        # A guide is in use but the CV shows the master's summary: the page says why, at that section.
        why = SUMMARY_STATUSES.get(status, _UNFINISHED)
        summary["summary_note"] = (section, f"The CV's own summary (no written summary: {why})")
    # The link comes from the run's own arguments (pawl state, which no agent can write), never from
    # A/posting-url.txt: that file is the application's record, and agents can write in its folder.
    # review_page checks the URL (a bad one is url_invalid, never a crash).
    url = _source_url(posting, posting_url) or None
    emit(
        review_page(
            Path(master),
            app / "selection.json",
            app / "tailored.yaml",
            app / "review.verified.json",
            app / "page-check.json",
            app / "review.html",
            url,
            **summary,
            stale_review=stale,
            carry=json.loads((app / "carry.json").read_text(encoding="utf-8")) if carry_sha else None,
        )
    )
    # review_page validated both files, so their keys are there.
    verified = json.loads((app / "review.verified.json").read_text(encoding="utf-8"))
    pc = json.loads((app / "page-check.json").read_text(encoding="utf-8"))
    _final(payload={"summary": _summary(verified, pc, stale), "review_html": str((app / "review.html").resolve())})
    return 0


# --- 3b. carry: a fresh run that starts from an earlier run's selection (`from_selection`) ----------


_CARRY_COUNTS = (  # carry.json key, then the note's words for one and for more
    ("changed", "item changed in the master", "items changed in the master"),
    ("dropped", "item not carried", "items not carried"),
    ("swaps_dropped", "swap not carried", "swaps not carried"),
    ("titles_reset", "title back to the default", "titles back to the default"),
    ("new", "item new in the master", "items new in the master"),
)


def _carry_note(old: str, report: dict) -> str:
    """One line for the review question: what the carry kept, and what it could not."""
    parts = [f"{report['kept']} items kept as reviewed"]
    parts += [f"{len(report[k])} {one if len(report[k]) == 1 else more}" for k, one, more in _CARRY_COUNTS if report[k]]
    return f"Carried from {old}: {', '.join(parts)} (listed on the review page)."


def step_carry(
    a: str,
    master: str,
    from_selection: str,
    limit: str,
    summary_guide: str,
    section: str,
    max_bullets: str,
    changes: str = "",
) -> int:
    """selection.json (and summary.json, with a guide) carried from FROM_SELECTION onto this run's master, and
    carry.json, its report (pinned by `carry_sha256`). The selection is then checked like a draft: `ok` goes to the
    build and the recruiter; with check errors, or the user's CHANGES (the workflow arg), `fix` sends a change
    request to apply_changes. Never a new draft: what the user reviewed stays as it was unless the master changed it."""
    n = _int(limit, "LIMIT")
    app, src = Path(a), Path(from_selection)
    sel = read_selection(src)
    new = load_master(Path(master))
    old = old_master(src.parent, Path(master), sel.master_hash)
    old_summary = src.parent / "summary.json"
    sentences = load_summary(old_summary) if section and old_summary.is_file() else None
    carried = carry_selection(old, new, sel, sentences)
    _write_atomic(app / "selection.json", json.dumps(carried.selection, ensure_ascii=False, indent=2) + "\n")
    if carried.summary is not None:
        _write_atomic(app / "summary.json", json.dumps(carried.summary, ensure_ascii=False, indent=2) + "\n")
    else:
        (app / "summary.json").unlink(missing_ok=True)
    report = {"from": str(src.parent), **carried.report}
    _write_atomic(app / "carry.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    emit(report)
    errors: list[dict] = []
    try:
        _check_selection(a, section, max_bullets)
    except Failure as f:
        errors += f.errors
    try:
        with tempfile.TemporaryDirectory(prefix="cv-skills-") as work:
            out = Path(work) / "tailored.yaml"
            build(Path(master), app / "selection.json", out)
            pc = page_check(out, n)
        if not pc["fits"]:
            errors.append({"code": "page_limit", "id": "", "message": f"The CV is {pc['pages']} pages; the limit is {n}."})
    except Failure as f:
        errors += f.errors
    request = ""
    if errors:
        emit({"ok": False, "errors": errors})
        request = (
            "The master changed since this selection was reviewed, and it no longer passes the checks. Make the"
            " smallest change to selection.json that fixes these errors, and change nothing else: "
            + " ".join(f"[{e['code']}] {e['id']}: {e['message']}" for e in errors)
        )
    if changes:
        request = f"{request} Then, as the user asked: {changes}" if request else changes
    payload = {"after_changes": True, "carry_sha256": _sha256(app / "carry.json"), "carry_note": _carry_note(str(src.parent), report)}
    if request:
        payload["change_request"] = request
    _final("fix" if request else "ok", payload)
    return 0


# --- 9. changes, 10. final render ------------------------------------------------


# apply_changes reports only what a request asked of the summary; the run's choice changes only then.
_SUMMARY_REQUESTS = {"rewrite": "rewrite", "original": "original", "written": "keep"}


def step_build_changes(
    a: str,
    master: str,
    limit: str,
    rerun: str,
    section: str = "",
    choice: str = "",
    request: str = "",
    max_bullets: str | None = None,
    reviewed: str | None = None,
) -> int:
    """REVIEWED is the `reviewed_cv_sha256` of the last recruiter review ("" = none yet). Whenever the changes
    altered the text the recruiter reads, the recruiter grades the CV again (`rerun`): re-verifying an old review's
    quotes would leave its statuses about content no longer under review."""
    again = _bool(rerun, "RERUN")
    # apply_changes' postcondition (check-draft ... REQUEST) rejects an unknown request; should one get here, it is
    # "none" (the choice stands), never a run blocked on a usage error.
    _build_draft(a, master, limit, section, max_bullets)
    if reviewed is not None and reviewed != cv_text_sha(Path(a) / "tailored.yaml", section):
        again = True
    if again:
        _set_aside_review(Path(a))  # only then does the recruiter run next
    # pawl keeps an agent's last rerun_review when a later return omits it: reset it once used.
    line = f"{'rerun' if again else 'recheck'} after_changes=true rerun_review=false"
    if choice:
        # "none" keeps the earlier choice, so an unrelated change round never undoes "keep the original".
        line += f" summary_choice={_SUMMARY_REQUESTS.get(request, choice)} summary_request=none"
    _final(line)
    return 0


def step_recheck(a: str, section: str = "") -> int:
    _check(a, _latest_review(Path(a)), Path(a) / "review.verified.json", section=section)
    _final(payload={})
    return 0


# --- the written summary (WP17): after the review settles, before the review page -------


def _section_selected(a: str, section: str) -> bool:
    try:
        sel = json.loads((Path(a) / "selection.json").read_text(encoding="utf-8"))
        return any(isinstance(s, dict) and s.get("id") == section for s in sel["sections"])
    except (OSError, ValueError, TypeError, KeyError):
        return True  # unreadable: the summary steps report it


def step_summary_route(a: str, master: str, summary_guide: str, section: str, choice: str) -> int:
    """`none` (no guide, or its section dropped) or `original` (the user's choice) -> the review page with the
    master's summary; `keep` -> build the current summary.json, which still checks against the selection;
    `write` -> the writer. Every route clears `summary_applied`: tailored.yaml has no written summary yet.
    It sets `summary_status` (the review page's reason): none, section_dropped, original, or pending (keep/write).
    On `keep`/`write` it pins selection.json (`selection_sha256`) for build-summary; unless the user asked for a
    rewrite, it clears `summary_notes`, which are only ever the notes of that request."""
    status = "pending"
    if not section:
        token = status = "none"
    elif not _section_selected(a, section):  # defensive: check-draft and the build steps reject such a selection
        token, status = "none", "section_dropped"
    elif choice == "original":
        token = status = "original"
    elif choice == "rewrite" or not (Path(a) / "summary.json").is_file():
        token = "write"
    else:
        try:
            emit(summary_check(Path(master), Path(a) / "selection.json", Path(summary_guide), Path(a) / "summary.json"))
            token = "keep"
        except Failure as f:  # e.g. a change dropped a cited source: write it again
            emit({"ok": False, "errors": f.errors})
            token = "write"
    line = f"{token} summary_applied=false summary_status={status}"
    if token in ("keep", "write"):
        sel = Path(a) / "selection.json"
        line += f" selection_sha256={_sha256(sel) if sel.is_file() else ''}"
    if choice != "rewrite":
        line += " summary_notes="
    _final(line)
    return 0


def step_summary_check(a: str, master: str, summary_guide: str, limit: str | None = None) -> int:
    """write_summary's postcondition. With LIMIT, the CV built with the summary (aside) must also fit: a summary that
    pushes it over is sent back to the writer, the retry carrying why."""
    n = None if limit is None else _int(limit, "LIMIT")
    app = Path(a)
    emit(summary_check(Path(master), app / "selection.json", Path(summary_guide), app / "summary.json"))
    if n is None:
        return 0
    with tempfile.TemporaryDirectory(prefix="cv-skills-") as work:
        out = Path(work) / "tailored.yaml"
        build(Path(master), app / "selection.json", out, app / "summary.json", Path(summary_guide))
        pc = page_check(out, n)
    if not pc["fits"]:
        return _too_long(
            pc,
            f"The CV with this summary is {pc['pages']} pages; the limit is {n}. Shorten the summary (fewer or"
            " shorter sentences) until the CV fits, checking with build --summary and page-check.",
        )
    emit(pc)
    return 0


_BUILT = ("review.verified.json", "cv.txt", "page-check.json", "tailored.yaml")  # tailored.yaml last


def step_build_summary(
    a: str, master: str, limit: str, summary_guide: str, section: str, selection_sha: str = ""
) -> int:
    """tailored.yaml with the written summary, page-check.json and cv.txt (summary excluded), and the review
    re-verified against it (so the review page is not stale): `ok`. Everything is built and checked in a work
    folder inside `a` first, then moved into place: any failure, or a page that no longer fits (`too_long`, back
    to the writer; write_summary's postcondition makes it rare), leaves the folder with the draft as it was.
    With SELECTION_SHA (summary-route's pin), a selection.json changed since is `selection_changed` (exit 1)."""
    n = _int(limit, "LIMIT")
    app = Path(a)
    sel = app / "selection.json"
    if selection_sha and (not sel.is_file() or _sha256(sel) != selection_sha):
        raise Failure(
            1,
            [
                {
                    "code": "selection_changed",
                    "id": str(sel),
                    "message": f"`{sel}` changed after the summary step started; the written summary was checked"
                    " against the selection as it was. The review page shows the CV's own summary.",
                }
            ],
        )
    with tempfile.TemporaryDirectory(prefix=".cv-skills-", dir=app) as work:
        w = Path(work)
        out = w / "tailored.yaml"
        emit(build(Path(master), sel, out, app / "summary.json", Path(summary_guide)))
        pc = page_check(out, n)
        emit(pc)
        if not pc["fits"]:
            _final("too_long")
            return 0
        _write_atomic(w / "page-check.json", json.dumps(pc, ensure_ascii=False) + "\n")
        _cv_txt(w, out, section)
        _check(a, _latest_review(app), w / "review.verified.json", section=section, tailored=out)
        for name in _BUILT:
            os.replace(w / name, app / name)
    # A rewrite asked for in a change round is done: later rounds keep this summary unless asked again, and
    # the notes of that request are spent.
    _final("ok summary_applied=true summary_status=applied summary_choice=keep summary_notes=")
    return 0


def step_summary_status(reason: str) -> int:
    """The `summary_status` a summary step's failure or visit cap leaves (`writer_failed`, `build_failed` or
    `cap_reached`), routed to by the workflow; the review page's note says it. Any other REASON is `usage`."""
    if reason not in _FAILED_STATUSES:
        raise _usage(f"REASON must be one of {', '.join(_FAILED_STATUSES)}, got `{reason}`.")
    _final(payload={"summary_status": reason})
    return 0


def step_final_render(a: str) -> int:
    emit(final_render(Path(a) / "tailored.yaml", Path(a)))
    _final(payload={})
    return 0


def step_pinned(
    master: str, master_sha: str, a: str, requirements_sha: str, summary_guide: str = "", guide_sha: str = ""
) -> int:
    """The workflow's invariant: the master, requirements.json and the summary guide (if in use) still have the
    hashes recorded in pawl's state (which no agent can write). An empty hash is not recorded yet (or no guide)."""
    errors = []
    pins = [(Path(master), master_sha), (Path(a) / "requirements.json", requirements_sha)]
    if guide_sha:
        pins.append((Path(summary_guide), guide_sha))
    for path, want in pins:
        if want and (not path.is_file() or _sha256(path) != want):
            errors.append(
                {
                    "code": "input_changed",
                    "id": str(path),
                    "message": f"`{path}` changed (or vanished) during this run; the CV and the recruiter's gate are"
                    " checked against the version the run started with. Restore it, or start a fresh run.",
                }
            )
    if errors:
        raise Failure(1, errors)
    return 0


def _adapter(fn: Callable[..., int]) -> Callable[[list[str]], int]:
    """`fn(*args)` behind an argument-count check (a wrong count is `usage`); parameters with a default
    are optional trailing arguments."""
    params = list(inspect.signature(fn).parameters.values())
    required = sum(p.default is inspect.Parameter.empty for p in params)
    names = " ".join(p.name.upper() if p.default is inspect.Parameter.empty else f"[{p.name.upper()}]" for p in params)

    def run(args: list[str]) -> int:
        if not required <= len(args) <= len(params):
            count = str(len(params)) if required == len(params) else f"{required} to {len(params)}"
            raise _usage(f"expected {count} argument(s) ({names}), got {len(args)}.")
        return fn(*args)

    run.__doc__ = fn.__doc__
    return run


# Adapter name (as in `scripts/tailor/step.sh <name>`) -> handler(args) -> int.
ADAPTERS: dict[str, Callable[[list[str]], int]] = {
    name: _adapter(fn)
    for name, fn in {
        "floor": step_floor,
        "fetch": step_fetch,
        "posting-ok": step_posting_ok,
        "use-browser-text": step_use_browser_text,
        "use-file": step_use_file,
        "check-requirements": step_check_requirements,
        "name-folder": step_name_folder,
        "check-draft": step_check_draft,
        "carry": step_carry,
        "build-draft": step_build_draft,
        "check-review-shape": step_check_review_shape,
        "check-review": step_check_review,
        "review-page": step_review_page,
        "build-changes": step_build_changes,
        "recheck": step_recheck,
        "final-render": step_final_render,
        "pinned": step_pinned,
        "summary-route": step_summary_route,
        "summary-check": step_summary_check,
        "build-summary": step_build_summary,
        "summary-status": step_summary_status,
    }.items()
}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        if not argv or argv[0] not in ADAPTERS:
            got = f"`{argv[0]}`" if argv else "nothing"
            raise _usage(f"cv_skills.steps: unknown step {got}; expected one of {', '.join(ADAPTERS)}.")
        return ADAPTERS[argv[0]](argv[1:])
    except Failure as f:
        return _rejected(f)
    except Exception as e:
        msg = " ".join(str(e).split())
        emit(
            {
                "ok": False,
                "errors": [
                    {
                        "code": "internal_error",
                        "id": "",
                        "message": f"{type(e).__name__}: {msg} (a cv-skills bug; please report)",
                    }
                ],
            }
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
