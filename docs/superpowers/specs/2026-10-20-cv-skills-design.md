# cv-skills — design

Date: 2026-10-20. Status: approved 2026-10-20.

## Intent

You keep one curated master CV. For each job posting you want a CV tailored to it,
fast, without the tool inventing anything, and with a human signing off before it
goes out.

cv-skills is an open-source Claude Code plugin that does this. It is a thin layer:
**RenderCV** renders, **pawl** (dcferreira/agent-pawl) drives the steps and
enforces the checks, and a few deterministic scripts do everything that can be
checked in code. The LLM does only the parts that need judgement: reading the
posting, choosing and ordering entries, swapping terms for the posting's words,
and grading the result as a recruiter would.

The author is the first user (dogfooding with their own CV), but the tool is generic.

### Why it exists (prior art, 2026-10)

- **Chameleon** (davidalecrim1/chameleon, MIT, 32★) is the closest: Claude Code
  skills that tailor a master RenderCV YAML and render a PDF. It rewrites the
  summary (prose), has no review step, no highlighting of what changed, and its
  no-invention rule is a prompt instruction only.
- **cvloom** (PyPI) selects by tag and rates templates against text extractors,
  but its AI features have no no-invention guarantee.
- RenderCV, YAMLResume, polycv and JSON Resume are renderers and formats, not
  tailoring tools.
- No tool found combines selection-only tailoring, a visible record of every
  word change, and a human approval gate. That is the gap.

## Goals

1. One master CV file, in RenderCV YAML, holding everything.
2. For one posting, produce a tailored CV that fits a page limit (per-run setting,
   default 2 pages).
3. The tailored CV contains only master content: entries selected, dropped and
   reordered, plus term swaps toward the posting's vocabulary. Nothing else.
4. Every swap is shown to the human, highlighted, with the original word.
5. A recruiter-style review grades the draft against the posting's requirements
   and drives up to two redrafts before the human sees it.
6. Human approves or requests changes; only after approval is the PDF made.
7. The PDF's text extracts cleanly (ATS-safe), verified with `pdftotext`.
8. Each application is kept as a folder, so "what did I send to X?" has an answer.

## Non-goals

- Writing or rephrasing prose. No generated summaries, no rewritten bullets.
  *Amended 2026-10-21:* one explicit exception: the summary paragraph may be written
  per posting, and only when the CV repo has a summary guide (the user's questions, style and fixed facts).
  It is written last, after the selection has settled; every sentence cites the CV entries or guide
  facts it draws on, and a check holds every number and proper noun to those sources; the recruiter
  never sees it and it never counts as evidence; the review page marks it "written for this posting"
  with its sources. Bullets are still never rewritten.
- Cover letters.
- Languages other than English.
- llms.txt and JSON Resume output (cut from v1; may come later).
- A subjective overall band, positioning or seniority commentary (cut from v1).
- A GUI or web app beyond one static HTML review page.
- Tracking application status (applied / interview / rejected).

## Anti-goals (would be a failure even if it technically works)

- A CV that claims something the master doesn't. The structural guarantee is:
  output text = master text + listed swaps, nothing else.
- A word change that reaches the PDF without having been shown to the human.
- Personal CV data in the public plugin repo.
- Hidden text, white text, or anything aimed only at AI screeners.

## Constraints

- Uses Claude Code. Runs in the user's own interactive Claude Code session on their
  own subscription; no API key, no Agent SDK (the SDK may not offer claude.ai login
  to third-party tools).
- Depends on pawl (installed as its own plugin) and RenderCV (Python, MIT).
- **pawl does not yet support plugin-shipped workflows** (agent-pawl
  `docs/status.md`, "Not built"). Workflows resolve from the repo's
  `.claude/workflows/<name>.yaml` or `~/.claude/workflows/`. The design accounts
  for this (see Packaging).
- pawl agentic steps can only end `success`/`failure`; named outcomes come from
  deterministic steps. Agent outputs go through typed `writes:`.

## Decisions

- **Form:** a Claude Code plugin named `cv-skills`, built on RenderCV and pawl.
  Not a standalone app.
- **Repos:** the tool repo is separate from the user's private CV data repo.
- **Swaps are free-form.** No whitelist of allowed swaps, no cap on size. A soft
  rule in the drafter's prompt: a swap renames something you know under the
  posting's term and must not change the meaning. The human review is the safety
  net. Every swap is highlighted there.
- **Page fit is checked by a script**, which the drafter calls as often as it
  likes, rather than estimating.
- **Requirements come from a separate agent** that sees only the posting.
  The list is taken as is: the user does not review or edit it, and it stays
  fixed for the run so the recruiter's gate doesn't move.
- **Recruiter score:** per-requirement met / partial / missing with verified
  quotes. Gating is objective; nothing subjective gates.
- **Recruiter isolation is best-effort.** It runs as an ordinary pawl agentic
  step given only the CV text, posting and requirements. No enforced separation
  from the drafting context; this is not a scientific setup.
- **Selection is coarse as well as fine.** The drafter decides what goes in at
  every level: whole sections, whole entries (e.g. 20 past positions trimmed to
  the 4 that matter for a 1-page CV), individual bullets, and individual items in
  skills lines.
- **Pinned entries:** `pin: yes` in the master marks entries that are always
  kept (e.g. contact, current role, education).
- **Where swaps may apply (my call):** bullets, entry summaries and skills lines.
  Never in position titles, the headline, company or institution names, or dates.
  `build` rejects swaps elsewhere.
  *Amended 2026-10-21:* job titles are still never swapped, but an entry's title may switch to one
  of the alternative titles the user listed for it in the master (`title_variants`); anything else
  is rejected, and a switched title is shown on the review page like a swap.
- **v1 outputs:** HTML review page, PDF, Markdown.

## The flow (one pawl workflow, `tailor`)

Input: a posting URL or a file with the posting text; page limit (default 2).

1. **Fetch posting** (deterministic). A script fetches the URL (or reads the file),
   converts to plain text, saves `posting.txt` in a new application folder
   `applications/<YYYY-MM-DD>-<company>-<role>/`. No other agent fetches the web.
   - **Fallback when the script can't get the text** (e.g. JavaScript-only job
     sites, login walls): if the user has a browser connected through the Claude
     in Chrome extension, an agentic step opens the URL in a new tab, reads the
     page text, and saves it as `posting.txt`. That step gets only the URL and the
     browser tools. It never sees the master CV. If no browser is connected, or
     that fails too, the run asks the user to paste the posting into a file.
