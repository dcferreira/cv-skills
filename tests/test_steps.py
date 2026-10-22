import contextlib
import hashlib
import http.server
import io
import json
import shutil
import threading
from datetime import date
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from cv_skills import steps
from cv_skills.master import ids_listing, load_master

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MASTER = REPO / "examples" / "master.yaml"
SELECTION = REPO / "examples" / "selection.json"
POSTING = "Harbourlight Analytics is hiring a data engineer. " * 6  # > 200 chars
A = "applications/.tmp-run1"
TODAY = date.today().isoformat()
FINAL = f"applications/{TODAY}-acme-analytics-senior-data-engineer"

REQS = {
    "company": "Acme Analytics",
    "role": "Senior Data Engineer",
    "requirements": [
        {"id": "r1", "text": "Feature store", "kind": "must", "evidence": "cv"},
        {"id": "r2", "text": "Streaming in Go", "kind": "must", "evidence": "cv"},
        {"id": "r3", "text": "Mentoring", "kind": "nice", "evidence": "cv"},
    ],
}
R1_OK = {"id": "r1", "status": "met", "evidence_quote": "feature store used by four machine-learning teams", "reason": "x"}
R1_BAD = {"id": "r1", "status": "met", "evidence_quote": "invented sentence", "reason": "x"}
R1_MISSING = {"id": "r1", "status": "missing", "evidence_quote": "", "reason": "x"}
R2_OK = {"id": "r2", "status": "partial", "evidence_quote": "streaming ingestion service in Go", "reason": "x"}
R3 = {"id": "r3", "status": "missing", "evidence_quote": "", "reason": "x"}


def run(capsys, *argv) -> tuple[int, str, str]:
    """(exit code, last stdout line, whole stdout)."""
    rc = steps.main([str(a) for a in argv])
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return rc, lines[-1] if lines else "", out


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def named(app_dir: str) -> str:
    """name-folder's payload: the folder and the hash of its requirements.json."""
    return json.dumps({"app_dir": app_dir, "requirements_sha256": sha(Path(app_dir) / "requirements.json")})


def outcome(line: str) -> str:
    """check-review's line without its `reviewed_cv_sha256` (tests/test_steps_carry.py checks that)."""
    return line.split(" reviewed_cv_sha256=")[0]


def last_json(line: str) -> dict:
    return json.loads(line)


def codes(line: str) -> set[str]:
    data = json.loads(line)
    assert data["ok"] is False
    return {e["code"] for e in data["errors"]}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A CV repo (cwd): master.yaml and an applications/ folder."""
    shutil.copy(MASTER, tmp_path / "master.yaml")
    (tmp_path / "applications").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_review(app: Path, *reqs: dict) -> None:
    review = {"requirements": list(reqs), "top_fixes": ["Lead with the feature store"], "missed_requirements": []}
    (app / "review.json").write_text(json.dumps(review))


@pytest.fixture
def app(repo):
    """applications/<final> with selection.json, tailored.yaml (built, no render), requirements.json, a page-check.json."""
    from cv_skills.build import build

    a = repo / FINAL
    a.mkdir()
    shutil.copy(SELECTION, a / "selection.json")
    build(Path("master.yaml"), a / "selection.json", a / "tailored.yaml")
    (a / "requirements.json").write_text(json.dumps(REQS))
    pc = {"ok": True, "pages": 1, "limit": 2, "fits": True, "last_page_remaining_mm": 41.3, "last_page_fill_ratio": 0.8}
    (a / "page-check.json").write_text(json.dumps(pc))
    return a


@pytest.fixture(scope="session")
def drafted(tmp_path_factory) -> Path:
    """A repo whose application folder went through the real `build-draft` (one render, shared)."""
    root = tmp_path_factory.mktemp("drafted")
    shutil.copy(MASTER, root / "master.yaml")
    a = root / FINAL
    a.mkdir(parents=True)
    shutil.copy(SELECTION, a / "selection.json")
    (a / "requirements.json").write_text(json.dumps(REQS))
    with contextlib.chdir(root), contextlib.redirect_stdout(io.StringIO()):
        assert steps.main(["build-draft", FINAL, "master.yaml", "2"]) == 0
    return root


@pytest.fixture
def drafted_repo(drafted, tmp_path, monkeypatch):
    root = tmp_path / "repo"
    shutil.copytree(drafted, root)
    monkeypatch.chdir(root)
    return root


# --- dispatch ---------------------------------------------------------------


def test_adapters_table_lists_every_step():
    assert set(steps.ADAPTERS) == {
        "floor", "fetch", "posting-ok", "use-browser-text", "use-file", "check-requirements", "name-folder",
        "check-draft", "build-draft", "check-review-shape", "check-review", "review-page", "build-changes",
        "recheck", "final-render", "pinned", "summary-route", "summary-check", "build-summary",
        "summary-status", "carry",
    }  # fmt: skip


@pytest.mark.parametrize("argv", [[], ["nope"], ["floor", "master.yaml"], ["recheck", "a", "b", "c"]])
def test_usage_errors(repo, capsys, argv):
    rc, last, _ = run(capsys, *argv)
    assert rc == 1 and codes(last) == {"usage"}


def test_bad_integer_is_usage(repo, capsys):
    rc, last, _ = run(capsys, "floor", "master.yaml", "two")
    assert rc == 1 and codes(last) == {"usage"}


def test_internal_error(repo, capsys, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(steps, "final_render", boom)
    rc, last, _ = run(capsys, "final-render", A)
    assert rc == 1 and codes(last) == {"internal_error"}


# --- floor ------------------------------------------------------------------


def test_floor_fits(repo, capsys):
    rc, last, out = run(capsys, "floor", "master.yaml", "2")
    assert (rc, last) == (0, f"fits master_sha256={sha('master.yaml')}")
    assert json.loads(out.splitlines()[0])["fits"] is True


def test_floor_too_long(repo, capsys, monkeypatch):
    monkeypatch.setattr(steps, "pin_floor", lambda m, n, section=None: {"ok": True, "pages": 3, "limit": n, "fits": False})
    assert run(capsys, "floor", "master.yaml", "2")[:2] == (0, f"too_long master_sha256={sha('master.yaml')}")


def test_floor_bad_master_is_failure(repo, capsys):
    rc, last, _ = run(capsys, "floor", "missing.yaml", "2")
    assert rc == 1 and json.loads(last)["ok"] is False


# --- fetch ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["my posting.txt", "-posting.txt"])
def test_fetch_file(repo, capsys, name):
    (repo / name).write_text(POSTING)
    rc, last, _ = run(capsys, "fetch", name, "run1")
    assert (rc, last) == (0, 'fetched {"app_dir": "applications/.tmp-run1"}')
    assert (repo / A / "posting.txt").read_text() == POSTING.strip() + "\n"
    assert (repo / A / ".cv-skills-run").read_text() == ".tmp-run1"


def test_fetch_rerun_is_idempotent(repo, capsys):
    (repo / "p.txt").write_text(POSTING)
    first = run(capsys, "fetch", "p.txt", "run1")[:2]
    assert run(capsys, "fetch", "p.txt", "run1")[:2] == first == (0, 'fetched {"app_dir": "applications/.tmp-run1"}')
    assert sorted(p.name for p in (repo / "applications").iterdir()) == [".tmp-run1"]


@contextlib.contextmanager
def _serve(body: bytes):
    """A local server answering every GET with `body` as HTML; yields its job URL."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/job"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def js_only_url():
    with _serve((FIXTURES / "posting_js_only.html").read_bytes()) as url:
        yield url


