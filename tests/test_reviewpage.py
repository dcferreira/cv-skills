import json
import re
from pathlib import Path

import pytest

from cv_skills import cli

REPO = Path(__file__).resolve().parent.parent
MASTER = REPO / "examples" / "master.yaml"
SELECTION = REPO / "examples" / "selection.json"

REQS = {
    "company": "Acme Analytics",
    "role": "Senior Data Engineer",
    "requirements": [
        {"id": "r1", "text": "Feature store", "kind": "must", "evidence": "cv"},
        {"id": "r2", "text": "Streaming in Go", "kind": "must", "evidence": "cv"},
        {"id": "r3", "text": "Mentoring", "kind": "nice", "evidence": "cv"},
    ],
}
PAGE_CHECK = {"pages": 2, "limit": 2, "fits": True, "last_page_remaining_mm": 41.5, "last_page_fill_ratio": 0.78}


def run(capsys, *argv):
    rc = cli.main([str(a) for a in argv])
    return rc, json.loads(capsys.readouterr().out)


def setup(tmp_path, capsys, *, selection=SELECTION, reqs=REQS, review=None):
    tailored = tmp_path / "tailored.yaml"
    assert run(capsys, "build", "--master", MASTER, "--selection", selection, "--out", tailored)[0] == 0
    (tmp_path / "requirements.json").write_text(json.dumps(reqs))
    rq = review or [
        {"id": "r1", "status": "met", "evidence_quote": "feature store used by four machine-learning teams", "reason": "direct"},
        {"id": "r2", "status": "partial", "evidence_quote": "streaming ingestion service in Go", "reason": "go"},
        {"id": "r3", "status": "missing", "evidence_quote": "", "reason": "none"},
    ]
    (tmp_path / "review.json").write_text(
        json.dumps({"requirements": rq, "top_fixes": ["Lead with the feature store"], "missed_requirements": ["Kubernetes"]})
    )
    (tmp_path / "page-check.json").write_text(json.dumps(PAGE_CHECK))
    rc, _ = run(
        capsys,
        "check-review",
        "--review", tmp_path / "review.json",
        "--requirements", tmp_path / "requirements.json",
        "--tailored", tailored,
        "--out", tmp_path / "review.verified.json",
    )
    assert rc == 0
    return tmp_path


def page(capsys, app, *, selection=SELECTION, out=None, **over):
    args = {
        "--master": MASTER,
        "--selection": selection,
        "--tailored": app / "tailored.yaml",
        "--review": app / "review.verified.json",
        "--page-check": app / "page-check.json",
        "--out": out or app / "review.html",
    }
    args.update(over)
    argv = ["review-page"] + [x for kv in args.items() for x in kv]
    return run(capsys, *argv)


@pytest.fixture
def app(tmp_path, capsys):
    return setup(tmp_path, capsys)


def html_of(app):
    return (app / "review.html").read_text(encoding="utf-8")


def test_review_page_end_to_end(app, capsys):
    rc, out = page(capsys, app)
    assert rc == 0 and out == {"ok": True, "out": str(app / "review.html"), "swaps": 3}
    h = html_of(app)
    assert h.startswith("<!doctype html>") and "Robin Kestrel" in h


def test_every_swap_is_marked_with_original(app, capsys):
    page(capsys, app)
    h = html_of(app)
    assert len(re.findall(r'<mark class="swap"', h)) == 3
    for original, new in [
        ("forecasting product", "demand-forecasting product"),
        ("four ML teams", "four machine-learning teams"),
        ("(pandas, polars)", "(pandas and polars)"),
    ]:
        assert f'title="was: {original}"' in h
        assert new in h
    # numbered superscripts and the changes table
    assert h.count("<sup>[1]</sup>") == 1 and "<sup>[3]</sup>" in h
    changes = h.split('id="changes"')[1].split("</table>")[0]
    assert changes.count("<tr>") == 4  # header + 3 swaps
    assert "experience.0.highlights.2" in changes


def test_markup_renders_bold_not_literal(app, capsys):
    page(capsys, app)
    h = html_of(app)
    assert "<strong>Go</strong>" in h and "**" not in h


