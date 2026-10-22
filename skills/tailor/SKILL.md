---
name: tailor
description: Tailor the user's master CV (RenderCV YAML) to one job posting with the cv-skills `tailor` pawl workflow - entries selected and reordered plus listed term swaps only, a recruiter-style review, and the user's approval before cv.pdf is made. Use when the user asks to tailor their CV to a job posting, URL or posting file, or to resume a tailor run.
---

# cv-skills:tailor

Tailors the master CV to one posting. The `tailor` pawl workflow does the work. This skill installs
it into the CV repo and starts or resumes the run. The workflow makes every check in code; the run's
agents only make judgement calls. Never edit `master.yaml`, `selection.json` or `tailored.yaml`
yourself outside the run's steps.

1. **Find the CV repo.** Run from the repo that holds the master CV (default `master.yaml` at its
   root). The workflow writes `applications/<date>-<company>-<role>/` there. If there is no master,
   point the user to `cv-skills:setup` and stop.

2. **Install the workflow.** Run this on every run, so plugin updates flow through. It overwrites
   only the files it owns:

   ```bash
   uv run --project "${CLAUDE_SKILL_DIR}/../.." --frozen --quiet cv-skills install-workflow --dest .claude/workflows
   ```

   It must print `{"ok": true, ...}`. If it refuses with `run_live`, a `tailor` run is still live
   in this repo and installing a different workflow would strand it (pawl resumes a run only on the
   workflow it started with): do not reinstall, resume that run with `pawl run tailor` in step 3, or
   ask the user whether to abandon it first. If `uv` is missing, the user needs to install uv
   (RenderCV comes with the plugin's locked environment); for a missing `pdftotext`, poppler.

3. **Start or resume the run** with the agent-pawl `pawl` skill (`agent-pawl:pawl`), and follow it
   exactly (DISPATCH → subagent, ASK → AskUserQuestion, submit, until TERMINAL). Each step's
   description is complete: it names the only files its agent may read and the exact JSON to return.
   Pass it to the subagent verbatim with the step's `model` and `tools`, and add nothing beyond the
   working directory and the tool list the `pawl` skill tells you to state: no pasted or summarised
   file contents, no extra rules.

   ```
   pawl run tailor posting=<URL or path to a posting file> [posting_url=<URL>] [page_limit=<pages, default 2>] [max_bullets=<per entry, default 4>] [master=<path>] [summary_guide=<path>]
   ```

   Defaults: `page_limit=2`, `max_bullets=4` (the most bullets any one entry keeps, checked in code),
   `master=master.yaml`, `summary_guide=summary-guide.md`. Pass a value only when the user asks for
   one. With `page_limit=1`, warn the user that the pinned entries and the summary can leave almost
   no room for bullets (the README's "Run options").

   With a posting file, also pass `posting_url=<the posting's web address>` when you know it, so the
   review page links the original posting. A URL `posting` is linked on its own; `posting_url` wins
   over it.

   An optional `summary-guide.md` at the repo root turns on the written summary: the CV's summary
   paragraph is written for the posting, checked against its cited sources, and marked on the review
   page (see the README's "Written summary"). If the guide is elsewhere, pass
   `summary_guide=<path>`. Without a guide file the master's summary is used unchanged.

   With no arguments, `pawl run tailor` resumes the repo's paused run (e.g. one parked at the review
   question or blocked).

   **The master changed during a run.** A run is pinned to the master it started with: after a
   master edit it blocks with `input_changed`, and resuming cannot fix that. Do not start a plain
   fresh run, which would redraft everything the user already reviewed. Carry the run instead:

   ```
   pawl run tailor --fresh posting=<the same posting> from_selection=<that run's application folder>/selection.json [changes="<the user's change request>"]
   ```

   It reuses that run's posting and requirements, moves its selection (and written summary) onto
   the new master by matching each item's text, and goes straight to the recruiter review and the
   review question, with no new draft. Pass the user's pending change request (e.g. "add
   experience.0.highlights.4" for a bullet they just added to the master) as `changes=`, so it is
   applied before the first review. The same works from any earlier run's folder, approved or not. If pawl refuses with "workflow file has changed since run … started", the
   plugin was updated while that run was paused, and a run only resumes on the workflow it started
   with. Tell the user, and offer a fresh run (`pawl run tailor --fresh posting=…`); the old
   run's application folder stays as it is.

4. **At the review question** (`human_review`), the question names the review page
   (`review.html`, absolute path) and a one-line summary. Tell the user to open it before answering:
   it shows the CV with every word swap and changed job title highlighted, with the original on
   hover and in a numbered list of changes. Their answer is
   `approve`, `stop`, or free-text changes (the README's "Change requests" and "Requirements and the
   review page"). A change can add any master item by its ID ("add experience.0.highlights.4"). In a
   carried run the question has one more line, what the carry kept and could not; the page lists the
   items the master changed, the ones not carried and the ones new in the master, by ID.

5. **At the end**, report the terminal message: where `cv.pdf` and `cv.md` are, or why the run
   paused. Nothing is sent or uploaded anywhere; applying to the job is the user's to do (the
   README's "Output and privacy").

The README is the reference for every option and behaviour: "Tailoring a CV", "Run options",
"Written summary" and "The master file".