2. **Extract requirements** (agentic). Sees only `posting.txt`. Writes
   `requirements.json`: a list of `{id, text, kind: must|nice, evidence: cv|none}`
   plus company and role. Treats the posting as untrusted data. The list is used as is.
   *Amended 2026-10-21:* `evidence: none` marks what no CV line can show with a quote
   (language fluency, generic soft skills, work authorisation, willingness to relocate
   or travel); dogfood showed such musts failing the gate on an unquotable line.
3. **Draft** (agentic). Sees the master, the posting and the requirements.
   Produces a **selection**, not YAML:
   - which sections, entries, bullets and skills items to include, in what order
     (anything not listed is dropped);
   - a list of swaps, each `{target_id, from, to}`.

   It calls the build and page-check scripts itself (below) and iterates until the
   draft fits.
4. **Check draft** (deterministic). Builds the tailored RenderCV YAML from master +
   selection. Fails back to step 3 if: an ID doesn't exist; a pinned entry is
   missing; a swap's `from` text isn't in its target, or the target is a field
   swaps may not touch; the page limit is exceeded. If pinned entries alone exceed
   the limit, the run stops with a clear error instead of looping.
5. **Recruiter review** (agentic). Given only the rendered CV text, the posting
   and the requirements list. For each requirement: `status` (met / partial /
   missing), `evidence_quote` (verbatim from the CV), and a short reason. Also
   `top_fixes` (non-gating suggestions) and `missed_requirements` (requirements it
   thinks step 2 missed, non-gating).
6. **Check review** (deterministic). Verifies each quote appears in the CV text
   (after normalising whitespace, dashes and markup); an unverifiable quote makes
   that requirement `missing`. Gate: every must-have is met or partial with a
   verified quote.
   *Amended 2026-10-21:* the gate (and the supported/total counts) covers only
   `evidence: cv` must-haves; the recruiter grades only those, and `none` ones are shown
   to the human "for you to judge", not gated, so the gate stays objective: every gated
   requirement needs a verified quote.
   - Pass → step 7.
   - Fail, and fewer than 2 redrafts so far, and the count of supported must-haves
     went up since the last round (or this is the first round) → back to step 3
     with the review as input. (A must-have the user genuinely lacks stops the
     loop through the "went up" rule rather than being chased.)
   - Otherwise → step 7 anyway, with the gaps shown. The human decides.
7. **Render review page** (deterministic). One static HTML file: the CV as it will
   look, every swap highlighted with the original word shown next to it (e.g. on
   hover and in a list of changes), the requirement-by-requirement table with
   quotes, `top_fixes`, `missed_requirements`, page count and space left.
8. **Human review** (human). Approve, or request changes in free text.
9. **Apply changes** (agentic, only on request). Turns the notes into concrete
   edits to the selection (include/exclude entries or bullets, undo swap N, add a
   swap), then back to step 4. On this path the recruiter loop is skipped unless
   the user asks for it.
10. **Final render** (deterministic). PDF and Markdown from the approved tailored
    YAML. Text-extraction check: `pdftotext` output, normalised (ligatures,
    hyphenation, whitespace), must contain every bullet's expected text in reading
    order. Fails loudly on missing text or garbled order.

### Scripts (deterministic, testable, no LLM)

- `fetch-posting` — URL or file → `posting.txt`.
- `build` — master + selection → tailored RenderCV YAML + `swaps` with positions.
  Errors list exactly what's wrong (unknown ID, swap text not found, pin missing).
