"""Static checks on workflow/tailor.yaml beyond what `pawl validate` covers."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from cv_skills.steps import ADAPTERS

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / "workflow/tailor.yaml"
PSEUDO_KEYS = {"run_id", "step", "attempt", "visits", "last_error", "blocked_reason"}


def _load() -> dict:
    yaml = YAML(typ="safe")
    # pawl's `!cmd` context entries: a command string.
    yaml.constructor.add_constructor("!cmd", lambda loader, node: loader.construct_scalar(node))
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.mark.skipif(shutil.which("pawl") is None, reason="pawl is not on PATH")
def test_pawl_validate_clean():
    r = subprocess.run(["pawl", "validate", "--path", str(WORKFLOW)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""


def test_every_step_sh_call_names_an_adapter():
    text = WORKFLOW.read_text(encoding="utf-8")
    names = re.findall(r"scripts/tailor/step\.sh ([a-z-]+)", text)
    assert names
    assert sorted(set(names) - set(ADAPTERS)) == []


def test_every_key_is_declared():
    wf = _load()
    declared = set(wf["args"]) | set(wf["state"]) | PSEUDO_KEYS
    used = set(re.findall(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}", WORKFLOW.read_text(encoding="utf-8")))
    assert sorted(used - declared) == []


def test_agent_retries_have_a_fixed_attempt_key():
    """pawl's default attempt key hashes the failure text, so changing errors would never run out."""
    for step in _load()["steps"]:
        if step["kind"] == "agentic" and step.get("attempts", 1) > 1:
            assert "attempt_key" in step, step["id"]


def test_human_steps_carry_the_review_page_and_summary():
    steps = {s["id"]: s for s in _load()["steps"]}
    question = steps["human_review"]["question"]
    assert "${review_html}" in question and "${summary}" in question
    for s in steps.values():
        if s["kind"] == "human":
            assert s["timeout"] == "168h"
            assert s["outcomes"]["timeout"] == "blocked"


def test_inputs_are_pinned_by_an_invariant():
    """Agents can write files, so the master and requirements.json are pinned in engine state."""
    wf = _load()
    checks = [inv["check"] for inv in wf["invariants"]]
    assert checks == [
        "scripts/tailor/step.sh pinned ${master} ${master_sha256} ${app_dir} ${requirements_sha256}"
        " ${summary_guide} ${guide_sha256}"
    ]
    steps = {s["id"]: s for s in wf["steps"]}
    assert steps["floor"]["run"] == "scripts/tailor/step.sh floor ${master} ${page_limit} ${summary_guide} ${max_bullets}"
    assert {"master_sha256", "guide_sha256", "summary_section"} <= set(steps["floor"]["writes"])
    assert "requirements_sha256" in steps["name_folder"]["writes"]


def test_rerun_review_is_reset_after_use():
    """pawl keeps an agent's previous value when its return omits the key."""
    steps = {s["id"]: s for s in _load()["steps"]}
    assert "rerun_review" in steps["build_changes"]["writes"]


def test_visit_caps_have_routes():
    """A blocked run re-enters the exhausted step on resume and exhausts again, so caps are routed."""
    steps = {s["id"]: s for s in _load()["steps"]}

    def exhausted(step):
        if "exhausted" in step.get("outcomes", {}):
            return step["outcomes"]["exhausted"]
        return next((c["next"] for c in step.get("catch", []) if c["on"] == "exhausted"), None)

    assert exhausted(steps["draft"]) == "review_page"
    assert exhausted(steps["recruiter"]) == "recheck_review"
    assert exhausted(steps["apply_changes"]) == "too_many_rounds"
    assert exhausted(steps["human_review"]) == "too_many_rounds"
    rounds = steps["apply_changes"]["max_visits"]
    for sid in ("human_review", "build_changes", "recheck_review", "review_page", "summary_route"):
        assert steps[sid]["max_visits"] > rounds, sid
    for sid in ("summary_route", "write_summary", "build_summary"):
        assert exhausted(steps[sid]) == "summary_cap_reached", sid
    for sid in ("summary_writer_failed", "summary_build_failed", "summary_cap_reached"):
        assert "max_visits" not in steps[sid], sid  # bounded by the capped steps that lead to them
    # Each change round can visit the summary steps twice (write, then build; or a defensive too_long loop),
    # so their caps exceed two per round, or a long session would exhaust them before apply_changes' cap.
    for sid in ("write_summary", "build_summary"):
        assert steps[sid]["max_visits"] > 2 * rounds, sid