def test_cv_sections_in_selection_order(app, capsys):
    page(capsys, app)
    h = html_of(app)
    idx = [h.index(f">{t}<") for t in ("Profile", "Experience", "Skills", "Education", "Honors")]
    assert idx == sorted(idx)
    # dropped master content is absent
    assert "Copperline Systems" not in h


def test_requirement_table_and_extras(app, capsys):
    page(capsys, app)
    h = html_of(app)
    assert "Feature store" in h and "feature store used by four machine-learning teams" in h
    assert "Lead with the feature store" in h and "Kubernetes" in h
    assert "Gate" in h and "2 of 2" in h


def test_downgraded_requirement_note(tmp_path, capsys):
    bad = [
        {"id": "r1", "status": "met", "evidence_quote": "an invented sentence", "reason": "x"},
        {"id": "r2", "status": "met", "evidence_quote": "streaming ingestion service in Go", "reason": "x"},
        {"id": "r3", "status": "missing", "evidence_quote": "", "reason": "x"},
    ]
    app = setup(tmp_path, capsys, review=bad)
    page(capsys, app)
    h = html_of(app)
    assert "quote not found in the CV" in h and "an invented sentence" in h
    assert "1 of 2" in h


def test_gate_line_splits_met_and_partial(app, capsys):
    page(capsys, app)
    assert "2 of 2 must-haves supported by a verified quote (1 met, 1 partial)." in html_of(app)


def test_gate_line_split_when_failing(tmp_path, capsys):
    bad = [
        {"id": "r1", "status": "met", "evidence_quote": "an invented sentence", "reason": "x"},
        {"id": "r2", "status": "met", "evidence_quote": "streaming ingestion service in Go", "reason": "x"},
        {"id": "r3", "status": "missing", "evidence_quote": "", "reason": "x"},
    ]
    app = setup(tmp_path, capsys, review=bad)
    page(capsys, app)
    h = html_of(app)
    assert "1 of 2 must-haves supported by a verified quote (1 met, 0 partial). Unmet: r1." in h


def test_page_shows_pages_and_space_left(app, capsys):
    page(capsys, app)
    h = html_of(app)
    assert "2 of 2" in h and "41.5 mm" in h and "78%" in h


def test_text_is_html_escaped(tmp_path, capsys):
    hostile = dict(REQS)
    hostile["requirements"] = [
        {"id": "r1", "text": "<script>alert(1)</script> & more", "kind": "must", "evidence": "cv"},
        {"id": "r2", "text": "Streaming", "kind": "must", "evidence": "cv"},
        {"id": "r3", "text": "Mentoring", "kind": "nice", "evidence": "cv"},
    ]
    rq = [
        {"id": "r1", "status": "missing", "evidence_quote": "", "reason": '<img src=x onerror="y">'},
        {"id": "r2", "status": "met", "evidence_quote": "streaming ingestion service in Go", "reason": "x"},
        {"id": "r3", "status": "missing", "evidence_quote": "", "reason": "x"},
    ]
    app = setup(tmp_path, capsys, reqs=hostile, review=rq)
    page(capsys, app)
    h = html_of(app)
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; more" in h
    assert "<img" not in h and "<script" not in h


def test_swap_text_with_html_is_escaped(tmp_path, capsys):
    sel = json.loads(SELECTION.read_text())
    sel["swaps"][1]["to"] = "four <i>ML</i> teams"
    p = tmp_path / "sel.json"
    p.write_text(json.dumps(sel))
    app = setup(tmp_path, capsys, selection=p)
    page(capsys, app, selection=p)
    h = html_of(app)
    assert "four &lt;i&gt;ML&lt;/i&gt; teams" in h and "<i>" not in h


def test_page_is_self_contained(app, capsys):
    page(capsys, app)
    h = html_of(app)
    for banned in ("<script", "<link", "<img", "@import", " src=", "<iframe"):
        assert banned not in h


def test_tailored_mismatch_exit_1(app, capsys):
    t = app / "tailored.yaml"
    t.write_text(t.read_text().replace("four machine-learning teams", "four ML teams"))
    rc, out = page(capsys, app)
    assert rc == 1 and out["errors"][0]["code"] == "tailored_mismatch"
    assert not (app / "review.html").exists()


