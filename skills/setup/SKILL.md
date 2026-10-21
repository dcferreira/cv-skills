---
name: setup
description: Use when the user wants to start with cv-skills in a CV repo, convert an existing CV (LaTeX, PDF or RenderCV YAML) into a master.yaml, or check that RenderCV, pdftotext and pawl are installed.
---

# cv-skills:setup

Run once in the user's CV repo. Check the tools, then convert the user's existing CV into `master.yaml`,
the one curated file the `tailor` skill works from. Conversion is copying, never writing: the master
holds the user's own words.

**Running the CLI.** The plugin root is two directories above this skill's base directory (the one
containing `.claude-plugin/`). Every `cv-skills <subcommand>` below means:

```bash
uv run --project <plugin root> --frozen cv-skills <subcommand> …
```

Each prints one JSON object. Exit 0 is ok; exit 1 or 2 prints `{"ok": false, "errors": [{code, id, message}]}`
with every problem found. Read the `message`s and fix what they name.

## 1. Check the tools

Run `cv-skills check-deps`. On failure, show the user each `message` (it says how to install) and stop;
do not work around a missing tool. On success, say which versions were found.

## 2. Find the source CV

- Ask which file to convert if the repo has several candidates (`*.tex`, `*.pdf`, RenderCV `*.yaml`).
  One CV only; if the user has several versions, they pick.
- The master goes to `master.yaml` in the repo root. If it already exists, never overwrite it: ask the
  user for another name or for permission first.
- If the repo is a git or jj repo with the source CV tracked, leave that history alone. Moving or
  deleting the old CV is the user's decision, not part of setup.

## 3. Convert

Write the master as a valid RenderCV 2.8 file (`cv:` / `design:` / `locale:` / `settings:`). Rules:

- **Copy text verbatim.** No rewording, no tidying, no merged or split bullets, no new facts, no
  summary you wrote yourself. Fix only what the format forces: LaTeX escapes (`\&` → `&`), accents
  (`Z\"urich` → `Zürich`), `**bold**` where the source has `\textbf`. If a line is awkward, leave it;
  the user curates later.
- **Dates** become `YYYY-MM` (`start_date`, `end_date`; `present` for a current role). Where the source
  gives only a year, write `YYYY`.
- **Design.** `theme: classic` and `design.header.connections.show_icons: false`: icon fonts put glyphs
  into the PDF text and `final-render` rejects them. Next to it,
  `design.header.connections.display_urls_instead_of_usernames: true`: without icons, social handles
  would print as bare, unlabelled usernames; this prints them as `github.com/<name>` etc.
- **Sections** become `cv.sections` with plain keys (lowercase words and underscores, never a `.`).
  Use RenderCV's entry types: `company`/`position`/`highlights` for experience,
  `institution`/`area`/`degree` for education, `label`/`details` (comma-separated string) for skills
  lines, `name`/`summary`/`highlights` for projects, `bullet` for one-line items, plain strings for
  paragraphs.
- **Extra keys, strings only:** `pin: yes` (never `true`) and `tags: a, b` (never a list).
- **By source format.**
  - *LaTeX:* read the whole file, preamble included. Custom macros (`\newcommand`, `\cventry`,
    `\resumeSubheading`, a class such as moderncv or awesome-cv) define which argument is the employer,
    the title, the dates and the bullets. Learn them from the definitions, not from their names. Ignore
    commented-out text (`% …`); it is not part of the CV.
  - *PDF:* extract with `pdftotext` **without** `-layout` and rebuild the structure from reading order.
    Re-join words that were hyphenated at a line break.
  - *RenderCV YAML:* keep the content as it is. Only add what is missing and turn any `pin: true` or
    list-valued `tags` into the string forms above.

Leave `pin` and `tags` off during conversion except where the source already had them. Pinning is step 5.

## 4. Gates

Loop until all three exit 0. Fix the master from the error messages; never edit the checks, the
source or the tools to get a pass.

1. `cv-skills ids --master master.yaml`: the master is valid for RenderCV and for cv-skills (string
   `pin`/`tags`, no `.` in section keys). Its output lists every ID the tailor skill will use.
2. `cv-skills source-check --master master.yaml --source <source CV>`: every body string and every
   company, position, institution, area, degree, project name, title and skills label occurs in the
   source CV, and a company sits next to its position. `text_not_in_source` names the ID: restore the
   source wording or delete the item. It cannot see two source bullets merged into one or a sentence
   cut short, so keep bullets one-to-one with the source. The company-next-to-position test is a
   150-character heuristic: on a `text_not_in_source` you believe is a false reject, report it to the
   user; never reword or delete true content to pass. This check
   guards the conversion only. The user's later edits to the master are their own text; do not run
   it as a gate on them.
3. `cv-skills final-render --yaml master.yaml --out-dir <a new temporary directory>`: the master
   renders and its text extracts from the PDF in order. Delete the temporary directory afterwards;
   the master is not a CV to send. A `text_private_use` error means icons crept in: set
   `show_icons: false`.

`source-check` does not look at dates or contact details, because they change format in conversion.

## 5. Hand over

Tell the user:

- what was converted (sections, number of entries and bullets) and anything left out, and why;
- **check the dates and contact details yourself**: they are the one part no check compared against the
  source;
- the suggested pins: the current role, education, and the entry holding contact details if there is
  one. Add `pin: yes` only to the ones the user confirms. A pinned entry is in every tailored CV;
- `tags` are optional hints for the drafter (`tags: ml, infra`);
- `title_variants` (optional): other job titles they would accept for an entry, as one string,
  `title_variants: Analytics Engineer; Data Platform Engineer` next to its `position`. Only titles
  the user gives; never suggest one;
- an optional written summary: a `summary-guide.md` at the repo root (front matter `section`,
  `max_chars`, `max_sentences`, `facts`, then guidance for the writer; `examples/summary-guide.md` in
  the plugin is a worked example). Without it the master's own summary is used. After each edit of
  the guide, check it with `cv-skills guide-check --guide summary-guide.md --master master.yaml`;
- the tailor run's defaults: `page_limit=2` and `max_bullets=4` (the most bullets any one entry keeps);
- that the master is theirs to curate now: delete what they never want sent, add what the old CV left
  out. Nothing was sent or uploaded anywhere.

The README is the reference: "The master file", "Written summary", "Run options" and "CLI".