AGENT_READS = {
    "browser_fetch": set(),
    "requirements": {"${app_dir}/posting.txt"},
    "draft": {
        "${master}",
        "${app_dir}/posting.txt",
        "${app_dir}/requirements.json",
        "${app_dir}/review.verified.json",
        "${app_dir}/selection.json",
    },
    "recruiter": {"${app_dir}/cv.txt", "${app_dir}/posting.txt", "${app_dir}/requirements.json"},
    "apply_changes": {"${app_dir}/selection.json", "${app_dir}/review.verified.json", "${master}"},
    "write_summary": {
        "${summary_guide}",
        "${app_dir}/cv.txt",
        "${app_dir}/posting.txt",
        "${app_dir}/requirements.json",
    },
}


def _agents() -> dict:
    return {s["id"]: s for s in _load()["steps"] if s["kind"] == "agentic"}


def test_agents_read_their_own_inputs():
    """Inputs are files the agent reads (verbatim by construction), never context a driver pastes."""
    agents = _agents()
    assert set(agents) == set(AGENT_READS)
    for sid, step in agents.items():
        assert "context" not in step, sid
        desc = " ".join(step["description"].split())
        if AGENT_READS[sid]:
            m = re.search(r"Read only these files: (.+?)\. ", desc)
            assert m, sid
            assert set(m.group(1).replace(" and ", ", ").split(", ")) == AGENT_READS[sid], sid
            assert "Read" in step["subagent_args"]["tools"], sid
        else:
            assert "Read no files" in desc, sid


def test_retried_agents_may_overwrite_their_own_attempt():
    """A retry runs on the tree the failed attempt left: its own output file is already there."""
    agents = _agents()
    for sid, own in (("draft", "selection.json"), ("recruiter", "review.json"), ("write_summary", "summary.json")):
        desc = " ".join(agents[sid]["description"].split())
        assert f"If ${{app_dir}}/{own} already exists" in desc, sid
        assert "previous attempt" in desc, sid


def test_agents_state_their_exact_return():
    for sid, step in _agents().items():
        desc = " ".join(step["description"].split())
        m = re.search(r"Return exactly (\{.+?\}) and nothing else\.", desc)
        assert m, sid
        assert set(re.findall(r'"(\w+)":', m.group(1))) == set(step["writes"]), sid


def test_no_example_cv_names_in_the_workflow():
    """The review question's example must be generic, not the fixture's (e.g. "Copperline")."""
    master = YAML(typ="safe").load((REPO / "examples/master.yaml").read_text(encoding="utf-8"))
    names = {master["cv"]["name"], *master["cv"]["name"].split()}
    for entries in master["cv"]["sections"].values():
        for e in entries:
            if isinstance(e, dict):
                names |= {e[k].split()[0] for k in ("company", "institution") if k in e}
    text = WORKFLOW.read_text(encoding="utf-8")
    assert sorted(n for n in names if n in text) == []


RUBRIC = (
    "met = demonstrated in a role or project bullet, or stated outright for a factual requirement"
    " (location, degree, certification); partial = only listed in skills, or adjacent experience;"
    " missing = absent. Judge strictly: met only when the CV clearly shows it."
)