- `page-check` — renders via RenderCV, returns JSON:
  `{pages, limit, fits, last_page_remaining_mm, last_page_fill_ratio}` (how much
  space is left at the bottom of the last page, so the drafter knows if there's
  room for one more bullet or how much to cut).
- `check-review` — recruiter output + CV text → verified statuses + gate result.
- `review-page` — tailored YAML + swaps + review → HTML.
- `final-render` — PDF + Markdown + the `pdftotext` check.

### The master file

RenderCV's YAML (its `cv:` / `design:` / `locale:` / `settings:` structure),
kept a **valid RenderCV file** so `rendercv render master.yaml` and editor schema
validation still work. Extra keys go only on entries, which RenderCV allows:

- `pin: yes` on entries that are always kept;
- optional `tags` to help the drafter (not required), as one comma-separated
  string (e.g. `tags: ml, infra`).

Both values are strings because RenderCV 2.8 needs string values for entry keys
(it crashes rendering a boolean `pin: true` or a list of tags).

*Amended 2026-10-21:* an experience entry may also carry `title_variants`, one string of
alternative job titles the user approves for it, separated by `;` (titles can contain commas,
rarely a semicolon), e.g. `title_variants: Senior Analytics Engineer; Senior ML Engineer`. The
entry's own `position` stays the default; a tailored CV may use the default or one listed variant.

**IDs (my call):** bullets in RenderCV are plain strings, so they can't carry an
`id` key. IDs are therefore path-based (e.g. `experience.2.highlights.3`),
computed by `build` from the master. They only need to be stable within one
run: the selection records a hash of the master it was made against, and `build`
refuses a selection whose hash doesn't match. Re-rendering an old application
uses its saved `tailored.yaml`, not the selection, so later edits to the master
don't break it.

The `build` script strips the extra keys before handing YAML to RenderCV.

### Application folder

`applications/<YYYY-MM-DD>-<company>-<role>/` in the user's CV repo:
`posting.txt`, `requirements.json`, `selection.json` (with swaps),
`review.json`, `tailored.yaml`, `review.html`, `cv.pdf`, `cv.md`.

## Packaging

- **Plugin repo** (`cv-skills`, public later): the skills, the `tailor` workflow
  template, the scripts, a fictional example CV for tests and docs. No real
  personal data.
- **Install:** the user installs two plugins, agent-pawl and cv-skills (stated in
  the install instructions), plus RenderCV and `pdftotext`.
- **Skills:**
  - `cv-skills:tailor`: copies the bundled `tailor` workflow (and the scripts it
    calls) into the CV repo's `.claude/workflows/`, then starts a `tailor` run
    for a posting. It copies on every run, so updates to the plugin flow through.
  - `cv-skills:setup`: run once in a CV repo. Checks dependencies (RenderCV,
    pdftotext, pawl) and converts an existing CV (PDF, LaTeX or RenderCV YAML)
    into a master file with IDs, for the user to curate.
- Why the copy: `pawl run` takes only a workflow name, resolved from the repo's
  `.claude/workflows/` or `~/.claude/workflows/`, never from a plugin
  (agent-pawl `docs/cli.md`, `docs/status.md` "Plugin-shipped workflows: not
  built"). A symlink would break, because plugin folders are versioned. When
  pawl supports plugin-shipped workflows, the copy goes away.

## Builder notes (settled during review)

- The application folder is created with a temporary name in step 1 and renamed
  once step 2 knows company and role.
- Large values (master, CV text, review) reach scripts as file paths in the
  application folder, never as `${}` arguments (pawl's 128 KiB per-variable
  environment limit).
- RenderCV already emits Markdown and HTML next to the PDF; `review-page` and
  `final-render` build on those.
- After step 9 (which skips the recruiter), `check-review` re-runs on the
  previous review against the new CV text, so the gaps shown stay current.
- Loop caps and human-step timeouts.
- The browser fallback in step 1 is an agentic step routed to only when the
  fetch script reports failure (a named outcome). Its postcondition checks that
  `posting.txt` exists and is non-trivially long. How it detects whether a
  browser is connected is left to the builder.

## Left to the builder

- Language for the scripts (Python is the natural fit: RenderCV is Python).
- Whether the scripts ship inside the plugin and are called by absolute path, or
  as a small Python package on `PATH`. pawl resolves `scripts/…` relative to the
  workflow file, so copied workflows need one or the other.
- How `page-check` measures the space left (e.g. lowest text position on the last
  page of the PDF).
- The HTML page's look, as long as swaps are unmissable.
- Folder naming details, and how `<company>-<role>` is slugged.
- Which RenderCV theme the example uses, as long as it passes the text-extraction
  check (no icon fonts, no multi-column layouts that extract out of order).
- Models per step (a different tier for the recruiter than the drafter is a
  sensible default).
- Test strategy: the scripts get unit tests with fixtures; the agentic steps are
  checked by running the workflow end to end on the example CV.