def test_page_check_invalid_exit_2(app, capsys):
    (app / "page-check.json").write_text(json.dumps({"pages": 2}))
    rc, out = page(capsys, app)
    assert rc == 2 and out["errors"][0]["code"] == "page_check_invalid"
    assert not (app / "review.html").exists()


def test_review_invalid_exit_2(app, capsys):
    (app / "review.verified.json").write_text("{nope")
    rc, out = page(capsys, app)
    assert rc == 2 and out["errors"][0]["code"] == "review_invalid"


def judge_section(h):
    return h.split("For you to judge</h2>")[1].split("</section>")[0]


def test_for_you_to_judge_lists_none_requirements(tmp_path, capsys):
    reqs = json.loads(json.dumps(REQS))
    reqs["requirements"] += [
        {"id": "r4", "text": "Fluent <German>", "kind": "must", "evidence": "none"},
        {"id": "r5", "text": "Curiosity", "kind": "nice", "evidence": "none"},
    ]
    app = setup(tmp_path, capsys, reqs=reqs)
    assert page(capsys, app)[0] == 0
    h = html_of(app)
    assert h.index("<th>Evidence quote</th>") < h.index("For you to judge</h2>") < h.index("Top fixes</h2>")
    section = judge_section(h)
    # the id too: the summary line names items by id
    assert "<li>r4 <b>must</b> Fluent &lt;German&gt;</li>" in section and "<li>r5 nice Curiosity</li>" in section
    assert "language fluency" in section and "not in the gate" in section
    assert "2 of 2" in h  # the gate counts only the cv musts


def test_for_you_to_judge_none_when_empty(app, capsys):
    page(capsys, app)
    assert "<p>None.</p>" in judge_section(html_of(app))


@pytest.mark.parametrize(
    "value", [None, "r4", [{"id": "r4", "kind": "should", "text": "x"}], [{"id": "r4", "kind": "must"}]]
)
def test_verified_without_valid_for_you_to_judge_is_review_invalid(app, capsys, value):
    p = app / "review.verified.json"
    data = json.loads(p.read_text())
    if value is None:
        data.pop("for_you_to_judge")
    else:
        data["for_you_to_judge"] = value
    p.write_text(json.dumps(data))
    rc, out = page(capsys, app)
    assert rc == 2 and out["errors"][0]["code"] == "review_invalid"
    assert "for_you_to_judge" in out["errors"][0]["message"]


def test_selection_rejection_passes_through(app, capsys):
    sel = json.loads(SELECTION.read_text())
    sel["sections"][0]["entries"][0]["id"] = "profile.99"
    p = app / "bad.json"
    p.write_text(json.dumps(sel))
    rc, out = page(capsys, app, selection=p)
    assert rc == 1 and not (app / "review.html").exists()


def test_out_is_input(app, capsys):
    rc, out = page(capsys, app, out=app / "tailored.yaml")
    assert rc == 2 and out["errors"][0]["code"] == "out_is_input"


def test_page_without_swaps(tmp_path, capsys):
    sel = json.loads(SELECTION.read_text())
    sel.pop("swaps")
    p = tmp_path / "sel.json"
    p.write_text(json.dumps(sel))
    app = setup(tmp_path, capsys, selection=p)
    rc, out = page(capsys, app, selection=p)
    assert rc == 0 and out["swaps"] == 0 and '<mark class="swap"' not in html_of(app)


# --- review findings -----------------------------------------------------------

from html.parser import HTMLParser

PAIRED = {"strong", "em", "mark", "a", "code", "ul", "li", "p", "div", "span", "del", "ins", "sup"}


def assert_well_formed(h):
    stack = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag in PAIRED:
                stack.append(tag)

        def handle_endtag(self, tag):
            if tag in PAIRED:
                assert stack and stack[-1] == tag, f"mis-nested </{tag}> with open {stack}"
                stack.pop()

    P().feed(h)
    assert not stack, f"unclosed {stack}"
    assert not re.search(r'href="[^"]*<', h)