@pytest.fixture
def posting_url():
    with _serve((FIXTURES / "posting.html").read_bytes()) as url:
        yield url


def test_fetch_js_only_url_is_fetch_failed(repo, capsys, js_only_url):
    rc, last, out = run(capsys, "fetch", js_only_url, "run1")
    assert (rc, last) == (0, 'fetch_failed {"app_dir": "applications/.tmp-run1"}')
    assert codes(out.splitlines()[0]) == {"fetch_failed"}
    assert (repo / A).is_dir() and not (repo / A / "posting.txt").exists()


@pytest.mark.parametrize("posting", ["missing.txt", "short.txt", "http://"])
def test_fetch_unusable(repo, capsys, posting):
    (repo / "short.txt").write_text("too short")
    rc, last, out = run(capsys, "fetch", posting, "run1")
    assert (rc, last) == (0, 'unusable {"app_dir": "applications/.tmp-run1"}')
    assert codes(out.splitlines()[0]) <= {"input_unreadable", "posting_empty", "url_invalid"}


def test_fetch_other_failure_is_exit_1(repo, capsys):
    (repo / "p.txt").write_text(POSTING)
    (repo / A / "posting.txt").mkdir(parents=True)  # out_unwritable
    rc, last, _ = run(capsys, "fetch", "p.txt", "run1")
    assert rc == 1 and codes(last) == {"out_unwritable"}



# --- fetch: the posting's URL (A/posting-url.txt) ------------------------------

JOB = "https://jobs.example.com/acme/123"


def url_file(repo) -> Path:
    return repo / A / "posting-url.txt"


def test_fetch_url_records_the_url(repo, capsys, posting_url):
    rc, last, _ = run(capsys, "fetch", posting_url, "run1")
    assert (rc, last) == (0, 'fetched {"app_dir": "applications/.tmp-run1"}')
    assert url_file(repo).read_text() == posting_url + "\n"


def test_fetch_file_with_posting_url_records_it(repo, capsys):
    (repo / "p.txt").write_text(POSTING)
    assert run(capsys, "fetch", "p.txt", "run1", JOB)[:2] == (0, 'fetched {"app_dir": "applications/.tmp-run1"}')
    assert url_file(repo).read_text() == JOB + "\n"


def test_posting_url_wins_over_a_posting_url(repo, capsys, posting_url):
    assert run(capsys, "fetch", posting_url, "run1", JOB)[0] == 0
    assert url_file(repo).read_text() == JOB + "\n"


@pytest.mark.parametrize("extra", [[], [""]])
def test_fetch_file_without_url_records_nothing(repo, capsys, extra):
    (repo / "p.txt").write_text(POSTING)
    assert run(capsys, "fetch", "p.txt", "run1", *extra)[0] == 0
    assert (repo / A / "posting.txt").is_file() and not url_file(repo).exists()


def test_fetch_rerun_without_url_removes_a_stale_one(repo, capsys):
    (repo / "p.txt").write_text(POSTING)
    assert run(capsys, "fetch", "p.txt", "run1", JOB)[0] == 0
    assert run(capsys, "fetch", "p.txt", "run1")[0] == 0
    assert not url_file(repo).exists()


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "jobs.example.com/acme", "https://jobs.example.com/a b"])
def test_fetch_invalid_posting_url_is_exit_1(repo, capsys, bad):
    (repo / "p.txt").write_text(POSTING)
    rc, last, _ = run(capsys, "fetch", "p.txt", "run1", bad)
    assert rc == 1 and codes(last) == {"url_invalid"}
    # a run keeps its arguments, so resuming cannot fix it: the message says to start afresh
    assert "--fresh" in json.loads(last)["errors"][0]["message"]
    assert not url_file(repo).exists()


def test_fetch_routes_an_upper_case_scheme_as_a_url(repo, capsys, posting_url):
    shouting = "HTTP" + posting_url[len("http") :]
    rc, last, _ = run(capsys, "fetch", shouting, "run1")
    assert (rc, last) == (0, 'fetched {"app_dir": "applications/.tmp-run1"}')


def test_fetch_too_many_args_is_usage(repo, capsys):
    rc, last, _ = run(capsys, "fetch", "p.txt", "run1", JOB, "master.yaml", "", "extra")
    assert rc == 1 and codes(last) == {"usage"}
    assert "POSTING RUN_ID [POSTING_URL] [MASTER] [FROM_SELECTION]" in json.loads(last)["errors"][0]["message"]

# --- posting-ok / use-browser-text / use-file -------------------------------


def test_posting_ok(repo, capsys):
    a = repo / A
    a.mkdir()
    rc, last, _ = run(capsys, "posting-ok", A)
    assert rc == 1 and codes(last) == {"input_unreadable"}
    (a / "posting.browser.txt").write_text("short")
    rc, last, _ = run(capsys, "posting-ok", A)
    assert rc == 1 and codes(last) == {"posting_empty"}
    (a / "posting.browser.txt").write_text(POSTING)
    assert run(capsys, "posting-ok", A)[0] == 0
    assert not (a / "posting.txt").exists()


def test_use_browser_text(repo, capsys):
    (repo / A).mkdir()
    (repo / A / "posting.browser.txt").write_text(POSTING)
    assert run(capsys, "use-browser-text", A)[:2] == (0, "{}")
    assert (repo / A / "posting.txt").read_text() == POSTING.strip() + "\n"


def test_use_file(repo, capsys):
    (repo / A).mkdir()
    (repo / "pasted posting.txt").write_text(POSTING)
    assert run(capsys, "use-file", "pasted posting.txt", A)[:2] == (0, "{}")
    assert (repo / A / "posting.txt").exists()
    rc, last, _ = run(capsys, "use-file", "nope.txt", A)
    assert rc == 1 and codes(last) == {"input_unreadable"}


# --- check-requirements / name-folder ---------------------------------------


def test_check_requirements(repo, capsys):
    (repo / A).mkdir()
    rc, last, _ = run(capsys, "check-requirements", A)
    assert rc == 1 and codes(last) == {"requirements_invalid"}
    (repo / A / "requirements.json").write_text(json.dumps({"company": "Acme"}))
    rc, last, _ = run(capsys, "check-requirements", A)
    assert rc == 1 and codes(last) == {"requirements_invalid"}
    (repo / A / "requirements.json").write_text(json.dumps(REQS))
    assert run(capsys, "check-requirements", A)[0] == 0


def _tmp_app(repo) -> Path:
    a = repo / A
    a.mkdir()
    (a / ".cv-skills-run").write_text(".tmp-run1")
    (a / "requirements.json").write_text(json.dumps(REQS))
    return a


def test_name_folder_renames(repo, capsys):
    _tmp_app(repo)
    assert run(capsys, "name-folder", A)[:2] == (0, named(FINAL))
    assert not (repo / A).exists() and (repo / FINAL / "requirements.json").exists()


def test_name_folder_collision(repo, capsys):
    _tmp_app(repo)
    (repo / FINAL).mkdir()
    assert run(capsys, "name-folder", A)[:2] == (0, named(FINAL + "-2"))


def test_name_folder_already_named_is_noop(repo, capsys):
    (repo / FINAL).mkdir()
    (repo / FINAL / "requirements.json").write_text(json.dumps(REQS))
    assert run(capsys, "name-folder", FINAL)[:2] == (0, named(FINAL))
    assert (repo / FINAL).is_dir()