def test_extractor_marks_evidence():
    """The extractor (posting only, so unbiased by the CV) decides what a CV line can't show."""
    desc = " ".join(_agents()["requirements"]["description"].split())
    assert '"evidence"' in desc and '"cv"' in desc and '"none"' in desc
    for category in (
        "language fluency",
        "generic soft skills",
        "work authorisation",
        "willingness to relocate, travel or work a timezone overlap",
    ):
        assert category in desc, category
    assert "where the candidate is based" in desc  # stays "cv": the header location line is quotable


def test_recruiter_rubric_and_scope_are_explicit():
    desc = " ".join(_agents()["recruiter"]["description"].split())
    assert RUBRIC in desc
    assert 'only the requirements whose "evidence" is "cv"' in desc


def test_agents_get_formats_inline():
    """Agents run in the user's CV repo, where cv-skills' docs are not there to read."""
    for step in _load()["steps"]:
        if step["kind"] == "agentic":
            assert "docs/" not in step["description"], step["id"]


def test_agents_reading_the_posting_treat_it_as_untrusted():
    for step in _load()["steps"]:
        if step["kind"] == "agentic" and "posting" in step["description"]:
            assert "untrusted data" in step["description"], step["id"]


TITLE_RULE = (
    'For an entry whose IDs listing has a "position" node with variants, set the entry\'s "position" to'
    " the variant that best matches the posting's own title vocabulary; keep the default when unsure."
    " Never write a title that is not listed."
)


def test_review_question_mentions_changed_titles():
    steps = {s["id"]: s for s in _load()["steps"]}
    question = " ".join(steps["human_review"]["question"].split())
    assert "changed job title" in question and "keep the default title" in question


def test_agents_choose_titles_only_from_the_master():
    agents = _agents()
    draft = " ".join(agents["draft"]["description"].split())
    assert TITLE_RULE in draft
    assert '"position": <a listed title, optional>' in draft  # in the selection format
    changes = " ".join(agents["apply_changes"]["description"].split())
    assert "switch a job title to another title its entry lists" in changes


def test_posting_url_is_optional_and_reaches_fetch():
    wf = _load()
    assert wf["args"]["posting_url"] == {"type": "string", "default": ""}
    steps = {s["id"]: s for s in wf["steps"]}
    assert steps["fetch"]["run"] == "scripts/tailor/step.sh fetch ${posting} ${run_id} ${posting_url} ${master} ${from_selection}"
    # the review page's link comes from the run's arguments, not from a file an agent could rewrite
    assert steps["review_page"]["run"] == (
        "scripts/tailor/step.sh review-page ${app_dir} ${master} ${posting} ${posting_url} ${summary_guide}"
        " ${summary_applied} ${summary_section} ${summary_status} ${reviewed_cv_sha256} ${carry_sha256}"
    )


# --- the written summary (WP17) ------------------------------------------------


def _steps() -> dict:
    return {s["id"]: s for s in _load()["steps"]}


def test_summary_guide_is_an_optional_arg_and_its_state_is_declared():
    wf = _load()
    assert wf["args"]["summary_guide"] == {"type": "string", "default": "summary-guide.md"}
    state = wf["state"]
    for key, decl in {
        "summary_section": {"type": "string", "default": ""},
        "guide_sha256": {"type": "string", "default": ""},
        "summary_applied": {"type": "boolean", "default": False},
        "summary_choice": {"type": "string", "default": "write"},
        "summary_notes": {"type": "string", "default": ""},
        "selection_sha256": {"type": "string", "default": ""},
        "summary_status": {"type": "string", "default": "not_reached"},
    }.items():
        assert state[key] == decl, key
    assert state["sentences"]["type"] == "integer"


