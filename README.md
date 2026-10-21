# cv-skills

A Claude Code plugin that tailors one curated master CV to a job posting. The
tailored CV contains only master content: sections, entries, bullets and skills
items selected, dropped and reordered, plus term swaps toward the posting's
vocabulary that are each listed and shown to you. Nothing is invented.

It is a thin layer: [RenderCV](https://github.com/rendercv/rendercv) renders,
[agent-pawl](https://github.com/dcferreira/agent-pawl) drives the steps, and the
`cv-skills` CLI does everything that can be checked in code.

Status: usable (v0.1.0). Design: `docs/superpowers/specs/2026-10-20-cv-skills-design.md`.

## Install

1. Install two Claude Code plugins, **agent-pawl** and **cv-skills**, from their marketplaces. In
   Claude Code:
   ```
   /plugin marketplace add dcferreira/agent-pawl
   /plugin install agent-pawl@agent-pawl
   /plugin marketplace add dcferreira/cv-skills
   /plugin install cv-skills@cv-skills
   ```
2. [uv](https://docs.astral.sh/uv/). The skills run the CLI with `uv run --project <plugin dir>
   --frozen`, which installs the Python dependencies (RenderCV 2.8, pinned) on first use.
3. poppler, for `pdftotext` (e.g. `apt install poppler-utils`, `pacman -S poppler`,
   `brew install poppler`).

From a checkout (development): `uv sync`, then the CLI is `uv run cv-skills`; load the plugin with
`claude --plugin-dir <checkout>`.

## Getting started

Run the `cv-skills:setup` skill once in your CV repo. It checks the tools (`cv-skills check-deps`) and
converts your existing CV (LaTeX, PDF or RenderCV YAML) into `master.yaml`, copying your text verbatim.
`cv-skills source-check` then proves every bullet and entry title occurs in the old CV, and `ids` and
`final-render` prove the master is valid and its text extracts. Dates and contact details are not
compared with the source: check those yourself, then curate the master.

## Tailoring a CV

In your CV repo (with `master.yaml` at its root), ask Claude Code to tailor your CV to a posting,
or use the `cv-skills:tailor` skill. It copies the `tailor` workflow into `.claude/workflows/` and
runs it with pawl:

```
pawl run tailor posting=<URL or file> [posting_url=<URL>] [page_limit=2] [max_bullets=4] [master=master.yaml] [summary_guide=summary-guide.md]
```

With no arguments, `pawl run tailor` resumes the repo's paused run. The run:

1. checks the pinned entries alone (plus the summary's section, with a summary guide) fit the page limit;
2. fetches the posting into `applications/<date>-<company>-<role>/posting.txt` (if the plain fetch
   fails, e.g. on a JavaScript-only page, it goes to your connected browser, else you are asked for a
   file);
3. extracts the requirements from the posting alone;
4. drafts a selection, checked by `build` and `page-check` (up to 3 tries; then the last draft goes
   to your review);
5. has a recruiter-style review grade it, verifies its quotes, and redrafts up to twice while a
   must-have is unmet and the drafts improve;
6. writes the summary paragraph, if you have a summary guide (see "Written summary");
7. writes `review.html` and asks you to approve, stop, or describe changes (applied and
   re-checked, then asked again);
8. on approval writes `cv.pdf` and `cv.md` after the text-extraction check.

Questions wait up to 7 days, then the run pauses; `pawl run tailor` resumes it.

### Run options

| Option | Default | What it does |
|---|---|---|
| `posting` | (required) | The posting: an `http(s)` URL, or a file with its text or HTML. |
| `posting_url` | none | The posting's web address, linked on the review page (wins over a URL `posting`). |
| `page_limit` | 2 | Pages the CV must fit, checked by rendering it. |
| `max_bullets` | 4 | The most bullets any one entry keeps, checked in code (`too_many_highlights`). |
| `master` | `master.yaml` | The master CV. |
| `summary_guide` | `summary-guide.md` | The written summary's guide; no such file, no written summary. |

**How bullets are spread.** Besides the `max_bullets` cap, the drafter is told: the most recent roles
get the most bullets, and the room the cap frees goes to the most relevant bullets of the other roles.
A role that is current or ended within about the last 5 years keeps at least one; older roles may keep
none. A bullet is kept only if it supports a requirement or shows senior scope, and white space is
preferred to filler. These are instructions to the drafter, not checks.

**One page is tight.** With `page_limit=1`, the pinned entries and the summary can leave room for only
a bullet or two (one test run kept a single bullet in total).
`cv-skills pin-floor --master master.yaml --limit 1 --guide summary-guide.md` says whether they fit at
all (`fits`); if they do but drafts keep too few bullets, pin fewer entries or lower the guide's
`max_chars`.

### Requirements and the review page

The requirements are extracted from the posting alone, each marked must-have or nice-to-have and with
`evidence`: `cv` when a CV line could show it, `none` when it cannot (language fluency, soft skills,
work authorisation, willingness to relocate, travel or work a timezone overlap). Only `cv`
requirements are graded and gate the draft; the others are listed under **For you to judge**.

`review.html` (in the application folder) shows, on one self-contained page:

- **Posting:** a link to the posting, when the run has its URL;
- the CV, with every word swap and every changed job title highlighted (the original on hover), and
  a list of changes, old next to new, numbered as change requests refer to them ("undo swap 2");
- the written summary, marked **written for this posting**, each sentence with its sources; or, with a
  guide in use but the CV's own summary shown, a note saying why;
- the gate result (must-haves supported), the requirement table with the recruiter's verified quotes,
  the requirements for you to judge, top fixes, possibly missed requirements, and page count and
  space left.

### Change requests

At the review question you answer `approve`, `stop`, or the changes you want in your own words, for
example "drop the oldest role", "undo swap 2", "keep the default title for the latest role", "rewrite
the summary: lead with the platform work", "keep the original summary". Add "and review again" for a
new recruiter review. Changes are applied and checked like a draft, then you are asked again, for up
to 10 rounds; after that the run stops without a PDF. While a summary guide is in effect, a request to drop the summary keeps the section and
shows the CV's own summary instead.

### Output and privacy

Everything goes into `applications/<date>-<company>-<role>/` in your CV repo:

- `posting.txt`; `posting.browser.txt` when the posting came through the browser; `posting-url.txt`
  when the posting's address is known;
- `requirements.json`;
- `selection.json`, `tailored.yaml`, `cv.txt` (the text the recruiter step reads) and `page-check.json`;
- `review.json` / `review.verified.json`, with earlier rounds kept as `review.round<N>.json`;
- `summary.json` and `summary-preview.yaml` (the writer's check of the CV with its summary), with a
  summary guide;
- `review.html`;
- `cv.pdf` and `cv.md`, only after you approve;
- a `.cv-skills-run` marker the run uses for its folder.

The run's agents are Claude Code subagents, so your master CV and the posting are read by Claude as
part of your Claude Code session. Beyond that, the run's network access is fetching the posting plus
RenderCV's own traffic: a daily check on PyPI for a newer RenderCV, and a one-time download of a
Typst package on the first render if it is not cached (so the first render needs a network). None of
these sends CV content: the CV is never uploaded, sent or submitted anywhere, and applying is yours
to do. The posting's URL goes on the review page and in `posting-url.txt`, never into `cv.pdf` or
`cv.md`.

### Known limits

- **Agent isolation is by description only.** Each agent's step names the only files it may read,
  and its `tools:` list says what it may use, but in Claude Code `tools:` on a pawl agentic step is
  advisory: pawl passes it through and the Agent tool cannot enforce it, so an agent could read or
  run more than it is told to. What the run guarantees is checked in code instead: the CV is only
  master text plus listed swaps, the master and `requirements.json` cannot change during a run, and
  every recruiter quote is verified.
- The first render needs a network connection: RenderCV's Typst theme downloads its fontawesome
  package once, then uses the cached copy.
- A run resumes only on the workflow file it started with, so `install-workflow` refuses
  (`run_live`) to change it while a `tailor` run is live.

### Written summary (optional)

With a `summary-guide.md` in your CV repo, the summary paragraph is **written by an agent** for each
posting. Everything else in the CV stays your own text. This is the one place where the guarantee
"master text plus listed swaps, nothing else" does not hold.

The guide's front matter names the summary `section`, a `max_chars` cap (at most the master
summary's length, so the page still fits), `max_sentences` (default 3, at most 5) and `facts` you
want it able to state (e.g. "10 years of experience"). Its body holds your questions and style
guidance for the writer.

Check the guide after every edit, before a tailor run:

```bash
cv-skills guide-check --guide summary-guide.md --master master.yaml
```

It checks only the front matter (the body is never interpreted, though the whole file must be
UTF-8) and writes nothing. It checks that the front matter
is there and is valid YAML with no unknown settings; that `section` names a master section of plain
strings only; that `max_chars`, if given, is a whole number from 1 up to the length of that
section's master text (its entries joined by a space); that `max_sentences` is from 1 to 5; and that
`facts` is a list of non-empty one-line strings. Exit 0 prints the settings in effect, e.g.
`{"ok": true, "section": "profile", "max_chars": 218, "max_sentences": 2, "facts": 1}` (`facts` is
a count). Exit 2 means the guide or master is unusable: a master error (`master_invalid`, checked
first), a file-level guide problem (`guide_invalid` with the path as `id`), or every bad setting at
once (`guide_invalid` with the setting as `id`). When the guide file exists, a tailor run makes the
same check at its first step and blocks there, so checking first saves a run; with no guide file, the
run simply writes no summary, while `guide-check` reports the missing file.

What the run checks: every sentence cites selected CV entries, the chosen job title or a guide
fact. Every number in it, in digits or as a word ("four", "twelve", "dozens", "twice", "third"; a
fixed list), and every word with a capital letter anywhere ("Kafka", "eBay", "gRPC") must appear
literally in those sources, so a figure like "8 years at X", computed from dates, needs a guide
fact. A sentence's first word is let off only when it is an ordinary word: one its sources use, or
a common opener such as "Data", "Senior" or "Experienced"; "Kubernetes specialist …" still needs
a source that says Kubernetes. Only `**bold**` markup is allowed (no italics, links, code, `_` or
`#`). Length and sentence count are capped, and the CV with the summary must still fit the page
limit.

What it cannot check: whether the wording is fair ("led" vs "contributed"), lowercase words
(including a name written in lowercase) and vague quantities ("several", "a few"). Read it on the
review page, where it is marked "written for this posting" with its sources. It is never shown to the
recruiter step and never counts as evidence for a requirement. Without a guide file, nothing changes.

While a guide is in effect, a run always has a summary: no draft may drop its section to fit the page
(the pin-floor check counts it), and a change request to drop the summary gets the CV's own summary
instead. For no summary at all, remove `summary-guide.md` before the run.

## The master file

A valid RenderCV 2.8 YAML file (`cv:` / `design:` / `locale:` / `settings:`), so
`rendercv render master.yaml` still works. Two optional extra keys on entries:

- `pin: yes` keeps an entry in every tailored CV (`pin_missing` otherwise). Pinning
  is per entry: the entry (and therefore its section) must always be selected, but
  its highlights and skills items are still chosen freely, so a pinned role can
  appear without bullets (the drafter is told to keep at least one in a recent role). Pin the
  roles a reader would otherwise see as a gap.
- `tags: ml, infra` (a comma-separated string) gives the drafter hints.

Both must be strings: RenderCV 2.8 turns every entry key into a template
placeholder and crashes on booleans or lists, so `pin: true` and `tags: [a, b]`
are rejected with a message saying what to write instead.

One more optional key, also a string, lists job titles you pre-approve for an
entry: `title_variants: Senior Analytics Engineer; Senior ML Engineer` next to
`position: Senior Data Engineer`. The drafter may print whichever listed title
fits the posting best (default: `position`), never any other; the review page
highlights a changed title. Items are separated by `;` because titles can
contain commas. A title outside the list is rejected (`position_not_allowed`).

See `examples/` for a fictional worked example of every feature: `master.yaml`
(pins, tags, `title_variants` on `experience.1`), `selection.json` (a selection
with swaps) and `summary-guide.md` (a summary guide that passes `guide-check`).

## CLI

`cv-skills ids --master master.yaml` prints, as JSON, the master hash and every
path ID in the master (0-based, e.g. `experience.0.highlights.1`,
`skills.0.details.2`).

`cv-skills build --master master.yaml --selection selection.json --out tailored.yaml`
writes the tailored RenderCV YAML (selected master text plus listed swaps only)
and prints the swap positions. See `examples/selection.json`.

`cv-skills cv-text --tailored tailored.yaml --out cv.txt` writes the tailored CV as
one text line per field, bullet or skills item: the text the recruiter reviews.

`cv-skills check-review --review review.json --requirements requirements.json
--tailored tailored.yaml --out review.verified.json` checks each recruiter quote
against the tailored CV (a quote that is not there turns `met`/`partial` into
`missing`), applies the must-have gate and prints whether to `pass`, `redraft` or
hand over to the `human`. Only requirements a CV line can show (`evidence: "cv"`)
are graded and gated; the others (language fluency, soft skills, work
authorisation, willingness to relocate, travel or work a timezone overlap) are listed for you to judge.

`cv-skills review-page --master master.yaml --selection selection.json --tailored
tailored.yaml --review review.verified.json --page-check page-check.json --out
review.html [--posting-url URL] [--summary summary.json --guide summary-guide.md]` writes one
self-contained HTML page (with the posting linked under the title when `--posting-url` is given, and
the written summary with its sources when `--summary`/`--guide` are): the CV with every swap and
changed title highlighted (original on hover and in a list of changes), the requirement table
with quotes, the requirements for you to judge, top fixes, possibly missed
requirements, and page count / space left.

`cv-skills summary-check --master master.yaml --selection selection.json --guide summary-guide.md
--summary summary.json` checks a written summary against its cited sources, the selection and the
guide (the rules in "Written summary"); the run calls it after the writer.

`cv-skills page-check --yaml tailored.yaml [--limit 2]` renders the tailored CV
and prints `{pages, limit, fits, last_page_remaining_mm, last_page_fill_ratio}`:
how much room is left for one more bullet.

`cv-skills final-render --yaml tailored.yaml --out-dir out/` writes `cv.pdf` and
`cv.md`, but only after checking that every bullet's text extracts from the PDF
(`pdftotext`) in order and that no icon-font glyphs leak into the text. Pick a
theme without icon fonts: the example turns off `classic`'s contact icons
(`design.header.connections.show_icons: false`).

`cv-skills pin-floor --master master.yaml [--limit 2] [--guide summary-guide.md]` renders only the
pinned entries (without their highlights or skills items; with `--guide`, plus the summary's section)
and says whether they fit.
`cv-skills app-dir --requirements requirements.json --parent applications --date
2026-10-20` prints the next free application folder name. `cv-skills
install-workflow --dest .claude/workflows` installs the `tailor` workflow.

`cv-skills check-deps` reports RenderCV 2.8, `pdftotext` and `pawl` (on `PATH`, else the agent-pawl
plugin's bundled binary), listing every missing tool at once.

`cv-skills source-check --master master.yaml --source old-cv.tex` (or `.pdf`, `.yaml`) checks that the
master's body strings and entry headings (company, position, institution, area, degree, name, title, journal,
skills labels) occur in the CV it was converted from, and that a company sits near its position. It guards
a conversion only, not later edits, and it is a guard rather than proof (it cannot see two bullets joined
into one). Dates and contact details are not compared.

Exit codes: 0 ok, 1 rejected (fixable), 2 unusable input; errors are
`{"ok": false, "errors": [{"code", "id", "message"}]}`. The full contract (IDs,
`selection.json`, swaps, error codes) is in [`docs/contract.md`](docs/contract.md).