def test_name_folder_recovers_after_crash(repo, capsys):
    _tmp_app(repo)
    assert run(capsys, "name-folder", A)[0] == 0
    # pawl re-runs the step with the old app_dir: the folder was already renamed.
    assert run(capsys, "name-folder", A)[:2] == (0, named(FINAL))
    assert sorted(p.name for p in (repo / "applications").iterdir()) == [Path(FINAL).name]


def test_name_folder_lost(repo, capsys):
    rc, last, _ = run(capsys, "name-folder", A)
    assert rc == 1 and json.loads(last)["ok"] is False


# --- check-draft / build-draft ----------------------------------------------


def test_check_draft_ok(app, capsys):
    rc, last, _ = run(capsys, "check-draft", FINAL, "master.yaml", "2")
    assert rc == 0 and last_json(last)["fits"] is True


def test_check_draft_bad_id(app, capsys):
    sel = json.loads((app / "selection.json").read_text())
    sel["sections"][1]["entries"].append({"id": "experience.9"})
    (app / "selection.json").write_text(json.dumps(sel))
    rc, last, _ = run(capsys, "check-draft", FINAL, "master.yaml", "2")
    assert rc == 1 and "unknown_id" in codes(last)


def test_check_draft_over_limit(app, capsys):
    # A master with many more bullets, all selected: two pages, limit 1.
    yaml = YAML()
    data = yaml.load(MASTER.read_text(encoding="utf-8"))
    extra = len(data["cv"]["sections"]["experience"][0]["highlights"])
    for i in range(80):
        data["cv"]["sections"]["experience"][0]["highlights"].append(f"Filler bullet number {i} about pipelines")
    with Path("long.yaml").open("w", encoding="utf-8") as f:
        yaml.dump(data, f)
    sel = json.loads(SELECTION.read_text())
    sel["master_hash"] = ids_listing(load_master(Path("long.yaml")))["master_hash"]
    sel["sections"][1]["entries"][0]["highlights"] += [f"experience.0.highlights.{extra + i}" for i in range(80)]
    (app / "selection.json").write_text(json.dumps(sel))
    rc, last, _ = run(capsys, "check-draft", FINAL, "long.yaml", "1")
    assert rc == 1
    out = json.loads(last)
    assert out["ok"] is False and [e["code"] for e in out["errors"]] == ["page_limit"]
    assert out["pages"] > 1 and out["limit"] == 1 and "last_page_remaining_mm" in out and "last_page_fill_ratio" in out
    assert f"is {out['pages']} pages; the limit is 1" in out["errors"][0]["message"]
    assert not (app / "tailored.yaml").read_text().count("Filler")  # check-draft builds into a temp dir


def test_build_draft_writes_three_files(drafted_repo):
    a = drafted_repo / FINAL
    assert (a / "tailored.yaml").exists()
    pc = json.loads((a / "page-check.json").read_text())
    assert pc["pages"] == 1 and pc["fits"] is True
    cv = (a / "cv.txt").read_text()
    assert cv.endswith("\n") and "Robin Kestrel" in cv.splitlines()
    assert "feature store used by four machine-learning teams" in cv


def test_build_draft_cv_txt_has_no_markup_and_its_quotes_verify(drafted_repo, capsys):
    a = drafted_repo / FINAL
    text = (a / "cv.txt").read_text()
    assert "**" not in text and "](" not in text
    # experience.0.highlights.0 is `... in **Go** that ...` in the master: quote the stripped line verbatim.
    line = next(l for l in text.splitlines() if l.startswith("Designed a streaming ingestion service in Go"))
    write_review(a, R1_OK, {"id": "r2", "status": "met", "evidence_quote": line, "reason": "x"}, R3)
    rc, last, _ = run(capsys, "check-review", FINAL, "0", "0", "false")
    assert (rc, outcome(last)) == (0, "pass")
    verified = json.loads((a / "review.verified.json").read_text())
    r2 = next(r for r in verified["requirements"] if r["id"] == "r2")
    assert (r2["status"], r2["downgraded"]) == ("met", False)


def test_build_draft_last_line(app, capsys):
    assert run(capsys, "build-draft", FINAL, "master.yaml", "2")[:2] == (0, "{}")


# --- recruiter review -------------------------------------------------------


def test_check_review_shape(app, capsys):
    rc, last, _ = run(capsys, "check-review-shape", FINAL)
    assert rc == 1 and codes(last) == {"review_invalid"}
    write_review(app, R1_OK)
    rc, last, _ = run(capsys, "check-review-shape", FINAL)
    assert rc == 1 and codes(last) == {"review_missing_requirement"}
    write_review(app, R1_BAD, R2_OK, R3)  # a bad quote is not a shape problem
    assert run(capsys, "check-review-shape", FINAL)[0] == 0
    assert not (app / "review.verified.json").exists()


@pytest.mark.parametrize(
    "review, args, expected",
    [
        ((R1_OK, R2_OK, R3), ("0", "0", "false"), "pass"),
        ((R1_BAD, R2_OK, R3), ("0", "0", "false"), "redraft redrafts_done=1 prev_supported=1"),
        ((R1_BAD, R2_OK, R3), ("1", "0", "false"), "redraft redrafts_done=2 prev_supported=1"),
        ((R1_MISSING, R2_OK, R3), ("1", "1", "false"), "human"),  # no progress
        ((R1_BAD, R2_OK, R3), ("2", "0", "false"), "human"),  # redrafts used up
        ((R1_BAD, R2_OK, R3), ("0", "0", "true"), "human"),  # after changes: never redraft
        ((R1_BAD, R2_OK, R3), ("2", "5", "true"), "human"),  # after changes: round 0 (state ignored)
        ((R1_OK, R2_OK, R3), ("0", "0", "true"), "pass"),
    ],
)
def test_check_review(app, capsys, review, args, expected):
    write_review(app, *review)
    rc, last, _ = run(capsys, "check-review", FINAL, *args)
    assert (rc, outcome(last)) == (0, expected)
    assert (app / "review.verified.json").exists()


def test_check_review_bad_flag(app, capsys):
    write_review(app, R1_OK, R2_OK, R3)
    rc, last, _ = run(capsys, "check-review", FINAL, "0", "0", "yes")
    assert rc == 1 and codes(last) == {"usage"}


@pytest.mark.parametrize(
    "review, pc, summary",
    [
        (
            (R1_OK, R2_OK, R3),
            {"pages": 1, "last_page_remaining_mm": 41.3},
            "Recruiter gate: pass, 2 of 2 must-haves supported (1 met, 1 partial). 1 page (limit 2), 41.3 mm left on the last page.",
        ),
        (
            (R1_BAD, R2_OK, R3),
            {"pages": 2, "last_page_remaining_mm": 3.0},
            "Recruiter gate: fail, 1 of 2 must-haves supported (0 met, 1 partial; unmet: r1). 2 pages (limit 2), 3 mm left on the last page.",
        ),
    ],
)
def test_review_page(app, capsys, review, pc, summary):
    full = json.loads((app / "page-check.json").read_text()) | pc
    (app / "page-check.json").write_text(json.dumps(full))
    write_review(app, *review)
    assert run(capsys, "check-review", FINAL, "0", "0", "false")[0] == 0
    rc, last, _ = run(capsys, "review-page", FINAL, "master.yaml")
    assert rc == 0
    out = json.loads(last)
    assert set(out) == {"summary", "review_html"}
    assert out["summary"] == summary
    assert out["review_html"] == str((app / "review.html").resolve())
    assert Path(out["review_html"]).is_file()