def test_the_recruiter_never_sees_the_summary_section():
    steps = _steps()
    runs = {
        "build_draft": "build-draft ${app_dir} ${master} ${page_limit} ${summary_section} ${max_bullets}",
        "check_review": "check-review ${app_dir} ${redrafts_done} ${prev_supported} ${after_changes} ${summary_section}",
        "build_changes": (
            "build-changes ${app_dir} ${master} ${page_limit} ${rerun_review} ${summary_section}"
            " ${summary_choice} ${summary_request} ${max_bullets} ${reviewed_cv_sha256}"
        ),
        "recheck_review": "recheck ${app_dir} ${summary_section}",
    }
    for sid, cmd in runs.items():
        assert steps[sid]["run"] == f"scripts/tailor/step.sh {cmd}", sid
    assert steps["recruiter"]["postcondition"] == "scripts/tailor/step.sh check-review-shape ${app_dir} ${summary_section}"


def test_the_summary_is_routed_after_the_review_settles():
    steps = _steps()
    assert steps["check_review"]["outcomes"] == {"pass": "summary_route", "human": "summary_route", "redraft": "draft"}
    assert steps["recheck_review"]["next"] == "summary_route"
    assert steps["build_changes"]["outcomes"] == {"rerun": "recruiter", "recheck": "recheck_review"}
    route = steps["summary_route"]
    assert route["kind"] == "deterministic"
    assert route["run"] == (
        "scripts/tailor/step.sh summary-route ${app_dir} ${master} ${summary_guide} ${summary_section} ${summary_choice}"
    )
    # the selection is pinned for build_summary, and a forced write clears the user's old rewrite notes
    assert route["emits"] == "pairs" and set(route["writes"]) == {
        "summary_applied", "selection_sha256", "summary_notes", "summary_status"
    }
    assert route["outcomes"] == {
        "none": "review_page",
        "original": "review_page",
        "keep": "build_summary",
        "write": "write_summary",
    }


def test_write_summary_step():
    step = _steps()["write_summary"]
    assert step["kind"] == "agentic"
    assert step["subagent_args"]["model"] == "opus"
    assert step["subagent_args"]["tools"] == ["Read", "Write", "Bash(.claude/workflows/scripts/tailor/cv.sh:*)"]
    # the page fit is part of the postcondition, so a too-long summary is retried with the reason
    assert step["postcondition"] == (
        "scripts/tailor/step.sh summary-check ${app_dir} ${master} ${summary_guide} ${page_limit}"
    )
    assert (step["attempts"], step["attempt_key"], step["max_visits"]) == (3, "summary-${visits}", 24)
    assert step["next"] == "build_summary"
    assert {c["on"]: c["next"] for c in step["catch"]} == {
        "exhausted": "summary_cap_reached", "failure": "summary_writer_failed"
    }
    desc = " ".join(step["description"].split())
    for phrase in (
        "${summary_notes}",
        "guide.facts.<n>",
        "<entry id>.position",
        "never cite",
        "a guide fact",
        "${app_dir}/summary.json",
        "cv.sh ids --master ${master}",
        "inline **bold** only",
        "number word",
    ):
        assert phrase in desc, phrase


def test_build_summary_step():
    step = _steps()["build_summary"]
    assert step["run"] == (
        "scripts/tailor/step.sh build-summary ${app_dir} ${master} ${page_limit} ${summary_guide} ${summary_section}"
        " ${selection_sha256}"
    )
    assert step["emits"] == "pairs" and set(step["writes"]) == {
        "summary_applied", "summary_choice", "summary_notes", "summary_status"
    }
    assert step["outcomes"] == {"ok": "review_page", "too_long": "write_summary"}
    assert step["max_visits"] == 24
    assert {c["on"]: c["next"] for c in step["catch"]} == {
        "exhausted": "summary_cap_reached", "failure": "summary_build_failed"
    }


