# cv-skills CLI contract

The stable interface later work builds on. Changing anything here is a breaking change.

## Subcommands

| Command | Does |
|---|---|
| `cv-skills ids --master PATH` | Prints the master hash and every ID in the master (master order). |
| `cv-skills build --master PATH --selection PATH --out PATH [--summary PATH --guide PATH]` | Writes the tailored RenderCV YAML to `--out` and prints the result with swap positions. With `--summary`/`--guide` (both or neither), the guide's section is the checked written summary (see "Written summary"). Writes nothing on any failure. |
| `cv-skills fetch-posting (--url URL \| --file PATH) --out PATH` | Writes the posting as plain text (`posting.txt`) to `--out` and prints the result. Writes nothing on any failure. |
| `cv-skills cv-text --tailored PATH --out PATH [--exclude-section KEY]` | Writes the tailored CV as text lines (the recruiter's input) to `--out`, without section `KEY` if given; prints `{"ok": true, "out", "lines"}`. |
| `cv-skills check-review --review PATH --requirements PATH --tailored PATH --out PATH [--redrafts-done N] [--prev-supported N] [--exclude-section KEY]` | Verifies the recruiter's quotes (against the CV without section `KEY` if given), applies the gate, writes the verified review to `--out`. |
| `cv-skills review-page --master PATH --selection PATH --tailored PATH --review PATH --page-check PATH --out PATH [--posting-url URL] [--summary PATH --guide PATH]` | Writes the static HTML review page to `--out`. `--summary`/`--guide` as passed to `build`. |
| `cv-skills guide-check --guide PATH --master PATH` | Checks the summary guide's front matter against the master; prints the settings in effect. Writes nothing. |
| `cv-skills summary-check --master PATH --selection PATH --guide PATH --summary PATH` | Checks a written summary (`summary.json`) against its cited sources, the selection and the guide. Writes nothing. |
| `cv-skills page-check --yaml PATH [--limit N]` | Renders the tailored YAML and prints the page count, whether it fits `--limit` (default 2) and the space left on the last page. Writes nothing persistent. |
| `cv-skills final-render --yaml PATH --out-dir DIR` | Checks that the CV's text extracts from the PDF, then writes `cv.pdf` and `cv.md` to `--out-dir`. Writes nothing on any failure. |
| `cv-skills check-deps` | Checks RenderCV 2.8, `pdftotext` and `pawl`; prints what was found. Writes nothing. |
| `cv-skills source-check --master PATH --source PATH` | Checks that the master's text occurs in the CV it was converted from (`cv-skills:setup`). Writes nothing. |
| `cv-skills app-dir --requirements PATH --parent DIR --date YYYY-MM-DD` | Prints the first free application folder name `<parent>/<date>-<company>-<role>[-N]`. Creates nothing. |
| `cv-skills pin-floor --master PATH [--limit N] [--guide PATH]` | Renders the pinned entries alone (with `--guide`, plus the summary guide's section) and prints the page count and whether they fit `--limit` (default 2). Writes nothing persistent. |
| `cv-skills install-workflow --dest DIR` | Copies the `tailor` pawl workflow and its shims into `DIR` (a CV repo's `.claude/workflows`), with the plugin root written into the shims. |

All output is one JSON object on stdout (UTF-8, not ASCII-escaped), including usage errors and unexpected
crashes. The only exception is `--help` (on `cv-skills` or a subcommand), which prints plain help text and exits 0.

## Exit codes and errors

| Exit | Meaning |
|---|---|
| 0 | OK. |
| 1 | Rejected, fixable by the drafter (bad selection or swaps). Nothing written. |
| 2 | Unusable input (file missing or unreadable, YAML/JSON unparsable, master invalid for RenderCV or for cv-skills rules, `--out` unwritable or an input file), bad command-line usage, or an internal error. Nothing written. |
| 3 | `fetch-posting` only: the URL did not give a usable posting (`fetch_failed`). This is the one outcome the workflow routes to the browser fallback. Nothing written. |

On exit 1 or 2:

```json
{"ok": false, "errors": [{"code": "…", "id": "…", "message": "…"}]}
```

All errors found are reported at once, not just the first. `id` is the offending ID, a JSON path into
`selection.json` (`sections[0].entries[1].id`), a file path, or `""` when there is none (`usage`,
`internal_error`, and `master_invalid` errors RenderCV reports without a location). `message` is one line; it names
the ID/field and says how to fix it.

| Code | Exit | Meaning |
|---|---|---|
| `usage` | 2 | Bad command line (unknown subcommand or flag, missing argument), `app-dir` with a `--date` that is not a real `YYYY-MM-DD` date (`id` = `--date`), or `build`/`review-page` with only one of `--summary` / `--guide` (`id` = `""`); `message` is argparse's or says what to fix. |
| `master_invalid` | 2 | Master missing, unreadable, not YAML (message gives the problem and its 1-based line/column), top level not a mapping, or rejected by (or crashing) RenderCV 2.8. |
| `pin_not_yes` | 2 | An entry's `pin` is not the string `yes`. |
| `tags_not_string` | 2 | An entry's `tags` is not one comma-separated string. |
| `title_variants_invalid` | 2 | An entry's `title_variants` is not one string, is on an entry without a non-empty `position`, or has an empty item (`a;;b`, a leading or trailing `;`), an item with a line break, an item listed twice or an item equal to `position` (see "Master rules"). One error per violation; `id` = `<section>.<i>.title_variants`; `message` shows how to write it (`title_variants: Senior Analytics Engineer; Senior ML Engineer`). |
| `section_key_has_dot` | 2 | A section key contains `.` (IDs are dot paths). |
| `markup_swallows_text` | 2 | A master string, as RenderCV 2.8 prints it, has `;`, `(` or `.` + letter right after bold, italic or link markup. Typst reads it as part of the markup: a `;` is dropped from the PDF; a `(` or `.x` makes the render fail (see "Master rules"). One error per occurrence; `id` = the string's path ID (header field, section key, `<section>.<i>.highlights.<j>`, `.details`, `.position`, `.title_variants.<k>`, …). Also exit **1** from `summary-check` (and `build` / `review-page` with `--summary`) for the written summary: `id` = `sentences[i]` when that sentence alone has the occurrence, else `sentences` (markup paired across sentences in the joined paragraph). |
| `guide_invalid` | 2 | The summary guide is missing, unreadable, not UTF-8, has no `---` front matter, front matter that is not a YAML mapping (`id` = the file path), or a bad setting (`id` = `guide.section`, `guide.max_chars`, `guide.max_sentences`, `guide.facts`, `guide.facts[k]`, `guide.<unknown key>`); every bad setting at once. See "Written summary". |
| `summary_invalid` | 2 | `summary.json` missing, unreadable, not UTF-8 or not JSON (`id` = file path), or the wrong shape: not `{"sentences": [{"text", "sources"}]}`, an unknown key, `text` not a string or not one line (a line break or other control character), `sources` not an array of strings (`id` = JSON path, e.g. `sentences[0].sources`). |
| `selection_invalid` | 2 | `selection.json` missing, unreadable, not UTF-8 or not JSON (`id` = file path). |
| `out_is_input` | 2 | `--out` resolves to the same file as `--master`, `--selection`, `--file` or another input (`--summary`, `--guide`, `--tailored`, …) (`id` = `--out` as given). Checked before reading anything. |
| `out_unwritable` | 2 | `--out` cannot be written, e.g. it is a directory; or `--out-dir` cannot be created or written (`id` = `--out` / `--out-dir` as given). |
| `run_live` | 2 | `install-workflow`: the installed `tailor.yaml` would change while pawl reports a live `tailor` run for that working copy (see `install-workflow`; `id` = the `tailor.yaml` path; `message` names the run ids). Nothing written. |
| `yaml_invalid` | 2 | `page-check` / `final-render`: `--yaml` missing, unreadable, or not renderable by RenderCV (`id` = the `--yaml` path; `message` carries RenderCV's reason). |
| `limit_invalid` | 2 | `page-check` / `pin-floor`: `--limit` is not 1 to 9 plain ASCII digits forming an integer >= 1 (`id` = `--limit`). |
| `tool_missing` | 2 | `pdftotext` is not on `PATH` (`id` = the tool name; `message` says to install poppler). `check-deps` also reports `rendercv` (not 2.8.x) and `pawl` (neither on `PATH` nor in the plugin cache) this way, all missing tools at once. |
| `source_unreadable` | 2 | `source-check`: `--source` is missing, a directory, unreadable, not UTF-8 text, or a `.pdf` that `pdftotext` cannot read (`id` = the source path). |
| `text_not_in_source` | 1 | `source-check`: a master body string, or an entry's `company`, `position`, `institution`, `area`, `degree` or `name`, does not occur in the source CV, or an entry's heading fields do not occur together (`id` = the master path ID, plus `.company` etc. for a field, or the entry). Restore the source wording or delete the item. |
| `render_failed` | 2 | RenderCV crashed or produced no PDF, poppler failed on the PDF, or the design has a margin in an unsupported unit (`id` = the `--yaml` path, or `""`). |
| `text_missing` | 2 | `final-render`: a body string of the CV is not in the PDF's extracted text (`id` = its path ID in the tailored YAML, e.g. `experience.0.highlights.2`; `message` quotes its first 60 characters). |
| `text_private_use` | 2 | `final-render`: the extracted PDF text, header included, contains Unicode private-use characters (category Co: U+E000..F8FF and the supplementary planes), i.e. icon-font glyphs (`id` = `""`; `message` lists up to 8 code points and says to set `design.header.connections.show_icons: false` or pick a theme without icon fonts). |
| `text_order` | 2 | `final-render`: a body string is in the PDF text but before the previous string, so the reading order is garbled (`id` = its path ID; `message` names the previous one). |
| `internal_invalid_output` | 2 | The tailored YAML failed RenderCV validation: a cv-skills bug, not a selection problem. |
| `internal_error` | 2 | Any other unexpected exception: a cv-skills bug. `message` is `<Type>: <message> (a cv-skills bug; please report)`. |
| `selection_schema` | 1 | Wrong JSON shape: unknown or missing key, wrong type, or a string that is not valid UTF-8 text (e.g. a lone surrogate `\ud800`). |
| `hash_mismatch` | 1 | `master_hash` is not the current master's hash. |
| `unknown_id` | 1 | A listed ID does not exist in the master. |
| `wrong_parent` | 1 | An ID is listed under the wrong section/entry or at the wrong level. |
| `duplicate_id` | 1 | An ID is listed more than once. |
| `highlights_missing` | 1 | The master entry has highlights but its selection has no `highlights` key. |
| `details_missing` | 1 | The master entry has skills items but its selection has no `details` key. |
| `not_selectable` | 1 | `highlights`/`details`/`position` on an entry that has none, or a `summary`/`bullet`/`position` ID listed. |
| `position_not_allowed` | 1 | An entry's `position` in the selection is neither its master `position` nor one of its `title_variants`, exactly (case and spaces included). `id` = `sections[i].entries[j].position`; `message` lists the allowed titles. |
| `empty_section` | 1 | A section is listed with no entries. |
| `pin_missing` | 1 | A pinned entry is not selected (also when its whole section is omitted). |
| `summary_section_missing` | 1 | `tailor` workflow only (`check-draft`, `build-draft`, `build-changes` step adapters): a summary guide is in use and the selection omits its section (`id` = the section). See "Written summary". |
| `too_many_highlights` | 1 | `tailor` workflow only (`check-draft`, `build-draft`, `build-changes` step adapters, trailing argument MAX_BULLETS = the workflow input `max_bullets`, default 4): an entry lists more than MAX_BULLETS highlights. One error per entry (`id` = the entry); the message gives the count and the limit. MAX_BULLETS below 1 or not an integer is `usage`; not passed, there is no cap. |
| `carry_master_not_found` | 1 | `tailor` workflow only (`carry` step adapter): neither the earlier run's `master.snapshot.yaml` nor any committed version of the master has the hash of its selection's `master_hash` (`id` = that run's folder; `message` says how to save it). See "Carrying a selection". |
| `carry_source_invalid` | 1 | `tailor` workflow only (`fetch` with `from_selection`): the `selection.json`, `posting.txt` or `requirements.json` of the earlier run is missing (one error per file, `id` = the path). |
| `carry_posting_mismatch` | 1 | `tailor` workflow only (`fetch` with `from_selection`): the run's posting URL (as linked on the review page) differs from the earlier run's `posting-url.txt` (`id` = that file). |
| `carry_report_changed` | 1 | `tailor` workflow only (`review-page` step with CARRY_SHA): `carry.json` is missing or not the bytes the `carry` step wrote (`id` = the file). |
| `swap_target_unknown` | 1 | A swap target is not an ID in the master. |
| `swap_target_not_selected` | 1 | A swap target (or its entry) is not in the selection. For a `summary`/`bullet` target the fix is to select its entry. |
| `swap_forbidden_field` | 1 | A swap targets a non-swappable field (position, also one with `title_variants`, company, dates, section, structured entry, …). |
| `swap_from_empty` | 1 | A swap's `from` is empty or whitespace-only. |
| `swap_to_empty` | 1 | A swap's `to` is empty or whitespace-only (deselect instead). |
| `swap_to_multiline` | 1 | A swap's `to` contains a newline or other control character (Unicode category Cc, tab included); a swap must stay on one line. |
| `swap_to_comma_in_detail` | 1 | A swap on a `detail` (skills item) has a comma in `to`; a skills-item swap must stay one item. |
| `swap_from_not_found` | 1 | `from` does not occur in the target's master text. |
| `swap_from_ambiguous` | 1 | `from` occurs more than once in the target; include more surrounding words. |
| `swap_overlap` | 1 | Two swaps on one target replace overlapping text. |
| `swap_markup_swallows_text` | 1 | A swap puts `;`, `(` or `.` + letter right after bold, italic or link markup in its target. Typst reads it as part of the markup: a `;` is dropped from the PDF; a `(` or `.x` makes the render fail (`id` = `swaps[i]`; `message` names the target, the markup and the character). Change the swap so the punctuation is not right after the markup, or drop it. |
| `selection_markup_swallows_text` | 1 | A selected skills line, its kept items joined, pairs markup up anew so that `;`, `(` or `.` + letter follows it (e.g. `*x, y*z, w*(q)` without `*x`; see `build`). `id` = `sections[i].entries[j].details`; `message` names the entry, the markup and the character. Select its items differently, or fix the markup in the master. |
| `summary_source_unknown` | 1 | A summary source is not a citable ID at all: not in the master, a section ID, a `<entry>.position` of an entry without a `position`, `guide.facts.<n>` beyond the guide's facts (`id` = `sentences[i].sources[k]`). |
| `summary_source_not_selected` | 1 | A summary source exists but is not selected (it or its entry was dropped), or is in the guide's own section, which the summary replaces (`id` = `sentences[i].sources[k]`; `message` says which). |
| `summary_source_missing` | 1 | A summary sentence cites no sources (`id` = `sentences[i].sources`). |
| `summary_unsupported_token` | 1 | A number (a token with a digit, or a number word such as `twelve`), or a token with an uppercase letter (other than an ordinary first word or the pronoun `I`, `I'm`, `I've`, `I'd`, `I'll`), is not a whole token of its cited sources' text (see "Written summary" for the exact rule; `id` = `sentences[i]`; one error per token; `message` names the token and the sentence's valid sources). |
| `summary_markup_not_allowed` | 1 | A summary sentence has markup other than balanced `**bold**`: `#` followed by a letter (a Typst command, which RenderCV passes through), any `_`, `[` or `]` (link syntax), a backtick, or a `*` outside a balanced `**…**` pair (e.g. `*italics*`) (`id` = `sentences[i]`; one error per sentence, `message` lists what it has). |
| `summary_too_long` | 1 | The joined summary, Markdown stripped, is over the guide's `max_chars` (`id` = `sentences`). |
| `summary_too_many_sentences` | 1 | More sentences than the guide's `max_sentences` (`id` = `sentences`). |
| `summary_empty` | 1 | No sentences (`id` = `sentences`), or a sentence whose text is blank (`id` = `sentences[i]`). |
| `summary_written_section_not_selected` | 1 | `build` / `review-page` with `--summary`: the guide's section is not in the selection, so the summary has nowhere to go (`id` = `guide.section`). |
| `fetch_failed` | 3 | `fetch-posting --url`: network error, timeout (20 s), HTTP error status, response that is not HTML or text, response over 5 MB, or fewer than 200 characters of page text (a JavaScript-only site or login wall). `id` = the URL. The only exit 3. |
| `url_invalid` | 2 | `fetch-posting --url` or `review-page --posting-url` is not an absolute `http://` or `https://` URL with a host, or contains whitespace, a control character, an invisible format character (Unicode category Cf: bidi overrides, zero-width marks) or a non-numeric port (`id` = the value). `javascript:`, `data:`, relative and protocol-relative (`//host`) URLs are all rejected. One rule for both flags. |
| `input_unreadable` | 2 | `--file` is missing, unreadable or not UTF-8 (`id` = file path). |
| `posting_empty` | 2 | `--file` has fewer than 200 characters of text after conversion (`id` = file path). |
| `tailored_invalid` | 2 | The tailored YAML is missing, unreadable, not UTF-8/YAML, or has no top-level `cv` mapping (`id` = file path). |
| `requirements_invalid` | 2 | `requirements.json` is missing, unreadable, not JSON or violates its schema, including a missing or bad `evidence` (one error per requirement, `id` = `requirements[i].evidence`; a missing one says the file predates the field and needs a fresh run). `id` = JSON path, or file path. |
| `review_invalid` | 2 | `review.json` is missing, unreadable or not JSON (`id` = file path). |
| `review_schema` | 1 | `review.json` has the wrong shape: unknown or missing key, wrong type, bad `status`, text that is not valid UTF-8. `id` = JSON path. |
| `review_unknown_requirement` | 1 | The review lists a requirement id that is not in `requirements.json`. |
| `review_unrated_requirement` | 1 | The review lists a requirement marked `evidence: "none"`; leave it out, the user judges it (`id` = the requirement id). |
| `review_missing_requirement` | 1 | The review has no entry for an `evidence: "cv"` requirement id. |
| `review_duplicate_requirement` | 1 | The review lists a requirement id more than once. |
| `tailored_mismatch` | 1 | `review-page`: `--tailored` is not byte-identical to what `build` writes for this master and selection (and the same `--summary`/`--guide`, or none) (`id` = the file). |
| `review_stale` | 1 | `review-page`: the verified review's `tailored_sha256` is not the hash of the current `--tailored` file (`id` = the review file). Re-run `check-review`. |
| `page_check_invalid` | 2 | `review-page`: the page-check JSON is missing, unreadable, not JSON or lacks `pages`, `limit`, `fits`, `last_page_remaining_mm` or `last_page_fill_ratio` (extra keys are ignored). |

## `page-check`

On exit 0 (also when the CV is over the limit: that is a measurement, not an error; the caller acts on `fits`):

```json
{"ok": true, "pages": 2, "limit": 2, "fits": true, "last_page_remaining_mm": 41.3, "last_page_fill_ratio": 0.82}
```

- `fits` is `pages <= limit`.
- `last_page_remaining_mm`: millimetres between the lowest body text on the last page and the bottom margin edge (1 decimal).
  Text that ends below the bottom margin edge, such as the page footer, is not body text. `last_page_fill_ratio`: how much of the
  area between the margins is used, `0`..`1` (3 decimals). Margins come from the YAML's `design.page`.
- The YAML is rendered in a temporary directory; nothing is written next to it.

## `app-dir`

On exit 0:

```json
{"ok": true, "dir": "<parent>/2026-10-20-acme-analytics-senior-data-engineer"}
```

- `requirements.json` is validated like `check-review` does (`requirements_invalid`, exit 2). `--date` must be a real
  date written `YYYY-MM-DD`, else `usage` with `id` `--date` (exit 2).
- Company and role become slugs: NFKD, non-ASCII dropped, lowercase, runs of anything but `a-z0-9` become `-`, cut to
  40 characters, no leading or trailing `-`. A slug that is empty (e.g. only non-Latin text) is `unknown`.
- If `<parent>/<name>` exists, `-2`, `-3`, ... is appended to the first free name. Nothing is created, so `--parent`
  need not exist.

## `pin-floor`

On exit 0 (also when the pinned entries do not fit: the caller acts on `fits`):

```json
{"ok": true, "pages": 1, "limit": 2, "fits": true}
```

- The floor is the smallest valid selection: every pinned entry, in master order, with `highlights: []` and `details: []`
  where the entry has them, and no swaps. Pins are entry-level, so highlights and skills items do not count; an entry's
  summary or bullet stays with it.
- With `--guide` (a summary guide, checked as in `guide-check`, `guide_invalid` otherwise), the floor also has every
  entry of the guide's section, with its master text: every draft must keep that section, and the written summary
  is never longer than that text. Without `--guide`, the output is unchanged.
- The selection is built and rendered in a temporary directory; nothing is written next to the master.
- `--limit` is checked as in `page-check` (`limit_invalid`).

## `install-workflow`

`cv-skills install-workflow --dest DIR` (`DIR` = the CV repo's `.claude/workflows`). On exit 0:

```json
{"ok": true, "files": ["<dest>/tailor.yaml", "<dest>/scripts/tailor/step.sh", "<dest>/scripts/tailor/cv.sh"]}
```

- Copies the plugin's `workflow/` files to the same relative paths under `--dest`, creating folders. In the two shims
  the placeholder `@CV_SKILLS_ROOT@` becomes the plugin root, shell-quoted; shims are mode 0755.
- Each file is written atomically; a re-run writes the same bytes. A file that cannot be written is `out_unwritable`.
- pawl resumes a run only on the workflow file it started with. So when the installed `tailor.yaml` would change (the
  shims, which carry the plugin root, may always follow an update), `pawl status --json` is asked first (pawl on `PATH`,
  else the plugin cache, run from the repo root: `--dest`'s grandparent when `--dest` ends in `.claude/workflows`). A
  `tailor` run there that is `running`, `blocked`, `newer-format` or `unreadable` (by `workflow`, or a `workflow_path`
  ending in `/tailor.yaml`) is
  `run_live` and nothing is written. Without a pawl to ask, or if it fails, the install goes ahead.
- `step.sh` runs `python -m cv_skills.steps` (the workflow's step adapters: pawl's stdout grammar, not part of this
  contract); `cv.sh` runs `cv-skills`.

## Application folder

A `tailor` run works in `applications/<date>-<company>-<role>/` (`posting.txt`, `posting.browser.txt` when the posting
came through the browser, `requirements.json`, `master.snapshot.yaml` (the master's bytes when the run started, for a later carried run),
`selection.json`, `tailored.yaml`, `cv.txt`, `page-check.json`, the reviews, `summary.json` and `summary-preview.yaml`
with a summary guide, `carry.json` in a carried run, `review.html`, then `cv.pdf` and `cv.md`; and the `.cv-skills-run`
marker). When the posting's web address is known it
also holds `posting-url.txt`: that URL alone, one line ending in `\n`. It is the workflow arg `posting_url` if given
(checked by the URL rule of `fetch-posting --url`; a bad one blocks the run at its `fetch` step with `url_invalid`,
and since a run keeps its arguments the fix is a fresh run), else `posting` when that is a valid http(s) URL; with
neither there is no file. The file is the application's record only: the review page's link
(`review-page --posting-url`) is computed by the same rule from the run's own arguments, never read back from the
file, because agents can write in the application folder. The link never goes into `cv.pdf` or `cv.md`.

## `final-render`

On exit 0:

```json
{"ok": true, "pdf": "<out-dir>/cv.pdf", "markdown": "<out-dir>/cv.md", "pages": 2, "checked_items": 31}
```

- The text check covers every body string under `cv.sections` in the tailored YAML: `summary`, each `highlights` item, `bullet`,
  each skills `details` item (split like `build`) and plain-text entries. Header fields (name, headline, contact) are not
  checked. IDs in errors are paths into the tailored YAML (`<section>.<i>`, `.summary`, `.highlights.<j>`, `.bullet`,
  `.details.<k>`), which can differ from master IDs.
- Each string is compared with the output of `pdftotext` (reading order, no `-layout`) after normalisation: NFKC
  (ligatures), curly quotes and dash variants folded, variation selectors U+FE00–U+FE0F dropped (RenderCV prints a
  literal `*` as a symbol that extracts as `*` + U+FE0E), Markdown marks and links stripped, then all whitespace and hyphens
  removed (line wrapping and hyphenation). Whitespace and hyphens still count as word boundaries: a match must start and
  end on one (or next to punctuation), so `Go` is not found inside `Google`. Strings must appear in YAML order, each
  with its own copy in the text (a string listed twice needs two occurrences); any other difference fails.
- All problems are reported at once. `cv.pdf` and `cv.md` are staged next to their targets and renamed into place
  only after the check passes and both targets are writable; on failure neither file is touched, and an `--out-dir`
  created by the run is removed again.
- Separately, private-use characters anywhere in the extracted text (header included) fail with `text_private_use`.
  Use a theme without icon fonts; the example sets `design.header.connections.show_icons: false` on `classic`.

## `check-deps`

On exit 0: `{"ok": true, "tools": [{"name", "found": true, "detail"}]}` for `rendercv` (version), `pdftotext` (path)
and `pawl` (path and `pawl --version`). `pawl` is looked up on `PATH` first, then at
`~/.claude/plugins/cache/*/agent-pawl/*/bin/pawl` (the agent-pawl plugin's bundled binary; the highest plain numeric
version wins; non-executable files and directories such as `1.0.0-rc1` or a commit hash are ignored).

## `source-check`

Guards a conversion into a master (`cv-skills:setup`), not later edits of it. On exit 0:
`{"ok": true, "checked_items": N, "source": "tex|pdf|yaml|text"}`.

- The source is read by suffix: `.tex` (comments, commands, braces and escapes stripped; accents resolved), `.pdf`
  (`pdftotext`, no `-layout`), `.yaml`/`.yml` and anything else as UTF-8 text.
- Checked: every body string under `cv.sections` (as in `final-render`) and the entry fields `company`, `position`,
  `institution`, `area`, `degree`, `name`, `title`, `authors` (each author), `journal`, `number`, `reversed_number`
  and `label`. Not `title_variants`: those are the user's own later wording, not converted text (a chosen title
  is still a master string the user approved). A `company` must also occur within 150 characters of its `position`, and an `institution` near its
  `area` and `degree`, so a promoted title or a swapped employer fails (`id` = the entry). Not checked: dates,
  `location`, the header (name, headline, contact) and link targets; dates and contact details change format in
  conversion and are the user's to check.
- A string matches when, after the `final-render` normalisation (NFKC, quotes and dashes, variation selectors, Markdown marks, whitespace
  and hyphens removed, except that a hyphen between digits is kept: `2-3` is not `23`), it occurs in the source text
  starting and ending on a word boundary, in any order. A string of 3 characters or fewer (`R`, `Go`, `C++`) must
  stand alone as a token: `R` is not found in `R&D`.
- It is a guard, not proof: it cannot see two source bullets joined into one, or a sentence cut short.
- LaTeX: unescaped `%` comments, `\iffalse…\fi` and `comment` environments are not source; escapes, accents
  (`\"u`, `\'{\i}`, `\H{o}`, `\c{c}`), `\o \ss \ae \l`, `\sim \times \ldots` are resolved. `\href{url}{text}` counts
  as `text` (a Markdown link `[text](url)` in the master folds to `text` too); `\url{u}` counts as `u`, its displayed text. Options (`\href[opt]{…}`, `\hyperref[label]{…}`) are dropped; custom wrapper macros
  around `\href` (`\newcommand{\link}[2]{\href{#1}{#2}}`) are not recognised, so their URL stays in the source text.

## IDs

Dot-separated paths, **0-based** list indices, stable within one master hash. Section keys must not contain `.`.

| Kind | ID | Selectable | Swappable |
|---|---|---|---|
| `section` | `<section>` (the YAML key, e.g. `experience`) | yes | no |
| `entry` | `<section>.<i>` | yes | only a plain-string (text) entry |
| `highlight` | `<section>.<i>.highlights.<j>` | yes | yes |
| `summary` | `<section>.<i>.summary` | no, kept with its entry | yes |
| `detail` | `<section>.<i>.details.<k>` | yes | yes |
| `bullet` | `<section>.<i>.bullet` (BulletEntry text) | no, kept with its entry | yes |
| `position` | `<section>.<i>.position`, only on an entry with `title_variants` | no (choose with the entry's `"position"`) | no |

`detail` items come from a OneLine entry's `details`, split on commas outside `()`/`[]` and trimmed:
`Python (pandas, polars), Go` is two items. `build` rejoins the selected items with `", "`.

`ids` output:

```json
{"master_hash": "sha256:…",
 "ids": [{"id": "experience", "kind": "section"},
         {"id": "experience.0", "kind": "entry", "text": "Quillfeather Labs, Senior Data Engineer", "pin": true},
         {"id": "experience.0.position", "kind": "position", "text": "Senior Data Engineer",
          "variants": ["Senior Analytics Engineer", "Senior ML Engineer"]},
         {"id": "experience.0.highlights.0", "kind": "highlight", "text": "…"}]}
```

`text` is the master text (for a structured entry, a short label of its identifying fields). `pin` appears only on pinned entries.
A `position` node comes right after its entry, only when the entry has `title_variants`: `text` is the default
title, `variants` the alternatives in master order. It lists the titles the selection may choose; it is not
selectable or swappable.

## Master hash

`"sha256:" + sha256(master file bytes).hexdigest()`. Any byte change to the master (comments included) changes it,
and every selection must be redrafted against the new IDs, or carried onto them by text (see "Carrying a selection").

## `selection.json`

```json
{"master_hash": "sha256:…",
 "sections": [
   {"id": "experience", "entries": [
     {"id": "experience.0", "highlights": ["experience.0.highlights.2", "experience.0.highlights.0"],
      "position": "Senior ML Engineer"}]},
   {"id": "skills", "entries": [
     {"id": "skills.1", "details": ["skills.1.details.3", "skills.1.details.0"]}]}],
 "swaps": [{"target_id": "experience.0.highlights.2", "from": "ML teams", "to": "machine-learning teams"}]}
```

- List order is output order at every level; anything not listed is dropped.
- Unknown keys anywhere are rejected. `swaps` is optional.
- An entry whose master entry has highlights (resp. skills items) **must** carry `highlights` (resp. `details`);
  `[]` drops them all deliberately. The key is rejected on an entry that has none.
- `highlights: []` removes the key from the output entry; `details: []` writes `details: ""`.
- `position` (optional string) chooses the entry's title: its master `position` or one of its `title_variants`,
  exactly (case and spaces included), else `position_not_allowed`. Omitted means the master `position`. On an entry
  with no `position` it is `not_selectable`. A swap can never change a title (`swap_forbidden_field`).

## Swaps

- `from` is matched against the **master** text of the target: exact, case-sensitive, on the raw YAML string
  (markdown such as `**Go**` included). It must occur exactly once.
- Several non-overlapping swaps on one target apply together, all against the master text.
- A swap may change what an item says, not the CV's shape: one bullet stays one bullet, one skills item stays
  one item. So `to` must not contain a newline or other control character (`swap_to_multiline`), and a swap on a
  `detail` must not put a comma in `to` (`swap_to_comma_in_detail`; `from` may contain one). Inline Markdown in
  `to` (e.g. `**bold**`) is allowed.
- `build` returns one record per swap, in `swaps` order:
  `{"index": 0, "target_id": "…", "from": "…", "to": "…", "start": 12, "end": 34}`.
  `start`/`end` are **character** (not byte) offsets of `to` in the target's tailored text, so
  `tailored[start:end] == to`. For a `detail` the text is the item, not the whole rejoined line.

## `build` output

On exit 0:

```json
{"ok": true, "out": "<--out as given>", "master_hash": "sha256:…", "swaps": [ … ],
 "titles": [{"entry": "experience.0", "default": "Senior Data Engineer", "chosen": "Senior ML Engineer"}]}
```

`titles` has one record per selected entry whose chosen `position` differs from its master `position`, in selection
order; it is always present (`[]` when none).
The YAML at `--out` is valid RenderCV 2.8 (validated before writing): `cv` non-section fields copied verbatim,
`cv.sections` in selection order, `pin`/`tags`/`title_variants` stripped, then `design`, `locale`, `settings` as in the master.
Every string in `cv.sections` is the master string at a selected ID, or that string with exactly its listed swaps;
an entry's `position` is its master `position` or the variant the selection chose.
A master without `cv.sections` (with `"sections": []`) yields an output without `cv.sections`.
After applying the swaps, `build` checks each swapped string for text RenderCV would lose after inline markup
(the `markup_swallows_text` rule in "Master rules", `settings.bold_keywords` included). An occurrence the
target's master text does not have was created by a swap: `swap_markup_swallows_text` (exit 1, nothing written),
`id` = `swaps[i]` of each swap that creates it on its own, and of every swap on that target for an occurrence
only their combination creates. Once no swap does, `build` checks each selected skills line (its kept items
joined with `, `): dropping items can pair markup up anew (`*x, y*z, w*(q)` without `*x`), which is
`selection_markup_swallows_text` (exit 1, nothing written, `id` = `sections[i].entries[j].details`).
The result JSON is serialised before the file is written, and the file is written atomically (temp file + rename)
with the usual `0o666 & ~umask` mode.

With `--summary J --guide G` (both or neither, else `usage`; `out_is_input` covers both files), `build` loads the
guide (`guide_invalid`) and `summary.json` (`summary_invalid`), and, once the selection, swap and markup checks
pass, rejects with exit 1 the errors of `summary-check` and `summary_written_section_not_selected`. Then the guide's
section in the tailored YAML is **one** plain-text entry: the sentences' texts, each trimmed, joined by `" "`
(Markdown kept). The result gains `"summary": {"section": "<key>", "sentences": [{"text", "sources"}]}` (the
sentences as given); without the flags there is no `summary` key and nothing changes.

## Agent outputs

`requirements.json` (step 2, extractor) and `review.json` (step 5, recruiter) are typed agent outputs; unknown keys
are rejected.

```json
{"company": "Acme Analytics", "role": "Senior Data Engineer",
 "requirements": [{"id": "r1", "text": "Feature store experience", "kind": "must", "evidence": "cv"},
                  {"id": "r2", "text": "Fluent English", "kind": "must", "evidence": "none"}]}
```

```json
{"requirements": [{"id": "r1", "status": "met", "evidence_quote": "feature store used by four ML teams", "reason": "…"}],
 "top_fixes": ["…"], "missed_requirements": ["…"]}
```

- `kind` is `must` or `nice`; `status` is `met`, `partial` or `missing`. Requirement ids are unique, non-empty
  strings. `top_fixes` and `missed_requirements` are required (use `[]`) and are never gating.
- `evidence` is required, `cv` or `none`, independent of `kind`. `none` is what a CV line can't show with a quote:
  language fluency, generic soft skills, work authorisation or visa, willingness to relocate, travel or work a
  timezone overlap (where the candidate is based is `cv`: the header's location line shows it). A missing or other
  value is `requirements_invalid`; a `requirements.json` written before this field needs a fresh run.
- The review has exactly one entry per `evidence: "cv"` requirement id and none for an `evidence: "none"` one
  (`review_unrated_requirement`); with no `cv` requirements, `"requirements": []`.

## `cv-text` and `check-review`

`cv-text` writes one line per CV string of the tailored YAML (name and header fields, section headings, entry
fields, summaries, bullets, text entries, and each skills item on its own line; section headings as RenderCV prints them; a
string containing a newline is one line) and ends the file with a newline. Inline Markdown in a line (`**x**`,
`__x__`, `*x*`, `` `x` ``, `[t](u)`) is reduced to its text; nothing else changes (no Unicode, dash, quote or
whitespace normalisation), so the text can be copied character for character.
The recruiter is given exactly this text.

`--exclude-section KEY` (both `cv-text` and `check-review`, optional) leaves section `KEY` out: its heading and
all its lines. `check-review` then verifies quotes against the remaining lines only, so a quote found only in that
section is not verified (`met`/`partial` becomes `missing`, `downgraded: true`); the same quote also found in
another line (e.g. a highlight) still verifies. A `KEY` the CV does not have changes nothing. The workflow passes
the summary guide's `section` whenever a guide is in use, so the written (or placeholder) summary is never recruiter
evidence. `tailored_sha256` is still the hash of the whole file.

`check-review` re-reads the tailored YAML itself (not the `cv-text` file), and accepts a quote copied from
`cv-text`: both sides are normalised (markup included) before matching, so a quote copied from the `cv-text` line
matches its YAML line. A quote is verified iff, after
normalisation, it contains a letter or digit and is a substring of **one** normalised CV line (or, to accept a
quote copied with its markup, of the same line normalised without the markup step) (a quote spliced from two lines does
not verify; a string with a newline is one CV line, newlines become spaces). Normalisation: Unicode NFKC; variation selectors U+FE00–U+FE0F dropped; `**x**`, `__x__`, `*x*`, `` `x` `` and `[t](u)` reduced to their text;
dash variants to `-`; curly quotes to straight; whitespace runs to one space. Matching is case-sensitive. A
`met`/`partial` requirement without a verified quote becomes `missing` with `downgraded: true`
(`claimed_status` keeps the recruiter's word). The gate passes iff every `evidence: "cv"` `must` is `met` or
`partial` after verification (no such musts: passes); `evidence: "none"` requirements are outside the gate and the
counts.

`--redrafts-done` defaults to 0; omit `--prev-supported` on the first round (it is required when `--redrafts-done` is above 0, else `usage`). On exit 0 (also when the gate fails):

```json
{"ok": true, "out": "<--out as given>", "gate": "pass|fail", "outcome": "pass|redraft|human",
 "supported_must": 2, "total_must": 3, "unmet_must": ["r3"], "downgraded": ["r1"],
 "for_you_to_judge": [{"id": "r4", "kind": "must", "text": "Fluent English"}]}
```

`outcome` is `pass` when the gate passes; else `redraft` iff `--redrafts-done` < 2 and (`--prev-supported` is
omitted or `supported_must` > `--prev-supported`); else `human`. `--out` receives the verified review: as
`requirements`, the `evidence: "cv"` requirements in `requirements.json` order, each with `id, kind, text, status,
claimed_status, downgraded, evidence_quote, reason`; as `for_you_to_judge`, the `evidence: "none"` ones as
`{id, kind, text}` in `requirements.json` order (`[]` if none); plus `company`, `role`, `top_fixes`,
`missed_requirements`, the gate fields above and `tailored_sha256` (hex SHA-256 of the tailored YAML file bytes the
quotes were checked against). `supported_must`, `total_must` and `unmet_must` count `cv` musts only.

## `review-page`

Takes the master and selection (swaps, their positions and the chosen titles are recomputed exactly as `build` does, and every
`build` rejection applies), the tailored YAML (must equal what `build` writes, else `tailored_mismatch`), the
**verified** review (`check-review --out`; `review_invalid` unless every field has its expected type, `tailored_sha256` and `for_you_to_judge` (a list of `{id, kind: must|nice, text}`) included; `review_stale` if that hash is not the
`--tailored` file's) and the `page-check` JSON. On exit 0:
`{"ok": true, "out": "<--out as given>", "swaps": <count>}`. Nothing is written on failure.

The page is one self-contained HTML file (no scripts, no external resources; light and dark). It shows, in
order: the gate result and pages / space left; the CV laid out from the tailored structure, each swap a
`<mark class="swap">` numbered `[N]` with `title="was: <original>"`, and each chosen title that is not the default
a `<mark class="swap title">` (the same highlight) numbered `[T<n>]` with `title="default: <master position>"`; a
Changes table (`#`, target ID, original, now): the swaps as numbered, then one row per chosen title (`T<n>`,
`<entry>.position (title)`, the default, the chosen title); the heading counts both; the requirement table (status, verbatim quote, reason, and a note when the recruiter's quote was not found);
"For you to judge" (each `for_you_to_judge` item as its kind, `must` in bold, and its text, then a note that a CV
line can't show these and they are not in the gate; "None." when empty); top fixes; possibly missed requirements.
All text is HTML-escaped.

With `--summary J --guide G` (both or neither, as passed to `build`): every `build` rejection for them applies
(`summary-check`'s errors, `summary_written_section_not_selected`), and `tailored_mismatch` is against `build`'s
output with the same summary, so the page and the shipped CV cannot diverge (a tailored YAML built with a summary
is a mismatch without the flags, and the other way round). The ok JSON gains `"summary_sentences": <count>`. On the
page, the guide's section shows the written paragraph with the badge `written for this posting`
(`<span class="badge">`); each sentence is `<span class="written" title="sources: <id>, <id>">…</span>` followed by
`<sup>[S<n>]</sup>` (1-based). The Changes table gets one more row, `<tr class="summary">` (`S`, `<section>
(summary)`, the master text of the section (entries joined by a space), the written paragraph), counted in its
heading; after it comes a "Summary sources" section, `<ol id="summary-sources">`, one item per sentence (`S<n>`, its
text, then each source ID with its tailored text: the item with swaps, the entry's own fields with the chosen title,
the chosen title, or the fact). Sentence and source texts get the page's inline-markup pass (`**x**` is bold),
after HTML escaping, like the CV's other text.

In the `tailor` workflow, when a summary guide is in use but the page shows the master's own summary, the
`review-page` step (`step.sh review-page A MASTER [POSTING POSTING_URL GUIDE APPLIED SECTION STATUS]`) puts
`<p class="summary-note">The CV's own summary (no written summary: <reason>)</p>` at the guide's section. The reason
comes from the workflow state `summary_status` (default `not_reached`), which only deterministic steps set, so it
is never an agent's claim:

| `summary_status` | Set by | Reason on the page |
|---|---|---|
| `none` | `summary_route` (no guide) | no note (no guide section) |
| `section_dropped` | `summary_route` (guide in use, its section not selected) | the summary's section is not in this selection |
| `original` | `summary_route` (the user chose the CV's own summary) | you chose the CV's original summary |
| `pending` | `summary_route` (keep or write) | (never shown: `build_summary` or a status below follows) the summary step did not finish |
| `applied` | `build_summary` ok | no note: the written summary is shown |
| `writer_failed` | `summary_writer_failed` (`write_summary` failed) | the writer could not produce a summary that passes the checks |
| `build_failed` | `summary_build_failed` (`build_summary` failed) | building the CV with the written summary failed (see the build_summary step's output) |
| `cap_reached` | `summary_cap_reached` (`summary_route`, `write_summary` or `build_summary` at its visit cap) | the summary steps reached their round limit |
| `not_reached` | default (`draft` at its visit cap goes straight to the page) | the run did not reach the summary step (the draft step reached its round limit) |

`applied` or `none` with a guide's section and no written summary (inconsistent state) also says "the summary step
did not finish". An unknown STATUS is `usage` (exit 1). `step.sh summary-status REASON` prints
`{"summary_status": "<REASON>"}` for REASON `writer_failed`, `build_failed` or `cap_reached`; any other REASON is
`usage` (exit 1).

The `review-page` step's two further optional arguments, `REVIEWED CARRY_SHA`, come from workflow state that only
deterministic steps set:

- REVIEWED is `reviewed_cv_sha256`, which `check-review` (the step) emits: the SHA-256 (hex) of the recruiter's
  text for the tailored YAML it verified (exactly `cv.txt`, the summary's section left out), computed from the YAML,
  never read from `cv.txt`. When it is not the hash of the current tailored YAML's text, the review shown graded an
  earlier version: the banner gets `<p class="stale">The recruiter graded an earlier version of this CV …</p>` and
  the question's summary line starts `Recruiter gate (from a review of an earlier version of this CV):`. The
  `build-changes` step takes the same hash as its last argument and routes to `rerun` (a new recruiter review)
  whenever the changed CV's text differs from it (`""`, no review yet, always differs), so this shows only when a
  step reached its cap with a CV the review did not grade (the recruiter's attempts or visits, or the draft step's).
- CARRY_SHA is the `carry` step's `carry_sha256`. When given, `carry.json` must have that hash
  (`carry_report_changed` otherwise: the run blocks) and the page shows `<section class="carry">` after the banner: the earlier
  folder, the count kept as reviewed, the items changed in the master (old and new text), those not carried (items,
  swaps, titles, each with why), and the items new in the master with their IDs, as "add <ID>" requests use them.

With `--posting-url URL` (optional), right under the page's `<h1>` comes
`<p class="posting">Posting: <a href="URL" target="_blank" rel="noopener noreferrer">URL</a></p>`, the URL
HTML-escaped (quotes included) in both places. A link loads nothing, so the page stays self-contained. A bad URL is
`url_invalid` (exit 2) and nothing is written; without the flag there is no such line. The link is on the review
page only, never in `cv.pdf` or `cv.md`.

## Carrying a selection (`tailor` `from_selection`)

A run is pinned to its master (`pinned`); `from_selection=<folder>/selection.json` starts a fresh run from an
earlier run's selection instead of a draft. The step adapters, in order:

- `fetch … MASTER FROM_SELECTION` copies the master's bytes to `A/master.snapshot.yaml` (every run). With
  FROM_SELECTION it fetches nothing: it copies the earlier folder's `posting.txt` and `requirements.json` (checked as
  in `app-dir`), so the gate uses the requirements that selection was reviewed against, and routes `carried` (to
  `name-folder`, skipping the extractor). A posting URL other than the earlier `posting-url.txt` is
  `carry_posting_mismatch`.
- `name-folder A FROM_SELECTION` routes `carry` (FROM_SELECTION given) or `draft`.
- `carry A MASTER FROM_SELECTION LIMIT GUIDE SECTION MAX_BULLETS [CHANGES]` finds the earlier master: the earlier
  folder's `master.snapshot.yaml`, else a committed version of MASTER (`git log --all` of that file), whichever has
  the hash of the selection's `master_hash`; else `carry_master_not_found`. The selection must be valid for that
  master (every `build` structure and swap check, `hash_mismatch` included). Then it writes `A/selection.json` for
  the current master, `A/summary.json` (with a guide: the earlier one, sources remapped) and `A/carry.json`.

Matching, per parent (sections by key; entries within the matched section by their `ids` text; highlights and
skills items within the matched entry by text):

1. Equal text: the k-th copy of a text to the k-th copy in the new list, wherever it moved.
2. Edited in place: the old and new lists are aligned (Python `difflib.SequenceMatcher`, no autojunk); in each
   `replace` block, the old items still unmatched pair in order with the new items still unmatched, only if there
   are as many of each. They are carried and reported in `changed` (`old_id`, `id`, `was`, `now`).
3. Anything else selected is left out and reported in `dropped` (`old_id`, `was`, `why`).

`<entry>.summary`, `.bullet` and `.position` follow their entry, when the new master has them (a `.position` needs a
title); an entry `summary` whose text changed is reported in `changed` too. A dropped entry's selected highlights and
skills items are listed in `dropped` after it. Order is the earlier selection's. An entry's
`highlights`/`details` key follows the new master entry (present iff it has them); a section left with no entries is
omitted. A chosen `position` no longer among the entry's titles goes back to the default (`titles_reset`). A swap is
kept, retargeted, only if its target was carried and its `from` occurs exactly once in the new master text; else it
is in `swaps_dropped` (`index`, 1-based as on the review page, `target_id`, `from`, `to`, `why`). The summary is
carried only if every source maps to a carried item or entry (`guide.facts.<n>` as is: the guide is not compared
with the earlier run's), and only if the earlier `tailored.yaml` showed it (its guide section is exactly the
paragraph). If the earlier CV showed the master's own summary instead (the user chose it, or the writer failed),
nothing is carried and the step also sets `summary_choice=original`, so the new CV shows the master's too. `summary`
says `carried` (and which cited items changed), `not carried: …` (a summary is then written again, unless it was the
master's), or `none` (no guide or no earlier `summary.json`). `new` lists the new master's sections,
entries, highlights and skills items that no old item maps to (an item inside a new entry or section is not listed
separately), as `{"id", "text"}`. `carry.json`: `{"from", "old_master_hash", "master_hash", "kept", "changed",
"dropped", "swaps_dropped", "titles_reset", "new", "summary"}`, `kept` = selected entries and items carried unchanged.

The carried selection then gets the draft's checks (`check-draft`'s, and `build` + `page-check`). All pass and no
CHANGES: `ok` (to `build-draft`, then the recruiter). Otherwise `fix`, with `change_request` = the errors as one
instruction to make the smallest fix, then `Then, as the user asked: <CHANGES>` (or CHANGES alone): to
`apply_changes`. Both set `after_changes=true` (its review never redrafts), `carry_sha256` and `carry_note` (one line
for the review question). Whatever the matching does, the CV is built by `build` from the current master: master text
plus listed swaps only.

## Written summary (`guide-check`, `summary-check`)

An optional guide file in the CV repo (the workflow's `summary_guide`, default `summary-guide.md`) lets an agent
write the summary paragraph, and only that paragraph, for each posting. No guide: nothing below applies.

Guide format: YAML front matter between a first line `---` and the next `---` line, then free text (questions to
answer, style) for the writer only; no code reads the body.

```markdown
---
section: profile          # required: the master section the paragraph replaces
max_chars: 200            # optional int >= 1; default and maximum: the section's master length
max_sentences: 3          # optional int 1..5; default 3
facts:                    # optional; citable as guide.facts.<n> (0-based)
  - 8 years of experience
---
Why this team? Lead with the strongest match. Keep it plain.
```

- `section` must be a section of the master whose entries are all plain strings (at least one).
- `max_chars` must not exceed the section's master length: its entries joined by `" "`, as raw text. The page fit
  was measured with that text.
- `facts` is a list of non-empty one-line strings (quote a number: `- "10"`). Unknown keys are rejected.
- Any problem is `guide_invalid` (exit 2), all at once. `guide-check` on exit 0 prints
  `{"ok": true, "section", "max_chars", "max_sentences", "facts": <count>}` (the values in effect).
- While a guide is in use, every `tailor` draft keeps its section: the workflow's `floor` step counts the section in
  the pin floor (as `pin-floor --guide`), and the draft and change postcondition
  `step.sh check-draft A MASTER LIMIT [REQUEST] [SECTION] [MAX_BULLETS]` (the draft passes REQUEST `none`; SECTION is the guide's
  section, `""` for no guide) rejects a selection without it with `summary_section_missing` (exit 1, so the agent puts
  it back), as do the `build-draft` and `build-changes` adapters. A change request to drop the summary is reported as
  `original` (the CV's own summary); for no summary at all, remove the guide file before the run.

`summary.json` (agent output; unknown keys rejected, `summary_invalid` otherwise):

```json
{"sentences": [{"text": "Data engineer at Quillfeather Labs who built a **feature store** for four teams.",
                "sources": ["experience.0", "experience.0.highlights.2", "guide.facts.0"]}]}
```

`text` is one line; inline `**bold**` is the only markup allowed. `summary-check` loads the master, guide, summary and
selection (their exit-2 errors first), applies the selection's structure and swap checks (exit 1, as `build`), then
reports every problem below at once (exit 1). On exit 0:
`{"ok": true, "section", "sentences": <count>, "sources": <count>, "chars": <length>, "max_chars"}`.

- **Sources.** A sentence must cite at least one (`summary_source_missing`). Citable, with the text it stands for:
  - a selected highlight, skills item (`detail`), plain-text entry, or the `summary`/`bullet` of a selected entry: its
    tailored text (swaps applied);
  - a selected structured entry `<section>.<i>`: its own fields as tailored (all of them except `highlights`,
    `details`, `summary`, `bullet` and the cv-skills keys), with `position` the **chosen** title, so `company`,
    `position`, dates, `location`, `institution`, `degree`, `label`, …;
  - `<section>.<i>.position` of a selected entry that has a `position`: the chosen title alone;
  - `guide.facts.<n>`: that fact.
  Nothing in the guide's own section is citable (`summary_source_not_selected`); neither is anything unselected
  (`summary_source_not_selected`). A string that is none of these (unknown ID, a section ID, a fact index beyond the
  list) is `summary_source_unknown`.
- **Tokens.** The sentence's text and its sources' texts are normalised (Markdown marks and links reduced to their
  text, NFKC, dashes and curly quotes folded, as in `check-review`) and split into whole tokens: runs of letters,
  digits and `_`, kept together across a single `.`, `,` or `'` between two of them (`1.5`, `1,200`, `Node.js`,
  `Robin's`); dashes, `/`, `%`, `+` and spaces split (`Zürich-based` is `Zürich` and `based`). Let the sources'
  tokens be the union of the sentence's valid cited sources' tokens. A token that breaks a rule below is
  `summary_unsupported_token`:
  - A **number word** (case-insensitive: `zero` … `twenty`, `thirty`, `forty`, `fifty`, `sixty`, `seventy`, `eighty`,
    `ninety`, `hundred`, `thousand`, `million`, `billion`, `dozen`, `dozens`, `half`, `double`, `triple`, `twice`,
    `once`, `thrice`, `first` … `tenth`) must be a sources' token, compared case-insensitively (`Four` is supported
    by `four`). This list is closed: other quantities (`several`, `many`) are not checked; `twenty-five` splits into
    `twenty` and `five`, each checked.
  - Any other token that contains a digit, or that contains an uppercase letter anywhere (`Kafka`, `eBay`, `iOS`,
    `gRPC`), must be a sources' token, case-sensitive (a token ending in `'s` also matches its stem).
  - Except the sentence's **first** token, when it is an ordinary word: no uppercase letter after its first
    character, and its lowercase form is either a sources' token compared case-insensitively (`Built` when a source
    says `built`) or one of these common openers: `a`, `an`, `the`, `i`, `my`, `with`, `over`, `across`, `from`,
    `for`, `in`, `on`, `at`, `as`, `by`, `data`, `senior`, `lead`, `staff`, `principal`, `engineer`, `engineering`,
    `builder`, `developer`, `specialist`, `experienced`, `hands` (as in `Hands-on`). Any other first token is held to
    the rule above, so an invented name cannot open a sentence (`Kubernetes specialist …` fails unless a source
    says `Kubernetes`).
  So `4` is not supported by `40k` or `2024`, `Go` not by `Google`, and `twelve` not by `four`.
- **Bounds.** The paragraph (texts trimmed, joined by `" "`, Markdown stripped) must be at most `max_chars`
  characters (`summary_too_long`); at most `max_sentences` sentences (`summary_too_many_sentences`); at least one
  sentence, none blank (`summary_empty`).
- **Markup.** Only balanced `**bold**` is allowed: RenderCV passes other markup through to Typst, so `#` + letter, any
  `_`, `[`, `]`, backtick, or a `*` left after removing the `**…**` pairs is `summary_markup_not_allowed`. Then the
  `markup_swallows_text` rule of "Master rules" (`settings.bold_keywords` included) on each sentence and on the
  joined paragraph, exit 1.

**What the check proves, and what it does not.** It proves that every number (in digits, or one of the number
words above) and every word with an uppercase letter in the summary appears literally in CV text the sentence cites
(as tailored for this posting) or in a fact the user wrote in the guide; a sentence's first word escapes this only
as an ordinary word (in its sources in any case, or a listed opener), and the first-person pronoun (`I`, `I'm`,
`I've`, `I'd`, `I'll`, straight or curly apostrophe) escapes it anywhere, being grammar, not a name. It does **not** prove that the sentence is
faithful to those sources: verbs and claims ("led" vs "contributed", "expert", "end to end"), lowercase words
(including a lowercase name such as `kubernetes`, and quantities outside the number-word list such as `several` or
`a few`), how facts are combined, and what a sentence implies are not checked. Figures derived from the CV are not supported unless they
appear literally: "8 years" computed from an entry's dates fails unless a cited source or a guide fact contains
`8` (put such figures in `facts`). The `sentences` are counted as given; one item may hold more than one
grammatical sentence. The review page marks the paragraph as written, with its sources, so the human checks the
rest.

## Master rules

- A valid RenderCV 2.8 file (`rendercv render master.yaml` works).
- Optional entry keys, all strings (RenderCV 2.8 crashes rendering non-string entry keys):
  - `pin: yes` (exactly; `pin: true` is rejected). Pinning is **per entry**: the entry, and so its section,
    must be in every selection; its highlights and skills items are still chosen freely.
  - `tags: ml, infra`: one comma-separated string of hints for the drafter.
  - `title_variants: Senior Analytics Engineer; Senior ML Engineer`: pre-approved alternatives to the entry's
    `position`, one of which the selection may choose (see `selection.json`). One string (not a YAML list, for the
    reason above), split on `;` (not `,`, which titles such as `Engineer, Platform` contain), items trimmed. Allowed
    only on an entry with a non-empty `position`; items must be non-empty, one line, pairwise distinct and differ
    from `position` (compared without surrounding spaces; the default is always allowed and never listed); else
    `title_variants_invalid`. Stripped from the tailored CV.
    Each variant is also checked for `markup_swallows_text` as RenderCV would print it in the `position` slot
    (`id` = `<section>.<i>.title_variants.<k>`, 0-based).
- No text that RenderCV 2.8 loses right after inline markup (`markup_swallows_text`). RenderCV turns bold
  (`**x**`, `__x__`), italic (`*x*`) and links (`[t](u)`) into Typst `#strong[…]`, `#emph[…]` and
  `#link("u")[…]`, and Typst reads a directly following `;`, `(` or `.` + letter (`**Node**.js`) as part of
  that expression: a `;` is dropped from the PDF; a `(` or `.x` makes the render fail. Markup nested inside
  other markup (`**a *b*; c**`, a link's text) counts too. Inline code (`` `x` ``) is immune, and anything else
  after the markup (`,` `.` `:` ` ;` `.5` `..` `[x]`, …) is fine. Checked on every string as RenderCV 2.8
  prints it: the header's `name`, `headline`, `location`, `email`, `phone`, `website` (as printed, without
  `https://`), `social_networks.<i>.username` (the profile URL instead with
  `design.header.connections.display_urls_instead_of_usernames: true`; a Google Scholar username is never
  printed) and `custom_connections.<i>.placeholder`, but not `photo`, URLs or network names; section headings
  (the key as RenderCV title-cases it, e.g. `python;_tools` -> `Python; Tools`; `id` = the key); and every entry
  string except `pin`, `tags`, `url`, `doi`, `start_date` and `end_date`, which RenderCV does not run through
  Markdown (`title_variants` is checked per variant instead, see above), a `summary` as the one block RenderCV prints (so markup may span its lines), plus a publication's
  link as printed (`[doi](https://doi.org/doi)`, else `[url without https://](url)`; `id` `.doi` / `.url`).
  `settings.bold_keywords` is bolded first (as RenderCV does) for `name`, `headline`, headings and entries.
  One error per occurrence; `id` is the section key, `<section>.<i>` for a plain-text entry,
  `<section>.<i>.highlights.<j>`, `.summary`, `.bullet`, `.details` (the whole skills line) or
  `<section>.<i>.<field>` (`position`, `company`, `name`, …). Fix: move the character inside the markup
  (`**Go;**`) or drop the markup.
  Not mirrored: custom `design.templates` that place `SUMMARY` or `URL` differently from RenderCV's defaults,
  and the normalised forms of `phone`/`email` (the raw strings are checked). `final-render` still catches a `;`
  lost in a body string, and a failed render shows up in `page-check`.

## `fetch-posting`

`cv-skills fetch-posting (--url URL | --file PATH) --out PATH`: exactly one of `--url` / `--file`. On exit 0:

```json
{"ok": true, "out": "<--out as given>", "source": "url", "chars": 1234}
```

`source` is `"url"` or `"file"`; `chars` is the length of the text written. `--out`'s parent directory is created.

`posting.txt` is UTF-8 plain text: `\n` newlines, no BOM, no trailing whitespace on a line, at most one blank line in a
row, one final newline. Headings and paragraphs are separated by a blank line; list items start with `- `. Content of
`script`, `style`, `noscript`, `template`, `svg`, `title` and `nav` elements, and of elements with the `hidden`
attribute, is dropped. A `--file` that starts with `<!doctype html` or `<html` is converted
like a fetched page; any other file is only normalised as above.

A fetched page must give at least 200 characters of text. The request has a 20 s timeout (per network operation, and checked
between reads for the whole response) and a 5 MB cap, follows redirects to `http(s)` URLs only, and sends no cookies
or credentials. A response shorter than its `Content-Length`, or with a malformed HTTP status line, is a `fetch_failed`.
A response with no `Content-Type` is treated as HTML if it starts with `<!doctype html` or `<html`, else as plain text;
only `text/html`, `text/plain` and `application/xhtml+xml` are accepted otherwise. `--file` is never an exit 3: when the user supplies the text there is
no fallback to route to.