def custom(tmp_path, capsys, *, replace=(), insert=None, swaps=(), extra_sections=()):
    """A master edited from the example, with a selection that includes experience.0.highlights.3."""
    text = MASTER.read_text()
    for old, new in replace:
        assert old in text
        text = text.replace(old, new)
    if insert:
        marker, block = insert
        assert marker in text
        text = text.replace(marker, block + marker, 1)
    master = tmp_path / "master.yaml"
    master.write_text(text)
    rc, out = run(capsys, "ids", "--master", master)
    assert rc == 0
    sel = json.loads(SELECTION.read_text())
    sel["master_hash"] = out["master_hash"]
    sel["sections"][1]["entries"][0]["highlights"].append("experience.0.highlights.3")
    sel["sections"].extend(extra_sections)
    sel["swaps"] = list(swaps)
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps(sel))
    return master, selection


def custom_page(tmp_path, capsys, **kw):
    return page_for(tmp_path, capsys, *custom(tmp_path, capsys, **kw))


def page_for(tmp_path, capsys, master, selection):
    """Build, check-review (all missing) and review-page for this master and selection; the page's HTML."""
    tailored = tmp_path / "tailored.yaml"
    assert run(capsys, "build", "--master", master, "--selection", selection, "--out", tailored)[0] == 0
    (tmp_path / "requirements.json").write_text(json.dumps(REQS))
    (tmp_path / "review.json").write_text(
        json.dumps(
            {
                "requirements": [{"id": r["id"], "status": "missing", "evidence_quote": "", "reason": "x"} for r in REQS["requirements"]],
                "top_fixes": [],
                "missed_requirements": [],
            }
        )
    )
    (tmp_path / "page-check.json").write_text(json.dumps(PAGE_CHECK))
    assert (
        run(
            capsys,
            "check-review",
            "--review", tmp_path / "review.json",
            "--requirements", tmp_path / "requirements.json",
            "--tailored", tailored,
            "--out", tmp_path / "review.verified.json",
        )[0]
        == 0
    )
    rc, out = page(capsys, tmp_path, selection=selection, **{"--master": master})
    assert rc == 0, out
    return html_of(tmp_path)


MENTOR = "Mentored three junior engineers through their first on-call rotation"


def test_swap_straddling_markup_stays_well_formed(tmp_path, capsys):
    h = custom_page(
        tmp_path,
        capsys,
        replace=[(MENTOR, "Wrote **Python** and Go services")],
        swaps=[{"target_id": "experience.0.highlights.3", "from": "Python** and", "to": "Rust** plus"}],
    )
    assert_well_formed(h)
    assert h.count('<mark class="swap"') == 1


def test_swap_inside_link_url_stays_well_formed(tmp_path, capsys):
    h = custom_page(
        tmp_path,
        capsys,
        replace=[(MENTOR, "See [the docs](https://example.com/foo) for details")],
        swaps=[{"target_id": "experience.0.highlights.3", "from": "foo", "to": "bar"}],
    )
    assert_well_formed(h)
    assert h.count('<mark class="swap"') == 1


def test_swap_inside_bold_still_renders_bold_and_mark(tmp_path, capsys):
    h = custom_page(
        tmp_path,
        capsys,
        replace=[(MENTOR, "Built services in **Go** daily")],
        swaps=[{"target_id": "experience.0.highlights.3", "from": "Go", "to": "Rust"}],
    )
    assert_well_formed(h)
    assert re.search(r"<strong><mark [^>]*>Rust</mark><sup>\[1\]</sup></strong>", h)


def test_marks_wrap_exactly_the_new_text(app, capsys):
    page(capsys, app)
    marks = re.findall(r'<mark class="swap" data-n="(\d)" title="was: [^"]*">(.*?)</mark>', html_of(app))
    assert marks == [("1", "demand-forecasting product"), ("2", "four machine-learning teams"), ("3", "(pandas and polars)")]


def test_private_use_chars_in_swap_survive(tmp_path, capsys):
    h = custom_page(
        tmp_path,
        capsys,
        replace=[(MENTOR, "Mentored three junior engineers daily")],
        swaps=[{"target_id": "experience.0.highlights.3", "from": "daily", "to": "Go\ue000tools\ue001\ue002"}],
    )
    assert re.search(r'<mark [^>]*>Go\ue000tools\ue001\ue002</mark>', h)