def test_review_page_summary_lists_for_you_to_judge(app, capsys):
    reqs = json.loads(json.dumps(REQS))
    reqs["requirements"] += [
        {"id": "r11", "text": "Fluent German", "kind": "must", "evidence": "none"},
        {"id": "r12", "text": "EU work permit", "kind": "nice", "evidence": "none"},
    ]
    (app / "requirements.json").write_text(json.dumps(reqs))
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false")[0] == 0
    rc, last, _ = run(capsys, "review-page", FINAL, "master.yaml")
    assert rc == 0
    assert json.loads(last)["summary"] == (
        "Recruiter gate: pass, 2 of 2 must-haves supported (1 met, 1 partial). For you to judge: r11, r12."
        " 1 page (limit 2), 41.3 mm left on the last page."
    )


def test_review_page_without_verified_review_fails(app, capsys):
    rc, last, _ = run(capsys, "review-page", FINAL, "master.yaml")
    assert rc == 1 and codes(last) == {"review_invalid"}


# The link comes from the run's own arguments (pawl state, which no agent can write), never from
# A/posting-url.txt: agents have Write in the application folder, and a posting can carry an injection.


def _page(app, capsys, *posting_args):
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false")[0] == 0
    rc, last, _ = run(capsys, "review-page", FINAL, "master.yaml", *posting_args)
    html_text = (app / "review.html").read_text(encoding="utf-8") if (app / "review.html").exists() else ""
    return rc, last, html_text


@pytest.mark.parametrize(
    "args, link",
    [
        (["p.txt", JOB], JOB),  # a posting file + posting_url
        ([JOB, ""], JOB),  # a posting URL
        (["https://api.example.com/feed/1", JOB], JOB),  # posting_url wins
    ],
)
def test_review_page_links_the_posting_from_the_run_arguments(app, capsys, args, link):
    rc, _, h = _page(app, capsys, *args)
    assert rc == 0 and f'<p class="posting">Posting: <a href="{link}"' in h


@pytest.mark.parametrize("args", [[], ["p.txt", ""]])
def test_review_page_without_a_url_has_no_link(app, capsys, args):
    rc, _, h = _page(app, capsys, *args)
    assert rc == 0 and 'class="posting"' not in h


def test_review_page_ignores_a_tampered_posting_url_file(app, capsys):
    (app / "posting-url.txt").write_text("https://evil.example/login\n")
    for args in ([], ["p.txt", JOB]):
        rc, _, h = _page(app, capsys, *args)
        assert rc == 0 and "evil.example" not in h


def test_review_page_bad_posting_url_is_exit_1(app, capsys):
    rc, last, _ = _page(app, capsys, "p.txt", "javascript:alert(1)")
    assert rc == 1 and codes(last) == {"url_invalid"}
    assert not (app / "review.html").exists()

# --- changes ----------------------------------------------------------------


@pytest.mark.parametrize("rerun, expected", [("true", "rerun after_changes=true rerun_review=false"), ("false", "recheck after_changes=true rerun_review=false")])
def test_build_changes(app, capsys, rerun, expected):
    rc, last, _ = run(capsys, "build-changes", FINAL, "master.yaml", "2", rerun)
    assert (rc, last) == (0, expected)
    assert (app / "cv.txt").exists() and json.loads((app / "page-check.json").read_text())["pages"] == 1


@pytest.mark.parametrize(
    "choice, request_, expected",
    [
        ("original", "none", "original"),  # an unrelated change round keeps the user's earlier choice
        ("keep", "none", "keep"),
        ("keep", "original", "original"),
        ("original", "written", "keep"),  # back to the written summary
        ("keep", "rewrite", "rewrite"),
    ],
)
def test_build_changes_folds_the_summary_request(app, capsys, choice, request_, expected):
    rc, last, _ = run(capsys, "build-changes", FINAL, "master.yaml", "2", "false", "", choice, request_)
    assert rc == 0
    assert last == f"recheck after_changes=true rerun_review=false summary_choice={expected} summary_request=none"


def test_build_changes_treats_an_unknown_summary_request_as_none(app, capsys):
    # apply_changes' postcondition rejects it first; here it must never block the run.
    rc, last, _ = run(capsys, "build-changes", FINAL, "master.yaml", "2", "false", "", "original", "maybe")
    assert (rc, last) == (0, "recheck after_changes=true rerun_review=false summary_choice=original summary_request=none")


@pytest.mark.parametrize("request_", ["none", "rewrite", "original", "written"])
def test_check_draft_accepts_a_known_summary_request(app, capsys, request_):
    rc, last, _ = run(capsys, "check-draft", FINAL, "master.yaml", "2", request_)
    assert rc == 0 and last_json(last)["fits"] is True


@pytest.mark.parametrize("request_", ["maybe", "", "Rewrite"])
def test_check_draft_rejects_an_unknown_summary_request(app, capsys, request_):
    rc, last, _ = run(capsys, "check-draft", FINAL, "master.yaml", "2", request_)
    assert rc == 1 and codes(last) == {"summary_request_invalid"}
    assert '"none", "rewrite", "original" or "written"' in json.loads(last)["errors"][0]["message"]


def test_recheck_after_selection_changed(app, capsys):
    write_review(app, R1_OK, R2_OK, R3)
    rc, last, _ = run(capsys, "check-review", FINAL, "0", "0", "false")
    assert (rc, outcome(last)) == (0, "pass")
    sel = json.loads((app / "selection.json").read_text())
    sel["sections"] = [s for s in sel["sections"] if s["id"] != "honors"]  # the user drops a section
    (app / "selection.json").write_text(json.dumps(sel))
    old = (app / "tailored.yaml").read_bytes()
    assert run(capsys, "build-changes", FINAL, "master.yaml", "2", "false")[:2] == (0, "recheck after_changes=true rerun_review=false")
    new = (app / "tailored.yaml").read_bytes()
    assert new != old
    assert run(capsys, "recheck", FINAL)[:2] == (0, "{}")
    verified = json.loads((app / "review.verified.json").read_text())
    assert verified["tailored_sha256"] == hashlib.sha256(new).hexdigest()
    assert run(capsys, "review-page", FINAL, "master.yaml")[0] == 0  # not stale


# --- a fresh review.json per recruiter round ----------------------------------


def rounds(app: Path) -> list[str]:
    return sorted(p.name for p in app.glob("review*.json") if p.name != "review.verified.json")


def test_build_draft_sets_review_aside(app, capsys):
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "build-draft", FINAL, "master.yaml", "2")[:2] == (0, "{}")
    assert rounds(app) == ["review.round1.json"]
    assert json.loads((app / "review.round1.json").read_text())["requirements"][0] == R1_OK
    write_review(app, R1_BAD, R2_OK, R3)
    assert run(capsys, "build-draft", FINAL, "master.yaml", "2")[0] == 0
    assert rounds(app) == ["review.round1.json", "review.round2.json"]
    assert json.loads((app / "review.round2.json").read_text())["requirements"][0] == R1_BAD


def test_set_aside_numbers_after_the_highest_round(app):
    (app / "review.round3.json").write_text("{}")
    (app / "review.roundx.json").write_text("{}")  # not a round file
    write_review(app, R1_OK, R2_OK, R3)
    steps._set_aside_review(app)
    assert rounds(app) == ["review.round3.json", "review.round4.json", "review.roundx.json"]


def test_build_draft_without_review_renames_nothing(app, capsys):
    assert run(capsys, "build-draft", FINAL, "master.yaml", "2")[0] == 0
    assert rounds(app) == []