def test_summary_status_is_set_only_by_deterministic_steps():
    """The review page's note says why the CV shows its own summary; only code may decide that."""
    steps = _steps()
    writers = {sid for sid, s in steps.items() if "summary_status" in (s.get("writes") or [])}
    assert writers == {
        "summary_route", "build_summary", "summary_writer_failed", "summary_build_failed", "summary_cap_reached"
    }
    assert all(steps[sid]["kind"] == "deterministic" for sid in writers)
    for sid, reason in {
        "summary_writer_failed": "writer_failed",
        "summary_build_failed": "build_failed",
        "summary_cap_reached": "cap_reached",
    }.items():
        assert steps[sid]["run"] == f"scripts/tailor/step.sh summary-status {reason}", sid
        assert steps[sid]["writes"] == ["summary_status"], sid
        assert steps[sid]["next"] == "review_page", sid
    # the draft's cap goes straight to the page: no summary step ran, so the status keeps its default
    draft_catch = {c["on"]: c["next"] for c in steps["draft"]["catch"]}
    assert draft_catch["exhausted"] == "review_page"
    assert _load()["state"]["summary_status"]["default"] == "not_reached"


def test_change_requests_choose_the_summary():
    """The agent reports only what the request asked; a deterministic step folds it into the run's
    choice, so an unrelated change round never undoes an earlier "keep the original summary"."""
    steps = _steps()
    step = steps["apply_changes"]
    assert "summary_choice" not in step["writes"]
    assert step["writes"]["summary_request"] == {"type": "string"}
    assert step["writes"]["summary_notes"] == {"type": "string"}
    desc = " ".join(step["description"].split())
    for request in ('"none"', '"rewrite"', '"original"', '"written"'):
        assert request in desc, request
    assert set(steps["build_changes"]["writes"]) >= {"summary_choice", "summary_request"}
    assert _load()["state"]["summary_request"] == {"type": "string", "default": "none"}
    # an unknown summary_request is the agent's to fix (a retry), never a blocked run
    assert step["postcondition"].startswith(
        "scripts/tailor/step.sh check-draft ${app_dir} ${master} ${page_limit} ${summary_request}"
    )


def test_max_steps_covers_the_summary_rounds():
    assert _load()["max_steps"] >= 260


def test_drafters_curate_rather_than_fill():
    """Tailored CVs read as dense: both selection prompts say to curate (relevance cut-off, taper by
    age, skills the posting asks for) and that white space is fine, never to fill the page."""
    agents = _agents()
    draft = " ".join(agents["draft"]["description"].split())
    changes = " ".join(agents["apply_changes"]["description"].split())
    for desc in (draft, changes):
        assert "full last page" not in desc
        assert "Do not add bullets only to fill space" in desc or "do not add bullets only to fill space" in desc
        assert "a short last page or white space is fine" in desc
        assert "senior scope (leading, cross-team work, production ownership)" in desc
        assert "asks for or that back a selected bullet" in desc  # skills items
        assert "drop bullets before dropping roles" in desc
        assert "shorten or drop bullets" not in desc  # the drafter selects; it cannot shorten a bullet


def test_drafters_must_keep_the_summary_section():
    """With a summary guide, no draft may drop its section (WP19); a request to drop the summary keeps the CV's own."""
    steps = _steps()
    assert steps["draft"]["postcondition"].startswith(
        "scripts/tailor/step.sh check-draft ${app_dir} ${master} ${page_limit} none ${summary_section}"
    )
    assert steps["apply_changes"]["postcondition"].startswith(
        "scripts/tailor/step.sh check-draft ${app_dir} ${master} ${page_limit} ${summary_request} ${summary_section}"
    )
    rule = 'If "${summary_section}" is not empty, that section must stay in the selection'
    for sid in ("draft", "apply_changes"):
        assert rule in " ".join(steps[sid]["description"].split()), sid
    changes = " ".join(steps["apply_changes"]["description"].split())
    assert (
        'If the request asks to drop or remove the summary, keep the ${summary_section} section selected and report'
        ' summary_request "original"'
    ) in changes