def test_empty_summary_renders_without_summary(tmp_path, capsys):
    h = custom_page(
        tmp_path,
        capsys,
        replace=[("summary: Lead engineer for the analytics platform behind a forecasting product.", 'summary: ""')],
    )
    assert "Quillfeather Labs" in h and "Lead engineer" not in h


def test_publication_authors_are_shown(tmp_path, capsys):
    block = (
        "    publications:\n      - title: A Paper\n        authors:\n          - Test Person\n"
        "          - Other Author\n        journal: J\n        date: 2020-01\n"
    )
    h = custom_page(
        tmp_path,
        capsys,
        insert=("    honors:\n", block),
        extra_sections=[{"id": "publications", "entries": [{"id": "publications.0"}]}],
    )
    assert "Test Person, Other Author" in h and "A Paper" in h


def test_section_heading_uses_rendercv_title(tmp_path, capsys):
    block = "    education_and_training:\n      - Evening classes in statistics\n"
    h = custom_page(
        tmp_path,
        capsys,
        insert=("    honors:\n", block),
        extra_sections=[{"id": "education_and_training", "entries": [{"id": "education_and_training.0"}]}],
    )
    assert ">Education and Training<" in h


def _mutate_verified(app, mutate):
    p = app / "review.verified.json"
    d = json.loads(p.read_text())
    mutate(d)
    p.write_text(json.dumps(d))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["requirements"][0].update(status='met" onmouseover="alert(1)'),
        lambda d: d["requirements"][0].update(kind="should"),
        lambda d: d["requirements"][0].update(downgraded="no"),
        lambda d: d["requirements"][0].update(evidence_quote=None),
        lambda d: d.update(gate='x" onclick="alert(1)'),
        lambda d: d.update(outcome="maybe"),
        lambda d: d.update(supported_must="<img src=x onerror=alert(1)>"),
        lambda d: d.update(total_must=True),
        lambda d: d.update(unmet_must=[1]),
        lambda d: d.update(top_fixes=[3]),
        lambda d: d.update(missed_requirements="x"),
        lambda d: d.update(company=5),
        lambda d: d.pop("tailored_sha256"),
        lambda d: d.update(tailored_sha256=7),
    ],
)
def test_verified_review_is_validated_strictly(app, capsys, mutate):
    _mutate_verified(app, mutate)
    rc, out = page(capsys, app)
    assert rc == 2 and out["errors"][0]["code"] == "review_invalid"
    assert not (app / "review.html").exists()


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_page_check_non_finite_is_invalid(app, capsys, bad):
    (app / "page-check.json").write_text(
        '{"pages": 2, "limit": 2, "fits": true, "last_page_remaining_mm": 1, "last_page_fill_ratio": %s}' % bad
    )
    rc, out = page(capsys, app)
    assert rc == 2 and out["errors"][0]["code"] == "page_check_invalid"


def test_stale_review_is_refused(app, capsys, tmp_path):
    # the tailored CV changes after check-review ran (e.g. after step 9): the old review must not be shown
    sel = json.loads(SELECTION.read_text())
    sel["swaps"] = sel["swaps"][:1]
    newsel = app / "newsel.json"
    newsel.write_text(json.dumps(sel))
    assert run(capsys, "build", "--master", MASTER, "--selection", newsel, "--out", app / "tailored.yaml")[0] == 0
    rc, out = page(capsys, app, selection=newsel)
    assert rc == 1 and out["errors"][0]["code"] == "review_stale"
    assert "check-review" in out["errors"][0]["message"]
    assert not (app / "review.html").exists()


# --- the posting link --------------------------------------------------------

POSTING_URL = "https://jobs.example.com/acme/123"


def test_posting_link_right_after_title(app, capsys):
    rc, _ = page(capsys, app, **{"--posting-url": POSTING_URL})
    assert rc == 0
    h = html_of(app)
    assert (
        "<h1>CV review: Acme Analytics — Senior Data Engineer</h1>"
        f'<p class="posting">Posting: <a href="{POSTING_URL}" target="_blank" rel="noopener noreferrer">'
        f"{POSTING_URL}</a></p><section>"
    ) in h
    assert_well_formed(h)