@pytest.mark.parametrize("rerun, expected", [("false", ["review.json"]), ("true", ["review.round1.json"])])
def test_build_changes_sets_review_aside_only_on_rerun(app, capsys, rerun, expected):
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "build-changes", FINAL, "master.yaml", "2", rerun)[0] == 0
    assert rounds(app) == expected


def test_recheck_uses_latest_round_when_no_new_review(app, capsys):
    # The recruiter ran out of visits after build-changes set its review aside: recheck the last one.
    write_review(app, R1_BAD, R2_OK, R3)
    (app / "review.json").rename(app / "review.round1.json")
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "build-changes", FINAL, "master.yaml", "2", "true")[0] == 0
    assert rounds(app) == ["review.round1.json", "review.round2.json"]
    assert run(capsys, "recheck", FINAL)[:2] == (0, "{}")
    verified = json.loads((app / "review.verified.json").read_text())
    assert verified["tailored_sha256"] == sha(app / "tailored.yaml")
    assert verified["gate"] == "pass" and verified["unmet_must"] == []  # round2 (R1_OK), not round1


def test_recheck_prefers_review_json(app, capsys):
    write_review(app, R1_OK, R2_OK, R3)
    (app / "review.json").rename(app / "review.round1.json")
    write_review(app, R1_BAD, R2_OK, R3)
    assert run(capsys, "recheck", FINAL)[:2] == (0, "{}")
    assert json.loads((app / "review.verified.json").read_text())["unmet_must"] == ["r1"]


def test_final_render(drafted_repo, capsys):
    assert run(capsys, "final-render", FINAL)[:2] == (0, "{}")
    a = drafted_repo / FINAL
    assert (a / "cv.pdf").stat().st_size > 0 and (a / "cv.md").exists()


# --- pinned (the workflow's invariant) ----------------------------------------


def test_pinned_before_anything_is_recorded(repo, capsys):
    assert run(capsys, "pinned", "master.yaml", "", A, "")[0] == 0


def test_pinned_unchanged(app, capsys):
    assert run(capsys, "pinned", "master.yaml", sha("master.yaml"), FINAL, sha(app / "requirements.json"))[0] == 0


def test_pinned_master_changed(app, capsys):
    before = sha("master.yaml")
    with open("master.yaml", "a", encoding="utf-8") as f:
        f.write("# an agent was here\n")
    rc, last, _ = run(capsys, "pinned", "master.yaml", before, FINAL, sha(app / "requirements.json"))
    assert rc == 1 and codes(last) == {"input_changed"}
    assert json.loads(last)["errors"][0]["id"] == "master.yaml"


def test_pinned_requirements_changed(app, capsys):
    before = sha(app / "requirements.json")
    (app / "requirements.json").write_text(json.dumps({**REQS, "requirements": REQS["requirements"][1:]}))
    rc, last, _ = run(capsys, "pinned", "master.yaml", sha("master.yaml"), FINAL, before)
    assert rc == 1 and json.loads(last)["errors"][0]["id"] == f"{FINAL}/requirements.json"


def test_pinned_requirements_missing(app, capsys):
    before = sha(app / "requirements.json")
    (app / "requirements.json").unlink()
    rc, last, _ = run(capsys, "pinned", "master.yaml", sha("master.yaml"), FINAL, before)
    assert rc == 1 and codes(last) == {"input_changed"}


# --- the written summary (WP17): guide pinned at floor, recruiter isolation, summary steps ---------------

GUIDE = "summary-guide.md"
PROFILE_LINE = "Data engineer with eight years of experience turning messy event streams into dependable, well-tested datasets."
GOOD = {
    "sentences": [
        {
            "text": "Data engineer at Quillfeather Labs who built a **feature store** used by four machine-learning teams.",
            "sources": ["experience.0", "experience.0.highlights.2"],
        },
        {"text": "Cut nightly batch runtime from 6 hours to 50 minutes with DuckDB.", "sources": ["experience.0.highlights.1"]},
    ]
}
INVENTED = {"sentences": [{"text": "Data engineer with 12 years at Quillfeather Labs.", "sources": ["experience.0"]}]}


def write_guide(root: Path, front: str = "section: profile\n") -> Path:
    path = root / GUIDE
    path.write_text(f"---\n{front}---\nWhy this team?\n", encoding="utf-8")
    return path


def write_summary(app: Path, summary: dict = GOOD) -> None:
    (app / "summary.json").write_text(json.dumps(summary))


def fake_floor(monkeypatch):
    monkeypatch.setattr(steps, "pin_floor", lambda m, n, section=None: {"ok": True, "pages": 1, "limit": n, "fits": True})


def test_floor_with_guide_pins_it(repo, capsys, monkeypatch):
    fake_floor(monkeypatch)
    write_guide(repo)
    rc, last, _ = run(capsys, "floor", "master.yaml", "2", GUIDE)
    assert (rc, last) == (0, f"fits master_sha256={sha('master.yaml')} guide_sha256={sha(GUIDE)} summary_section=profile")


@pytest.mark.parametrize("guide", [GUIDE, ""])
def test_floor_without_guide_file_records_empty_values(repo, capsys, monkeypatch, guide):
    fake_floor(monkeypatch)
    rc, last, _ = run(capsys, "floor", "master.yaml", "2", guide)
    assert (rc, last) == (0, f"fits master_sha256={sha('master.yaml')} guide_sha256= summary_section=")


def test_floor_invalid_guide_is_exit_1(repo, capsys, monkeypatch):
    def rendered(*a):
        raise AssertionError("rendered before the guide was checked")

    monkeypatch.setattr(steps, "pin_floor", rendered)
    write_guide(repo, "section: nope\n")
    rc, last, _ = run(capsys, "floor", "master.yaml", "2", GUIDE)
    assert rc == 1 and codes(last) == {"guide_invalid"}


def test_pinned_guide_unchanged(app, capsys):
    write_guide(app.parent.parent)
    args = ("master.yaml", sha("master.yaml"), FINAL, sha(app / "requirements.json"), GUIDE)
    assert run(capsys, "pinned", *args, sha(GUIDE))[0] == 0
    assert run(capsys, "pinned", *args, "")[0] == 0  # no guide in use: not pinned


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_pinned_guide_changed(app, capsys, change):
    guide = write_guide(app.parent.parent)
    before = sha(guide)
    if change == "edit":
        write_guide(app.parent.parent, "section: profile\nmax_sentences: 2\n")
    else:
        guide.unlink()
    rc, last, _ = run(capsys, "pinned", "master.yaml", sha("master.yaml"), FINAL, sha(app / "requirements.json"), GUIDE, before)
    assert rc == 1 and codes(last) == {"input_changed"}
    assert json.loads(last)["errors"][0]["id"] == GUIDE


def _profile_free(cv: str) -> bool:
    lines = cv.splitlines()
    return "Profile" not in lines and PROFILE_LINE not in lines and not any("Comfortable owning" in l for l in lines)


def test_build_draft_with_section_excludes_it_from_cv_txt(app, capsys):
    assert run(capsys, "build-draft", FINAL, "master.yaml", "2", "profile")[:2] == (0, "{}")
    cv = (app / "cv.txt").read_text()
    assert _profile_free(cv) and "Robin Kestrel" in cv.splitlines()
    assert "profile" in YAML(typ="safe").load((app / "tailored.yaml").read_text())["cv"]["sections"]  # still in the CV


