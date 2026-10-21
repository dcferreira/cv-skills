"""`cv-skills <subcommand>`: the deterministic scripts behind the tailor workflow.

Contract: JSON on stdout, exit codes 0 (ok), 1 (drafter-fixable rejection),
2 (unusable input), 3 (URL fetch failed: browser fallback). Errors are `{"ok": false, "errors": [{code, id, message}]}`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path


class Failure(Exception):
    """A rejection: `main` emits `{"ok": false, "errors": errors}` and returns `code`."""

    def __init__(self, code: int, errors: list[dict]):
        super().__init__(errors)
        self.code = code
        self.errors = errors


def emit(obj: dict) -> None:
    """Write one JSON object to stdout (UTF-8 text kept as is)."""
    text = json.dumps(obj, ensure_ascii=False)
    # Never crash on output: escape anything UTF-8 cannot encode (lone surrogates).
    text = text.encode("utf-8", "backslashreplace").decode("utf-8")
    sys.stdout.write(text + "\n")


class JsonArgumentParser(argparse.ArgumentParser):
    """Usage errors become the JSON error object (code `usage`, exit 2); `--help` is unchanged.

    Subparsers inherit this class (argparse uses the parent's class by default).
    """

    def error(self, message: str):  # type: ignore[override]
        emit(
            {
                "ok": False,
                "errors": [{"code": "usage", "id": "", "message": f"{self.prog}: {message}. See `{self.prog} --help`."}],
            }
        )
        sys.exit(2)


def cmd_ids(args: argparse.Namespace) -> int:
    from cv_skills.master import ids_listing, load_master

    emit(ids_listing(load_master(Path(args.master))))
    return 0


def _written_paths(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    """`--summary` and `--guide`: both or neither, else `usage` (checked before anything is read)."""
    if (args.summary is None) != (args.guide is None):
        given, missing = ("--summary", "--guide") if args.guide is None else ("--guide", "--summary")
        raise Failure(
            2,
            [{"code": "usage", "id": "", "message": f"`{given}` needs `{missing}` too: pass both (a written summary) or neither."}],
        )
    return (Path(args.summary), Path(args.guide)) if args.summary is not None else (None, None)


def cmd_build(args: argparse.Namespace) -> int:
    from cv_skills.build import build

    emit(build(Path(args.master), Path(args.selection), Path(args.out), *_written_paths(args)))
    return 0


def cmd_guide_check(args: argparse.Namespace) -> int:
    from cv_skills.summary import guide_check

    emit(guide_check(Path(args.guide), Path(args.master)))
    return 0


def cmd_summary_check(args: argparse.Namespace) -> int:
    from cv_skills.summary import summary_check

    emit(summary_check(Path(args.master), Path(args.selection), Path(args.guide), Path(args.summary)))
    return 0


def cmd_fetch_posting(args: argparse.Namespace) -> int:
    from cv_skills.fetch import fetch_posting

    emit(fetch_posting(args.url, Path(args.file) if args.file else None, Path(args.out)))
    return 0


def cmd_cv_text(args: argparse.Namespace) -> int:
    from cv_skills.cvtext import cv_lines, load_tailored, strip_markup
    from cv_skills.outfile import check_out, write_out

    out = Path(args.out)
    check_out(out, {"--tailored": Path(args.tailored)})
    doc = load_tailored(Path(args.tailored))
    lines = [strip_markup(line) for line in cv_lines(doc, exclude_section=args.exclude_section)]
    write_out(out, "\n".join(lines) + "\n")
    emit({"ok": True, "out": str(out), "lines": len(lines)})
    return 0


def cmd_check_review(args: argparse.Namespace) -> int:
    from cv_skills.review import check_review

    emit(
        check_review(
            Path(args.review),
            Path(args.requirements),
            Path(args.tailored),
            Path(args.out),
            redrafts_done=args.redrafts_done,
            prev_supported=args.prev_supported,
            exclude_section=args.exclude_section,
        )
    )
    return 0


def cmd_review_page(args: argparse.Namespace) -> int:
    from cv_skills.reviewpage import review_page

    summary_path, guide_path = _written_paths(args)
    emit(
        review_page(
            Path(args.master),
            Path(args.selection),
            Path(args.tailored),
            Path(args.review),
            Path(args.page_check),
            Path(args.out),
            args.posting_url,
            summary_path=summary_path,
            guide_path=guide_path,
        )
    )
    return 0


def cmd_page_check(args: argparse.Namespace) -> int:
    from cv_skills.pagecheck import limit_error, page_check

    if not (args.limit.isascii() and args.limit.isdigit() and len(args.limit) <= 9):
        raise limit_error(args.limit)
    emit(page_check(Path(args.yaml), int(args.limit)))
    return 0


def cmd_final_render(args: argparse.Namespace) -> int:
    from cv_skills.finalrender import final_render

    emit(final_render(Path(args.yaml), Path(args.out_dir)))
    return 0


def cmd_source_check(args: argparse.Namespace) -> int:
    from cv_skills.sourcecheck import source_check

    emit(source_check(Path(args.master), Path(args.source)))
    return 0


def cmd_check_deps(args: argparse.Namespace) -> int:
    from cv_skills.deps import check_deps

    emit(check_deps())
    return 0


def cmd_app_dir(args: argparse.Namespace) -> int:
    from cv_skills.appdir import app_dir

    emit(app_dir(Path(args.requirements), Path(args.parent), args.date))
    return 0


def cmd_pin_floor(args: argparse.Namespace) -> int:
    from cv_skills.floor import pin_floor
    from cv_skills.pagecheck import limit_error

    if not (args.limit.isascii() and args.limit.isdigit() and len(args.limit) <= 9):
        raise limit_error(args.limit)
    section = None
    if args.guide is not None:
        from cv_skills.master import load_master
        from cv_skills.summary import load_guide

        section = load_guide(Path(args.guide), load_master(Path(args.master))).section
    emit(pin_floor(Path(args.master), int(args.limit), section))
    return 0


def cmd_install_workflow(args: argparse.Namespace) -> int:
    from cv_skills.install import install_workflow

    emit(install_workflow(Path(args.dest)))
    return 0


# Subcommand name -> handler(args) -> int. Later tasks register theirs here
# (and add the matching subparser in build_parser).
HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "ids": cmd_ids,
    "build": cmd_build,
    "fetch-posting": cmd_fetch_posting,
    "cv-text": cmd_cv_text,
    "check-review": cmd_check_review,
    "review-page": cmd_review_page,
    "page-check": cmd_page_check,
    "final-render": cmd_final_render,
    "app-dir": cmd_app_dir,
    "pin-floor": cmd_pin_floor,
    "install-workflow": cmd_install_workflow,
    "check-deps": cmd_check_deps,
    "source-check": cmd_source_check,
    "guide-check": cmd_guide_check,
    "summary-check": cmd_summary_check,
}


_SUMMARY_HELP = "Path to summary.json (the written summary); needs --guide. Optional."
_GUIDE_HELP = "Path to the summary guide (summary-guide.md); needs --summary. Optional."
_EXCLUDE_HELP = "A section key to leave out (the written summary's section: never recruiter evidence). Optional."


def _count(value: str) -> int:
    """argparse type: a non-negative integer."""
    if not value.isascii() or not value.isdigit():
        raise argparse.ArgumentTypeError(f"invalid count {value!r} (expected a non-negative integer)")
    return int(value)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="cv-skills",
        description="Deterministic scripts for tailoring a master RenderCV CV.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<subcommand>")

    ids = sub.add_parser(
        "ids",
        help="List every ID in the master and the master hash (JSON).",
        description="List every path ID in the master (0-based) and the master hash, as JSON.",
    )
    ids.add_argument("--master", required=True, help="Path to the master RenderCV YAML.")

    build = sub.add_parser(
        "build",
        help="Build the tailored RenderCV YAML from master + selection (JSON result).",
        description="Write the tailored RenderCV YAML (selected master text plus listed swaps)"
        " and print the result, incl. swap positions, as JSON. Writes nothing on rejection.",
    )
    build.add_argument("--master", required=True, help="Path to the master RenderCV YAML.")
    build.add_argument("--selection", required=True, help="Path to selection.json.")
    build.add_argument("--out", required=True, help="Path to write the tailored YAML to.")
    build.add_argument("--summary", default=None, help=_SUMMARY_HELP + " The guide's section becomes this summary.")
    build.add_argument("--guide", default=None, help=_GUIDE_HELP)

    fetch = sub.add_parser(
        "fetch-posting",
        help="Fetch a job posting (URL or file) as plain text (JSON result).",
        description="Write the posting as plain text to --out and print the result as JSON."
        " Exit 3 (`fetch_failed`) means the URL gave no usable text: use the browser fallback.",
    )
    source = fetch.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="http(s) URL of the posting.")
    source.add_argument("--file", help="File with the posting text (plain text or HTML).")
    fetch.add_argument("--out", required=True, help="Path to write posting.txt to.")

    cv_text = sub.add_parser(
        "cv-text",
        help="Write the tailored CV as text lines, the text the recruiter reads (JSON result).",
        description="Write the tailored CV as one text line per field, bullet, skills item or summary, with"
        " inline Markdown reduced to its text; a quote copied from it verifies with `check-review`."
        " Prints a JSON result.",
    )
    cv_text.add_argument("--tailored", required=True, help="Path to the tailored YAML (from `build`).")
    cv_text.add_argument("--out", required=True, help="Path to write the text to.")
    cv_text.add_argument("--exclude-section", default=None, metavar="KEY", help=_EXCLUDE_HELP)

    check_review = sub.add_parser(
        "check-review",
        help="Verify the recruiter's quotes, apply the gate, say what happens next (JSON result).",
        description="Verify each recruiter quote against the tailored CV (an unverified met/partial becomes"
        " missing), apply the must-have gate and print the outcome: pass, redraft or human."
        " Writes the verified review to --out. A failed gate is still exit 0.",
    )
    check_review.add_argument("--review", required=True, help="Path to review.json (recruiter output).")
    check_review.add_argument("--requirements", required=True, help="Path to requirements.json.")
    check_review.add_argument("--tailored", required=True, help="Path to the tailored YAML (from `build`).")
    check_review.add_argument("--out", required=True, help="Path to write the verified review to.")
    check_review.add_argument(
        "--redrafts-done", type=_count, default=0, help="Redrafts already done (default 0; at most 2 are allowed)."
    )
    check_review.add_argument(
        "--prev-supported",
        type=_count,
        default=None,
        help="Supported must-haves at the previous round; omit on the first round.",
    )
    check_review.add_argument(
        "--exclude-section",
        default=None,
        metavar="KEY",
        help=_EXCLUDE_HELP + " A quote found only in that section is not verified.",
    )

    review_page = sub.add_parser(
        "review-page",
        help="Write the static HTML review page for the human (JSON result).",
        description="Write one self-contained HTML page: the tailored CV with every swap highlighted (original on"
        " hover and in a list of changes), the requirement table, the requirements for you to judge, top fixes,"
        " missed requirements and the"
        " page count / space left. Refuses a --tailored that differs from what `build` writes.",
    )
    review_page.add_argument("--master", required=True, help="Path to the master RenderCV YAML.")
    review_page.add_argument("--selection", required=True, help="Path to selection.json.")
    review_page.add_argument("--tailored", required=True, help="Path to the tailored YAML (from `build`).")
    review_page.add_argument("--review", required=True, help="Path to the verified review (`check-review --out`).")
    review_page.add_argument("--page-check", required=True, help="Path to the `page-check` JSON.")
    review_page.add_argument("--out", required=True, help="Path to write the HTML page to.")
    review_page.add_argument(
        "--posting-url",
        default=None,
        help="The original posting's http(s) URL, linked under the page title (opens in a new tab). Optional.",
    )
    review_page.add_argument("--summary", default=None, help=_SUMMARY_HELP + " As passed to `build`.")
    review_page.add_argument("--guide", default=None, help=_GUIDE_HELP)

    guide = sub.add_parser(
        "guide-check",
        help="Check a summary guide (front matter) against the master (JSON).",
        description="Check the summary guide's front matter (section, max_chars, max_sentences, facts) against the"
        " master and print the settings in effect as JSON. The guide's body is not read. Writes nothing.",
    )
    guide.add_argument("--guide", required=True, help="Path to the summary guide (summary-guide.md).")
    guide.add_argument("--master", required=True, help="Path to the master RenderCV YAML.")

    summary = sub.add_parser(
        "summary-check",
        help="Check a written summary against its sources, the selection and the guide (JSON).",
        description="Check summary.json: every source a selected ID, its entry's title or a guide fact; every"
        " number (digits or a number word) and every word with a capital letter (but an ordinary first word)"
        " found in the cited sources' tailored text; the length and sentence bounds; inline **bold** as the only"
        " markup. Exit 1 lists every problem. Writes nothing.",
    )
    summary.add_argument("--master", required=True, help="Path to the master RenderCV YAML.")
    summary.add_argument("--selection", required=True, help="Path to selection.json.")
    summary.add_argument("--guide", required=True, help="Path to the summary guide (summary-guide.md).")
    summary.add_argument("--summary", required=True, help="Path to summary.json.")

    page_check = sub.add_parser(
        "page-check",
        help="Render a tailored CV and report pages and space left on the last page (JSON).",
        description="Render the tailored RenderCV YAML and print {pages, limit, fits,"
        " last_page_remaining_mm, last_page_fill_ratio} as JSON. Over the limit is not an error:"
        " fits is false.",
    )
    page_check.add_argument("--yaml", required=True, help="Path to the tailored RenderCV YAML (from `build`).")
    page_check.add_argument("--limit", default="2", help="Page limit, an integer >= 1 (default 2).")

    final = sub.add_parser(
        "final-render",
        help="Write cv.pdf and cv.md from the approved tailored YAML after a text-extraction check (JSON).",
        description="Render the tailored RenderCV YAML to PDF and Markdown in --out-dir, but only if every"
        " body string of the CV extracts from the PDF (pdftotext) in order. Writes nothing on failure.",
    )
    final.add_argument("--yaml", required=True, help="Path to the approved tailored RenderCV YAML.")
    final.add_argument("--out-dir", required=True, help="Folder for cv.pdf and cv.md (created if missing).")

    source_check = sub.add_parser(
        "source-check",
        help="Check that the master's text occurs in the CV it was converted from (JSON).",
        description="Check that every body string and entry title field (company, position, institution, area,"
        " degree, name) of the master occurs in the source CV (.tex, .pdf, .yaml or text). Guards a conversion"
        " only; dates and contact fields are not checked.",
    )
    source_check.add_argument("--master", required=True, help="Path to the master RenderCV YAML.")
    source_check.add_argument("--source", required=True, help="Path to the CV the master was converted from.")

    sub.add_parser(
        "check-deps",
        help="Check that RenderCV 2.8, pdftotext and pawl are available (JSON).",
        description="Check RenderCV 2.8, `pdftotext` and `pawl` (on PATH, else in the plugin cache) and print"
        " what was found as JSON. Every missing tool is reported at once (`tool_missing`, exit 2).",
    )

    app_dir = sub.add_parser(
        "app-dir",
        help="Name a new application folder from requirements.json and a date (JSON). Creates nothing.",
        description="Print {dir: <parent>/<date>-<company>-<role>[-N]} as JSON: the first name not yet in --parent."
        " Company and role are lowercase ASCII slugs (`unknown` if empty). Creates nothing.",
    )
    app_dir.add_argument("--requirements", required=True, help="Path to requirements.json.")
    app_dir.add_argument("--parent", required=True, help="Folder that holds the application folders.")
    app_dir.add_argument("--date", required=True, help="Date prefix, YYYY-MM-DD.")

    pin_floor = sub.add_parser(
        "pin-floor",
        help="Check that the pinned entries alone fit the page limit (JSON).",
        description="Render the smallest valid CV (pinned entries without highlights or skills items)"
        " and print {pages, limit, fits} as JSON. Over the limit is not an error: fits is false."
        " With --guide, the summary guide's section is in it too, with its master text.",
    )
    pin_floor.add_argument("--master", required=True, help="Path to the master RenderCV YAML.")
    pin_floor.add_argument("--limit", default="2", help="Page limit, an integer >= 1 (default 2).")
    pin_floor.add_argument(
        "--guide", help="Path to the summary guide (summary-guide.md): every draft keeps its section."
    )

    install = sub.add_parser(
        "install-workflow",
        help="Copy the tailor pawl workflow and its shims into a CV repo (JSON).",
        description="Write tailor.yaml and scripts/tailor/{step,cv}.sh into --dest (a CV repo's"
        " .claude/workflows), with this plugin's root written into the shims. Idempotent.",
    )
    install.add_argument("--dest", required=True, help="The CV repo's .claude/workflows folder.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except Failure as f:
        emit({"ok": False, "errors": f.errors})
        return f.code
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
        return 2


if __name__ == "__main__":
    sys.exit(main())