def test_posting_link_is_escaped_in_href_and_text(app, capsys):
    url = 'https://jobs.example.com/a?x=1&y="<b>"'
    assert page(capsys, app, **{"--posting-url": url})[0] == 0
    h = html_of(app)
    esc = "https://jobs.example.com/a?x=1&amp;y=&quot;&lt;b&gt;&quot;"
    assert f'<a href="{esc}" target="_blank" rel="noopener noreferrer">{esc}</a>' in h
    assert "<b>" not in h and url not in h


def test_no_posting_line_without_the_flag(app, capsys):
    assert page(capsys, app)[0] == 0
    assert 'class="posting"' not in html_of(app)


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:text/html,x", "//jobs.example.com/x"])
def test_bad_posting_url_exit_2_nothing_written(app, capsys, bad):
    rc, out = page(capsys, app, **{"--posting-url": bad})
    assert rc == 2 and out["errors"][0]["code"] == "url_invalid"
    assert "--posting-url" in out["errors"][0]["message"]
    assert not (app / "review.html").exists()


def test_review_page_function_takes_posting_url(app):
    from cv_skills.reviewpage import review_page

    out = app / "review.html"
    args = (MASTER, SELECTION, app / "tailored.yaml", app / "review.verified.json", app / "page-check.json", out)
    review_page(*args, posting_url=POSTING_URL)
    assert f'href="{POSTING_URL}"' in out.read_text(encoding="utf-8")
    review_page(*args)  # the default: no link
    assert 'class="posting"' not in out.read_text(encoding="utf-8")


# --- chosen titles -------------------------------------------------------------


def test_chosen_title_is_marked_and_listed_with_swaps(tmp_path, capsys, title_app):
    h = page_for(tmp_path, capsys, *title_app("Senior ML Engineer"))
    assert_well_formed(h)
    mark = '<mark class="swap title" title="default: Senior Data Engineer">Senior ML Engineer</mark><sup>[T1]</sup>'
    assert h.count(mark) == 1
    # Swap numbering is unchanged: three swaps, [1]..[3].
    assert len(re.findall(r'<mark class="swap"', h)) == 3
    assert "<sup>[1]</sup>" in h and "<sup>[3]</sup>" in h and "<sup>[4]</sup>" not in h
    assert "Changes (4)" in h
    changes = h.split('id="changes"')[1].split("</table>")[0]
    assert changes.count("<tr") == 5  # header + 3 swaps + 1 title
    assert (
        '<tr class="title"><td>T1</td><td><code>experience.0.position</code> (title)</td>'
        "<td><del>Senior Data Engineer</del></td><td><ins>Senior ML Engineer</ins></td></tr>"
    ) in changes


def test_default_title_is_not_marked(tmp_path, capsys, title_app):
    h = page_for(tmp_path, capsys, *title_app("Senior Data Engineer"))
    assert "swap title" not in h and "[T1]" not in h and "Changes (3)" in h
    assert "Senior Data Engineer" in h


def test_title_only_change_shows_the_table(tmp_path, capsys, title_app):
    master, selection = title_app("Senior ML Engineer")
    sel = json.loads(selection.read_text())
    sel["swaps"] = []
    selection.write_text(json.dumps(sel))
    h = page_for(tmp_path, capsys, master, selection)
    assert "Changes (1)" in h and "No term swaps" not in h
    assert h.split('id="changes"')[1].split("</table>")[0].count("<tr") == 2


def test_title_text_is_html_escaped(tmp_path, capsys, title_app):
    master, selection = title_app(
        "Analytics & <ML> Engineer",
        variants="Analytics & <ML> Engineer",
        replace=[("position: Senior Data Engineer", 'position: Data "&" <Platform> Engineer')],
    )
    h = page_for(tmp_path, capsys, master, selection)
    assert_well_formed(h)
    assert "<ML>" not in h and "<Platform>" not in h
    assert (
        '<mark class="swap title" title="default: Data &quot;&amp;&quot; &lt;Platform&gt; Engineer">'
        "Analytics &amp; &lt;ML&gt; Engineer</mark>"
    ) in h
    assert "<td><del>Data &quot;&amp;&quot; &lt;Platform&gt; Engineer</del></td>" in h
    assert "<td><ins>Analytics &amp; &lt;ML&gt; Engineer</ins></td>" in h