@pytest.mark.parametrize("section", ["", "profile"])
def test_build_changes_with_section(app, capsys, section):
    rc, last, _ = run(capsys, "build-changes", FINAL, "master.yaml", "2", "false", section)
    assert (rc, last) == (0, "recheck after_changes=true rerun_review=false")
    assert _profile_free((app / "cv.txt").read_text()) == (section == "profile")


R1_PROFILE = {"id": "r1", "status": "met", "evidence_quote": PROFILE_LINE, "reason": "x"}


def _r1(app: Path) -> dict:
    return next(r for r in json.loads((app / "review.verified.json").read_text())["requirements"] if r["id"] == "r1")


def test_check_review_with_section_refuses_a_quote_only_there(app, capsys):
    write_review(app, R1_PROFILE, R2_OK, R3)
    rc, last, _ = run(capsys, "check-review", FINAL, "0", "0", "true")
    assert (rc, outcome(last)) == (0, "pass")
    assert (_r1(app)["status"], _r1(app)["downgraded"]) == ("met", False)
    rc, last, _ = run(capsys, "check-review", FINAL, "0", "0", "true", "profile")
    assert (rc, outcome(last)) == (0, "human")
    assert (_r1(app)["status"], _r1(app)["downgraded"]) == ("missing", True)


def test_recheck_with_section_refuses_a_quote_only_there(app, capsys):
    write_review(app, R1_PROFILE, R2_OK, R3)
    assert run(capsys, "recheck", FINAL, "profile")[:2] == (0, "{}")
    assert _r1(app)["status"] == "missing"


def test_check_review_shape_with_section(app, capsys):
    write_review(app, R1_PROFILE, R2_OK, R3)  # a shape check: the quote is not judged here
    assert run(capsys, "check-review-shape", FINAL, "profile")[0] == 0


# summary-route A MASTER GUIDE SECTION CHOICE


def route(capsys, section="profile", choice="write"):
    return run(capsys, "summary-route", FINAL, "master.yaml", GUIDE, section, choice)[:2]


def pinned_sel() -> str:
    """summary-route's pin of the selection, as build-summary checks it."""
    return f"selection_sha256={sha(Path(FINAL) / 'selection.json')}"


def test_summary_route_none_without_a_guide(app, capsys):
    write_summary(app)
    assert route(capsys, section="") == (0, "none summary_applied=false summary_status=none summary_notes=")


def test_summary_route_original(app, capsys):
    write_guide(app.parent.parent)
    write_summary(app)
    assert route(capsys, choice="original") == (
        0, "original summary_applied=false summary_status=original summary_notes="
    )


@pytest.mark.parametrize("choice", ["write", "keep"])
def test_summary_route_keep_when_the_summary_still_checks(app, capsys, choice):
    write_guide(app.parent.parent)
    write_summary(app)
    assert route(capsys, choice=choice) == (
        0, f"keep summary_applied=false summary_status=pending {pinned_sel()} summary_notes="
    )


def test_summary_route_write_without_a_summary(app, capsys):
    write_guide(app.parent.parent)
    # a forced write (not the user's rewrite) clears notes left from an earlier rewrite
    assert route(capsys) == (0, f"write summary_applied=false summary_status=pending {pinned_sel()} summary_notes=")


def test_summary_route_write_on_rewrite(app, capsys):
    write_guide(app.parent.parent)
    write_summary(app)
    # the user's rewrite keeps their notes for the writer
    assert route(capsys, choice="rewrite") == (0, f"write summary_applied=false summary_status=pending {pinned_sel()}")


def test_summary_route_write_when_a_change_drops_a_cited_source(app, capsys):
    write_guide(app.parent.parent)
    write_summary(app)
    sel = json.loads((app / "selection.json").read_text())
    exp0 = sel["sections"][1]["entries"][0]
    exp0["highlights"].remove("experience.0.highlights.1")
    (app / "selection.json").write_text(json.dumps(sel))
    rc, last, out = run(capsys, "summary-route", FINAL, "master.yaml", GUIDE, "profile", "keep")
    assert (rc, last) == (0, f"write summary_applied=false summary_status=pending {pinned_sel()} summary_notes=")
    assert "summary_source_not_selected" in out


def test_summary_route_none_when_the_section_is_not_selected(app, capsys):
    write_guide(app.parent.parent)
    sel = json.loads((app / "selection.json").read_text())
    sel["sections"] = [s for s in sel["sections"] if s["id"] != "profile"]
    (app / "selection.json").write_text(json.dumps(sel))
    assert route(capsys) == (0, "none summary_applied=false summary_status=section_dropped summary_notes=")


# summary-check A MASTER GUIDE (write_summary's postcondition)


def test_summary_check_adapter(app, capsys):
    write_guide(app.parent.parent)
    write_summary(app)
    rc, last, _ = run(capsys, "summary-check", FINAL, "master.yaml", GUIDE)
    assert rc == 0 and json.loads(last)["ok"] is True
    write_summary(app, INVENTED)
    rc, last, _ = run(capsys, "summary-check", FINAL, "master.yaml", GUIDE)
    assert rc == 1 and codes(last) == {"summary_unsupported_token"}
    (app / "summary.json").unlink()
    rc, last, _ = run(capsys, "summary-check", FINAL, "master.yaml", GUIDE)
    assert rc == 1 and codes(last) == {"summary_invalid"}


def too_long(monkeypatch):
    monkeypatch.setattr(steps, "page_check", lambda y, n: {"ok": True, "pages": 3, "limit": n, "fits": False,
                                                            "last_page_remaining_mm": 0.0, "last_page_fill_ratio": 1.0})


def test_summary_check_with_a_limit_checks_the_page_fit(app, capsys, monkeypatch):
    # write_summary's postcondition: a summary that pushes the CV over the limit goes back to the writer,
    # and the retry's failure text says why.
    write_guide(app.parent.parent)
    write_summary(app)
    before = {n: (app / n).read_bytes() for n in ("tailored.yaml", "page-check.json")}
    rc, last, _ = run(capsys, "summary-check", FINAL, "master.yaml", GUIDE, "2")
    assert rc == 0 and json.loads(last)["fits"] is True
    too_long(monkeypatch)
    rc, last, _ = run(capsys, "summary-check", FINAL, "master.yaml", GUIDE, "2")
    assert rc == 1 and codes(last) == {"page_limit"}
    msg = json.loads(last)["errors"][0]["message"]
    assert "3 pages" in msg and "limit is 2" in msg and "Shorten the summary" in msg
    assert {n: (app / n).read_bytes() for n in before} == before  # built aside


def test_summary_check_with_a_limit_reports_the_summary_errors_first(app, capsys, monkeypatch):
    write_guide(app.parent.parent)
    write_summary(app, INVENTED)
    too_long(monkeypatch)
    rc, last, _ = run(capsys, "summary-check", FINAL, "master.yaml", GUIDE, "2")
    assert rc == 1 and codes(last) == {"summary_unsupported_token"}


# build-summary A MASTER LIMIT GUIDE SECTION [SELECTION_SHA256]