def test_max_bullets_per_entry():
    """Lots in the latest role, nothing in the others. A ceiling per entry, checked in code (WP20)."""
    wf = _load()
    assert wf["args"]["max_bullets"] == {"type": "integer", "default": 4}
    steps = _steps()
    assert steps["draft"]["postcondition"] == (
        "scripts/tailor/step.sh check-draft ${app_dir} ${master} ${page_limit} none ${summary_section} ${max_bullets}"
    )
    assert steps["apply_changes"]["postcondition"] == (
        "scripts/tailor/step.sh check-draft ${app_dir} ${master} ${page_limit} ${summary_request} ${summary_section}"
        " ${max_bullets}"
    )
    assert steps["build_draft"]["run"].endswith(
        "build-draft ${app_dir} ${master} ${page_limit} ${summary_section} ${max_bullets}"
    )
    assert steps["floor"]["run"].endswith(" ${summary_guide} ${max_bullets}")  # an invalid value blocks first
    assert steps["build_changes"]["run"].endswith(
        " ${summary_section} ${summary_choice} ${summary_request} ${max_bullets} ${reviewed_cv_sha256}"
    )
    taper = (
        "The most recent roles get the most bullets, up to the limit; use the room the limit frees for the most"
        " relevant bullets of the other roles, older roles fewer."
    )
    # Max 4, and recent roles never bare (min 1); older roles may be.
    recent = (
        "A role that is current or ended within about the last 5 years, and whose master entry has bullets, keeps"
        " at least 1; older roles may keep none."
    )
    for sid in ("draft", "apply_changes"):
        desc = " ".join(steps[sid]["description"].split())
        assert "At most ${max_bullets} bullets per entry." in desc, sid
        assert taper in desc and recent in desc, sid
        assert "keeps 0-2" not in desc and "any role may keep no bullets" not in desc, sid
        assert "a pinned role may keep no bullets" not in desc, sid  # would contradict the min for a recent role


# --- carrying a run across a master change (from_selection) ------------------------


def test_from_selection_is_an_optional_arg_that_reaches_fetch_and_name_folder():
    wf = _load()
    assert wf["args"]["from_selection"] == {"type": "string", "default": ""}
    assert wf["args"]["changes"] == {"type": "string", "default": ""}
    steps = {s["id"]: s for s in wf["steps"]}
    assert steps["fetch"]["outcomes"]["carried"] == "name_folder"  # no new requirements: the earlier run's
    assert steps["name_folder"]["run"] == "scripts/tailor/step.sh name-folder ${app_dir} ${from_selection}"
    assert steps["name_folder"]["outcomes"] == {"draft": "draft", "carry": "carry"}


def test_a_carried_run_never_redrafts():
    steps = {s["id"]: s for s in _load()["steps"]}
    carry = steps["carry"]
    assert carry["kind"] == "deterministic"
    assert carry["outcomes"] == {"ok": "build_draft", "fix": "apply_changes"}
    assert set(carry["writes"]) == {"after_changes", "carry_sha256", "carry_note", "change_request", "summary_choice"}
    assert carry["run"] == (
        "scripts/tailor/step.sh carry ${app_dir} ${master} ${from_selection} ${page_limit} ${summary_guide}"
        " ${summary_section} ${max_bullets} ${changes}"
    )


def test_the_review_question_carries_the_carry_note_and_add_by_id():
    steps = {s["id"]: s for s in _load()["steps"]}
    assert "${carry_note}" in steps["human_review"]["question"]
    assert '"add experience.0.highlights.4"' in steps["human_review"]["question"]
    assert '"add experience.0.highlights.4"' in steps["apply_changes"]["description"]


def test_the_gate_follows_the_reviewed_text():
    wf = _load()
    steps = {s["id"]: s for s in wf["steps"]}
    assert "reviewed_cv_sha256" in steps["check_review"]["writes"]
    assert wf["state"]["reviewed_cv_sha256"] == {"type": "string", "default": ""}
    assert steps["build_changes"]["run"].endswith(" ${reviewed_cv_sha256}")


def test_the_pinned_message_offers_a_carried_run():
    (inv,) = _load()["invariants"]
    assert "from_selection=" in inv["message"]