def test_build_summary_ok(app, capsys):
    write_guide(app.parent.parent)
    write_summary(app)
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false", "profile")[0] == 0
    rc, last, _ = run(capsys, "build-summary", FINAL, "master.yaml", "2", GUIDE, "profile")
    assert (rc, last) == (0, "ok summary_applied=true summary_status=applied summary_choice=keep summary_notes=")
    doc = YAML(typ="safe").load((app / "tailored.yaml").read_text())
    assert doc["cv"]["sections"]["profile"] == [" ".join(s["text"] for s in GOOD["sentences"])]
    assert _profile_free((app / "cv.txt").read_text())
    assert json.loads((app / "page-check.json").read_text())["fits"] is True
    # the verified review follows the new tailored YAML, so the review page is not stale
    assert json.loads((app / "review.verified.json").read_text())["tailored_sha256"] == sha(app / "tailored.yaml")
    rc, _, _ = run(capsys, "review-page", FINAL, "master.yaml", "p.txt", "", GUIDE, "true")
    assert rc == 0 and "written for this posting" in (app / "review.html").read_text()


def test_build_summary_too_long_leaves_the_draft(app, capsys, monkeypatch):
    write_guide(app.parent.parent)
    write_summary(app)
    before = {n: (app / n).read_bytes() for n in ("tailored.yaml", "page-check.json")}
    too_long(monkeypatch)
    rc, last, _ = run(capsys, "build-summary", FINAL, "master.yaml", "2", GUIDE, "profile")
    assert (rc, last) == (0, "too_long")
    assert {n: (app / n).read_bytes() for n in before} == before  # review_page without the summary still matches


def test_build_summary_failing_check_is_exit_1(app, capsys):
    write_guide(app.parent.parent)
    write_summary(app, INVENTED)
    rc, last, _ = run(capsys, "build-summary", FINAL, "master.yaml", "2", GUIDE, "profile")
    assert rc == 1 and codes(last) == {"summary_unsupported_token"}


FILES = ("tailored.yaml", "page-check.json", "cv.txt", "review.verified.json")


def snapshot(app: Path) -> dict:
    return {n: (app / n).read_bytes() if (app / n).exists() else None for n in FILES}


def test_build_summary_failure_after_the_build_leaves_the_folder_as_it_was(app, capsys):
    # No review to re-verify: the last fallible step fails, after the build and page-check.
    write_guide(app.parent.parent)
    write_summary(app)
    assert run(capsys, "build-draft", FINAL, "master.yaml", "2", "profile")[0] == 0
    before = snapshot(app)
    rc, last, _ = run(capsys, "build-summary", FINAL, "master.yaml", "2", GUIDE, "profile")
    assert rc == 1 and codes(last) == {"review_invalid"}
    assert snapshot(app) == before
    assert [p.name for p in app.iterdir() if p.name.startswith(".")] == []  # no work folder left behind


def test_build_summary_refuses_a_selection_changed_since_the_route(app, capsys):
    write_guide(app.parent.parent)
    write_summary(app)
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false", "profile")[0] == 0
    pinned = sha(app / "selection.json")
    sel = json.loads((app / "selection.json").read_text())
    sel["sections"] = [s for s in sel["sections"] if s["id"] != "honors"]  # the writer edited the selection
    (app / "selection.json").write_text(json.dumps(sel))
    before = snapshot(app)
    rc, last, _ = run(capsys, "build-summary", FINAL, "master.yaml", "2", GUIDE, "profile", pinned)
    assert rc == 1 and codes(last) == {"selection_changed"}
    assert snapshot(app) == before


def test_build_summary_with_the_pinned_selection(app, capsys):
    write_guide(app.parent.parent)
    write_summary(app)
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false", "profile")[0] == 0
    rc, last, _ = run(capsys, "build-summary", FINAL, "master.yaml", "2", GUIDE, "profile", sha(app / "selection.json"))
    assert (rc, last) == (0, "ok summary_applied=true summary_status=applied summary_choice=keep summary_notes=")


# review-page's note when a guide is in use but no written summary is shown


NOTE = "The CV&#x27;s own summary (no written summary: {})"


# The note's reason comes from `summary_status`, which only deterministic steps set, so it is always true.
STATUS_NOTES = {
    "original": "you chose the CV&#x27;s original summary",
    "section_dropped": "the summary&#x27;s section is not in this selection",
    "writer_failed": "the writer could not produce a summary that passes the checks",
    "build_failed": "building the CV with the written summary failed (see the build_summary step&#x27;s output)",
    "cap_reached": "the summary steps reached their round limit",
    "not_reached": "the run did not reach the summary step (the draft step reached its round limit)",
    "pending": "the summary step did not finish",
    "applied": "the summary step did not finish",  # applied without summary_applied: never claim more
    "none": "the summary step did not finish",
}


def test_every_summary_status_has_a_note():
    assert set(STATUS_NOTES) == set(steps.SUMMARY_STATUSES)


@pytest.mark.parametrize("status, reason", STATUS_NOTES.items())
def test_review_page_notes_why_the_summary_is_the_masters(app, capsys, status, reason):
    write_guide(app.parent.parent)
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false")[0] == 0
    assert run(capsys, "review-page", FINAL, "master.yaml", "p.txt", "", GUIDE, "false", "profile", status)[0] == 0
    h = (app / "review.html").read_text()
    assert h.count("no written summary") == 1 and NOTE.format(reason) in h
    cv = h.split('<article class="cv">')[1].split("</article>")[0]
    assert cv.index(NOTE.format(reason)) < cv.index("Comfortable owning")  # at the summary section


def test_review_page_after_the_draft_cap_says_the_summary_was_not_reached(app, capsys):
    """draft exhausted -> review_page: no summary step ran, so summary_status is still its default."""
    from ruamel.yaml import YAML as _Y

    wf = _Y(typ="safe").load((Path(__file__).resolve().parent.parent / "workflow/tailor.yaml").read_text())
    default = wf["state"]["summary_status"]["default"]
    assert default == "not_reached"
    write_guide(app.parent.parent)
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false")[0] == 0
    assert run(capsys, "review-page", FINAL, "master.yaml", "p.txt", "", GUIDE, "false", "profile", default)[0] == 0
    h = (app / "review.html").read_text()
    assert NOTE.format("the run did not reach the summary step (the draft step reached its round limit)") in h
    assert "writer" not in h.split('<article class="cv">')[1].split("</article>")[0]


def test_review_page_unknown_status_is_usage(app, capsys):
    write_guide(app.parent.parent)
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false")[0] == 0
    rc, last, _ = run(capsys, "review-page", FINAL, "master.yaml", "p.txt", "", GUIDE, "false", "profile", "write")
    assert rc == 1 and codes(last) == {"usage"}


# summary-status REASON: the status a failed or capped summary step leaves, for the review page's note


@pytest.mark.parametrize("reason", ["writer_failed", "build_failed", "cap_reached"])
def test_summary_status_adapter(capsys, reason):
    assert run(capsys, "summary-status", reason)[:2] == (0, json.dumps({"summary_status": reason}))


@pytest.mark.parametrize("reason", ["applied", "original", "not_reached", "", "WRITER_FAILED"])
def test_summary_status_rejects_an_unknown_reason(capsys, reason):
    rc, last, _ = run(capsys, "summary-status", reason)
    assert rc == 1 and codes(last) == {"usage"}


@pytest.mark.parametrize("extra", [["false", "", "original"], ["false"], []])
def test_review_page_without_a_guide_section_has_no_note(app, capsys, extra):
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false")[0] == 0
    assert run(capsys, "review-page", FINAL, "master.yaml", "p.txt", "", GUIDE, *extra)[0] == 0
    assert "no written summary" not in (app / "review.html").read_text()


def test_review_page_with_the_written_summary_has_no_note(app, capsys):
    write_guide(app.parent.parent)
    write_summary(app)
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false", "profile")[0] == 0
    assert run(capsys, "build-summary", FINAL, "master.yaml", "2", GUIDE, "profile")[0] == 0
    rc, _, _ = run(capsys, "review-page", FINAL, "master.yaml", "p.txt", "", GUIDE, "true", "profile", "applied")
    assert rc == 0 and "no written summary" not in (app / "review.html").read_text()


@pytest.mark.parametrize("extra", [[], ["p.txt", ""], ["p.txt", "", GUIDE, "false"], ["p.txt", "", "", ""]])
def test_review_page_without_an_applied_summary_has_no_badge(app, capsys, extra):
    write_guide(app.parent.parent)
    write_summary(app)  # present, but not applied: the page shows the master's summary
    write_review(app, R1_OK, R2_OK, R3)
    assert run(capsys, "check-review", FINAL, "0", "0", "false")[0] == 0
    assert run(capsys, "review-page", FINAL, "master.yaml", *extra)[0] == 0
    assert "written for this posting" not in (app / "review.html").read_text()


def test_review_page_bad_applied_flag_is_usage(app, capsys):
    rc, last, _ = run(capsys, "review-page", FINAL, "master.yaml", "p.txt", "", GUIDE, "yes")
    assert rc == 1 and codes(last) == {"usage"}


# --- the guide's section is required (WP19): the drafter cannot drop the summary to save space ------


def drop_profile(app: Path) -> None:
    sel = json.loads((app / "selection.json").read_text())
    sel["sections"] = [s for s in sel["sections"] if s["id"] != "profile"]
    (app / "selection.json").write_text(json.dumps(sel))


def test_check_draft_rejects_a_selection_without_the_guide_section(app, capsys):
    drop_profile(app)
    rc, last, _ = run(capsys, "check-draft", FINAL, "master.yaml", "2", "none", "profile")
    assert rc == 1 and codes(last) == {"summary_section_missing"}
    err = json.loads(last)["errors"][0]
    assert err["id"] == "profile" and "keep the `profile` section" in err["message"]


@pytest.mark.parametrize("request_", ["none", "original"])
def test_check_draft_with_the_guide_section_selected(app, capsys, request_):
    rc, last, _ = run(capsys, "check-draft", FINAL, "master.yaml", "2", request_, "profile")
    assert rc == 0 and last_json(last)["fits"] is True


def test_check_draft_without_a_guide_allows_dropping_the_section(app, capsys):
    drop_profile(app)
    for args in ((), ("none",), ("none", "")):
        rc, last, _ = run(capsys, "check-draft", FINAL, "master.yaml", "2", *args)
        assert rc == 0 and last_json(last)["fits"] is True, args


def test_build_draft_rejects_a_selection_without_the_guide_section(app, capsys):
    drop_profile(app)
    before = (app / "tailored.yaml").read_text()
    rc, last, _ = run(capsys, "build-draft", FINAL, "master.yaml", "2", "profile")
    assert rc == 1 and codes(last) == {"summary_section_missing"}
    assert (app / "tailored.yaml").read_text() == before  # nothing built


def test_build_changes_rejects_a_selection_without_the_guide_section(app, capsys):
    drop_profile(app)
    rc, last, _ = run(capsys, "build-changes", FINAL, "master.yaml", "2", "false", "profile", "written", "none")
    assert rc == 1 and codes(last) == {"summary_section_missing"}


def test_floor_counts_the_guide_section(repo, capsys, monkeypatch):
    seen = []
    monkeypatch.setattr(steps, "pin_floor", lambda m, n, section=None: seen.append(section) or {"ok": True, "pages": 1, "limit": n, "fits": True})
    write_guide(repo)
    assert run(capsys, "floor", "master.yaml", "2", GUIDE)[0] == 0
    assert run(capsys, "floor", "master.yaml", "2", "")[0] == 0
    assert run(capsys, "floor", "master.yaml", "2")[0] == 0
    assert seen == ["profile", None, None]


# --- a ceiling on bullets per entry (WP20): no one role takes all the space -----------------------------


def test_check_draft_rejects_more_bullets_than_the_limit(app, capsys):
    # experience.0 has 3 highlights in the example selection
    rc, last, _ = run(capsys, "check-draft", FINAL, "master.yaml", "2", "none", "", "2")
    assert rc == 1 and codes(last) == {"too_many_highlights"}
    err = json.loads(last)["errors"][0]
    assert err["id"] == "experience.0"
    assert "3 bullets" in err["message"] and "at most 2" in err["message"] and "strongest" in err["message"]


def test_check_draft_accepts_exactly_the_limit(app, capsys):
    rc, last, _ = run(capsys, "check-draft", FINAL, "master.yaml", "2", "none", "", "3")
    assert rc == 0 and last_json(last)["fits"] is True


def test_check_draft_reports_every_entry_over_the_limit(app, capsys):
    rc, last, _ = run(capsys, "check-draft", FINAL, "master.yaml", "2", "none", "", "1")
    assert rc == 1 and [e["id"] for e in json.loads(last)["errors"]] == ["experience.0", "experience.1"]


@pytest.mark.parametrize("value", ["0", "-1", "x", ""])
def test_check_draft_max_bullets_below_1_is_usage(app, capsys, value):
    rc, last, _ = run(capsys, "check-draft", FINAL, "master.yaml", "2", "none", "", value)
    assert rc == 1 and codes(last) == {"usage"}


def test_check_draft_old_signatures_have_no_ceiling(app, capsys):
    for args in ((), ("none",), ("none", "")):
        rc, last, _ = run(capsys, "check-draft", FINAL, "master.yaml", "2", *args)
        assert rc == 0, args


def test_build_draft_rejects_more_bullets_than_the_limit(app, capsys):
    rc, last, _ = run(capsys, "build-draft", FINAL, "master.yaml", "2", "", "2")
    assert rc == 1 and codes(last) == {"too_many_highlights"}
    assert not (app / "cv.txt").exists()  # nothing built (the fixture has no cv.txt; build-draft writes one)


def test_build_changes_rejects_more_bullets_than_the_limit(app, capsys):
    rc, last, _ = run(capsys, "build-changes", FINAL, "master.yaml", "2", "false", "", "", "", "2")
    assert rc == 1 and codes(last) == {"too_many_highlights"}
    rc, last, _ = run(capsys, "build-changes", FINAL, "master.yaml", "2", "false", "", "", "", "3")
    assert (rc, last) == (0, "recheck after_changes=true rerun_review=false")


@pytest.mark.parametrize("value", ["0", "-2", "x"])
def test_floor_rejects_a_bad_max_bullets_before_any_agent(repo, capsys, monkeypatch, value):
    # pawl does not range-check integer args: the run must block here, not in the draft's postcondition.
    fake_floor(monkeypatch)
    rc, last, _ = run(capsys, "floor", "master.yaml", "2", "", value)
    assert rc == 1 and codes(last) == {"usage"}
    assert "MAX_BULLETS" in json.loads(last)["errors"][0]["message"]


def test_floor_accepts_max_bullets(repo, capsys, monkeypatch):
    fake_floor(monkeypatch)
    rc, last, _ = run(capsys, "floor", "master.yaml", "2", "", "4")
    assert (rc, last) == (0, f"fits master_sha256={sha('master.yaml')} guide_sha256= summary_section=")


def test_check_draft_reports_a_missing_section_and_too_many_bullets_together(app, capsys):
    drop_profile(app)
    rc, last, _ = run(capsys, "check-draft", FINAL, "master.yaml", "2", "none", "profile", "1")
    assert rc == 1 and codes(last) == {"summary_section_missing", "too_many_highlights"}
