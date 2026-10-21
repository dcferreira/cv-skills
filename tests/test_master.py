import hashlib
from pathlib import Path

import pytest
from rendercv.schema.rendercv_model_builder import build_rendercv_dictionary_and_model

from cv_skills.cli import Failure
from cv_skills.master import (
    Master,
    ids_listing,
    load_master,
    split_details,
    strip_extra_keys,
)

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "master.yaml"

MINIMAL = """\
cv:
  name: Pat Example
  sections:
    experience:
      - company: Acme Widgets
        position: Engineer
        start_date: 2020-01
        end_date: present
{extra}        highlights:
          - Built a thing
          - Fixed a thing
design:
  theme: classic
"""


def write(tmp_path: Path, text: str, name: str = "master.yaml") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def minimal(tmp_path: Path, extra: str = "") -> Path:
    return write(tmp_path, MINIMAL.format(extra=extra))


def failure_codes(exc: pytest.ExceptionInfo) -> list[str]:
    return [e["code"] for e in exc.value.errors]


# --- hash -----------------------------------------------------------------


def test_hash_is_sha256_of_bytes():
    m = load_master(EXAMPLE)
    assert isinstance(m, Master)
    assert m.hash == "sha256:" + hashlib.sha256(EXAMPLE.read_bytes()).hexdigest()
    assert m.path == EXAMPLE


# --- example master -------------------------------------------------------


def test_example_master_is_valid_rendercv():
    # The master itself, extra keys and all, is a valid RenderCV file.
    build_rendercv_dictionary_and_model(EXAMPLE.read_text(encoding="utf-8"))


def test_example_covers_required_shapes():
    m = load_master(EXAMPLE)
    sections = [n.id for n in m.nodes.values() if n.kind == "section"]
    assert len(sections) >= 3
    assert len(m.children("experience", "entry")) >= 3
    pinned = [n.id for n in m.nodes.values() if n.pin]
    assert "experience.0" in pinned
    assert any(p.startswith("education.") for p in pinned)
    assert any("tags" in e for e in m.data["cv"]["sections"]["experience"])


def test_ids_cover_example():
    m = load_master(EXAMPLE)
    n = m.nodes

    assert n["experience"].kind == "section"
    assert n["experience"].parent is None
    assert n["experience"].swappable is False

    e0 = n["experience.0"]
    assert e0.kind == "entry" and e0.parent == "experience" and e0.pin is True
    assert e0.swappable is False
    assert e0.text == "Quillfeather Labs, Senior Data Engineer"
    assert n["experience.1"].pin is False

    h = n["experience.0.highlights.1"]
    assert h.kind == "highlight" and h.parent == "experience.0" and h.swappable
    assert h.text == m.data["cv"]["sections"]["experience"][0]["highlights"][1]

    s = n["experience.0.summary"]
    assert s.kind == "summary" and s.swappable
    assert s.text == m.data["cv"]["sections"]["experience"][0]["summary"]

    d = n["skills.0.details.2"]
    assert d.kind == "detail" and d.parent == "skills.0" and d.swappable
    assert d.text == "SQL"
    assert n["skills.0.details.0"].text == "Python (pandas, polars)"

    b = n["honors.0.bullet"]
    assert b.kind == "bullet" and b.parent == "honors.0" and b.swappable
    assert n["honors.0"].kind == "entry" and n["honors.0"].swappable is False

    t = n["profile.0"]
    assert t.kind == "entry" and t.swappable is True
    assert t.text == m.data["cv"]["sections"]["profile"][0]
    assert m.text_of("profile.0") == t.text

    # children/text_of
    assert m.children("experience.0", "highlight") == [
        f"experience.0.highlights.{j}"
        for j in range(len(m.data["cv"]["sections"]["experience"][0]["highlights"]))
    ]
    assert m.children("experience", "entry")[:2] == ["experience.0", "experience.1"]
    assert m.text_of("skills.0.details.2") == "SQL"
    assert m.text_of("experience.0.highlights.1") == h.text

    # insertion order = master order: section, then its entries depth-first
    ids = list(n)
    assert ids.index("experience") < ids.index("experience.0")
    assert ids.index("experience.0") < ids.index("experience.0.highlights.0")
    assert ids.index("experience.0.highlights.0") < ids.index("experience.1")


def test_text_of_rejects_non_swappable():
    m = load_master(EXAMPLE)
    with pytest.raises(KeyError):
        m.text_of("experience.0")
    with pytest.raises(KeyError):
        m.text_of("nope.0")


# --- split_details --------------------------------------------------------


def test_split_details_respects_parens():
    assert split_details("Python (pandas, numpy), Go") == ["Python (pandas, numpy)", "Go"]


def test_split_details_brackets_and_trim():
    assert split_details(" a [b, c] ,d,  e ") == ["a [b, c]", "d", "e"]
    assert split_details("Rust") == ["Rust"]


# --- loader rules ---------------------------------------------------------


def test_pin_yes_unquoted_is_string(tmp_path):
    # YAML 1.2 (RenderCV's ruamel): unquoted `yes` is the string "yes".
    m = load_master(minimal(tmp_path, "        pin: yes\n"))
    raw = m.data["cv"]["sections"]["experience"][0]["pin"]
    assert raw == "yes" and isinstance(raw, str)
    assert m.nodes["experience.0"].pin is True


def test_pin_true_rejected(tmp_path):
    with pytest.raises(Failure) as exc:
        load_master(minimal(tmp_path, "        pin: true\n"))
    assert exc.value.code == 2
    assert failure_codes(exc) == ["pin_not_yes"]
    err = exc.value.errors[0]
    assert err["id"] == "experience.0"
    assert "pin: yes" in err["message"]


def test_pin_other_string_rejected(tmp_path):
    with pytest.raises(Failure) as exc:
        load_master(minimal(tmp_path, "        pin: no\n"))
    assert failure_codes(exc) == ["pin_not_yes"]


def test_tags_list_rejected(tmp_path):
    with pytest.raises(Failure) as exc:
        load_master(minimal(tmp_path, "        tags: [ml, infra]\n"))
    assert exc.value.code == 2
    assert failure_codes(exc) == ["tags_not_string"]
    err = exc.value.errors[0]
    assert err["id"] == "experience.0"
    assert "tags: ml, infra" in err["message"]


def test_tags_string_accepted(tmp_path):
    m = load_master(minimal(tmp_path, "        tags: ml, infra\n"))
    assert m.data["cv"]["sections"]["experience"][0]["tags"] == "ml, infra"


def test_section_key_with_dot_rejected(tmp_path):
    text = MINIMAL.format(extra="").replace("    experience:", "    exp.erience:")
    with pytest.raises(Failure) as exc:
        load_master(write(tmp_path, text))
    assert exc.value.code == 2
    assert failure_codes(exc) == ["section_key_has_dot"]
    assert "exp.erience" in exc.value.errors[0]["message"]


def test_invalid_rendercv_master_exit_2(tmp_path):
    text = MINIMAL.format(extra="").replace("        position: Engineer\n", "")
    text = text.replace("  name: Pat Example\n", "  name: Pat Example\n  not_a_field: 1\n")
    with pytest.raises(Failure) as exc:
        load_master(write(tmp_path, text))
    assert exc.value.code == 2
    assert set(failure_codes(exc)) == {"master_invalid"}
    assert len(exc.value.errors) >= 1
    assert all(e["message"] for e in exc.value.errors)


def test_unparsable_yaml_exit_2(tmp_path):
    with pytest.raises(Failure) as exc:
        load_master(write(tmp_path, "cv: [unclosed\n"))
    assert exc.value.code == 2
    assert failure_codes(exc) == ["master_invalid"]


def test_missing_file_exit_2(tmp_path):
    with pytest.raises(Failure) as exc:
        load_master(tmp_path / "nope.yaml")
    assert exc.value.code == 2
    assert failure_codes(exc) == ["master_invalid"]
    assert "nope.yaml" in exc.value.errors[0]["message"]


def test_all_rule_errors_reported_at_once(tmp_path):
    text = MINIMAL.format(extra="        pin: true\n        tags: [a]\n")
    with pytest.raises(Failure) as exc:
        load_master(write(tmp_path, text))
    assert sorted(failure_codes(exc)) == ["pin_not_yes", "tags_not_string"]


# --- strip / listing ------------------------------------------------------


def test_strip_extra_keys():
    entry = {"company": "Acme", "pin": "yes", "tags": "a, b", "position": "Eng"}
    out = strip_extra_keys(entry)
    assert out == {"company": "Acme", "position": "Eng"}
    assert list(out) == ["company", "position"]
    assert entry["pin"] == "yes"  # input untouched


def test_ids_listing_shape():
    m = load_master(EXAMPLE)
    listing = ids_listing(m)
    assert listing["master_hash"] == m.hash
    by_id = {i["id"]: i for i in listing["ids"]}
    assert [i["id"] for i in listing["ids"]] == list(m.nodes)
    assert by_id["experience"] == {"id": "experience", "kind": "section"}
    assert by_id["experience.0"]["pin"] is True
    assert "pin" not in by_id["experience.1"]
    assert by_id["skills.0.details.2"] == {
        "id": "skills.0.details.2",
        "kind": "detail",
        "text": "SQL",
    }


# --- unusable masters never crash -----------------------------------------

BASE = "cv:\n  name: Ann Example\n  sections:\n{sections}\ndesign:\n  theme: classic\n"


@pytest.mark.parametrize(
    "text",
    [
        BASE.format(sections="    profile:\n      - 2024\n      - hello"),  # int text entry
        BASE.format(sections="    1:\n      - hello"),  # int section key
        "hello\n",  # top-level string
        "- a\n",  # top-level list
        "42\n",  # top-level number
    ],
    ids=["int_text_entry", "int_section_key", "top_string", "top_list", "top_number"],
)
def test_unusable_master_is_master_invalid(tmp_path, text):
    with pytest.raises(Failure) as exc:
        load_master(write(tmp_path, text))
    assert exc.value.code == 2
    assert set(failure_codes(exc)) == {"master_invalid"}
    assert all(e["message"] for e in exc.value.errors)


def test_top_level_not_mapping_message(tmp_path):
    with pytest.raises(Failure) as exc:
        load_master(write(tmp_path, "- a\n"))
    assert "mapping" in exc.value.errors[0]["message"]


def test_yaml_error_message_has_problem_and_location(tmp_path):
    with pytest.raises(Failure) as exc:
        load_master(write(tmp_path, "cv: [unclosed\n"))
    msg = exc.value.errors[0]["message"]
    assert "\n" not in msg
    assert "expected ',' or ']'" in msg
    assert "line 2, column 1" in msg


# --- markup_swallows_text -------------------------------------------------

# (example text, replacement, expected ID): `**x**;` in every kind of string RenderCV renders.
SWALLOW_CASES = [
    ("in **Go** that processes", "in **Go**; that processes", "experience.0.highlights.0"),
    ("Lead engineer for the", "Lead **engineer**; for the", "experience.0.summary"),
    ("bullet: Best Paper Award", "bullet: Best **Paper**; Award", "honors.0.bullet"),
    ("details: Python (pandas, polars), Go,", "details: Python (pandas, polars), **Go**; SQL;", "skills.0.details"),
    ("- Data engineer with eight", "- Data **engineer**; with eight", "profile.0"),
    ("position: Senior Data Engineer", "position: Senior **Data**; Engineer", "experience.0.position"),
    ("headline: Data engineer building", "headline: Data **engineer**; building", "headline"),
    ("  location: Lisbon, Portugal\n  email", "  location: '*Lisbon*(PT)'\n  email", "location"),
    ("    - name: tidewater", "    - name: '[tidewater](https://example.com).io'", "projects.0.name"),
]


def example_with(tmp_path: Path, *replacements: tuple[str, str]) -> Path:
    text = EXAMPLE.read_text(encoding="utf-8")
    for old, new in replacements:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return write(tmp_path, text)


def test_example_master_has_no_swallowed_text():
    load_master(EXAMPLE)  # does not raise


@pytest.mark.parametrize("old,new,id", SWALLOW_CASES, ids=[c[2] for c in SWALLOW_CASES])
def test_markup_swallows_text_each_kind(tmp_path, old, new, id):
    with pytest.raises(Failure) as exc:
        load_master(example_with(tmp_path, (old, new)))
    assert exc.value.code == 2
    assert failure_codes(exc) == ["markup_swallows_text"]
    assert exc.value.errors[0]["id"] == id


def test_markup_swallows_text_all_at_once_via_cli(tmp_path, capsys):
    import json

    from cv_skills.cli import main

    p = example_with(tmp_path, *[(old, new) for old, new, _ in SWALLOW_CASES])
    assert main(["ids", "--master", str(p)]) == 2
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert {e["code"] for e in errors} == {"markup_swallows_text"}
    assert sorted(e["id"] for e in errors) == sorted(c[2] for c in SWALLOW_CASES)


def test_markup_swallows_text_message(tmp_path):
    with pytest.raises(Failure) as exc:
        load_master(example_with(tmp_path, SWALLOW_CASES[0][:2]))
    msg = exc.value.errors[0]["message"]
    assert msg.startswith(
        "`experience.0.highlights.0`: Typst reads the `;` right after `**Go**` as part of the markup,"
        " so it is dropped from the PDF."
    )
    assert "`**Go;**`" in msg and "\n" not in msg


@pytest.mark.parametrize("follow,lost", [("(x)", "("), (".js", ".j")])
def test_markup_swallows_text_message_render_fails(tmp_path, follow, lost):
    with pytest.raises(Failure) as exc:
        load_master(example_with(tmp_path, ("in **Go** that", f"in **Go**{follow} that")))
    msg = exc.value.errors[0]["message"]
    assert f"Typst reads the `{lost}` right after `**Go**` as part of the markup, so the render fails." in msg


def test_markup_swallows_text_one_error_per_occurrence(tmp_path):
    with pytest.raises(Failure) as exc:
        load_master(example_with(tmp_path, ("in **Go** that", "in **Go**; *that*(x)")))
    assert [(e["id"], e["code"]) for e in exc.value.errors] == [
        ("experience.0.highlights.0", "markup_swallows_text"),
        ("experience.0.highlights.0", "markup_swallows_text"),
    ]


def test_markup_swallows_text_section_key(tmp_path):
    with pytest.raises(Failure) as exc:
        load_master(example_with(tmp_path, ("    honors:\n", "    '**Honors**; awards':\n")))
    assert [(e["code"], e["id"]) for e in exc.value.errors] == [("markup_swallows_text", "**Honors**; awards")]


def test_markup_swallows_text_ignores_pin_and_tags(tmp_path):
    load_master(example_with(tmp_path, ("tags: ml, infra", "tags: '**ml**; infra'")))


def test_markup_swallows_text_bold_keywords(tmp_path):
    swallowing = ("legacy cron jobs to Airflow with", "legacy cron jobs to Airflow; with")
    load_master(example_with(tmp_path, swallowing))  # no keywords: plain text, fine
    keywords = ("  current_date: 2026-10-20", "  current_date: 2026-10-20\n  bold_keywords:\n    - Airflow")
    load_master(example_with(tmp_path, keywords))  # `Airflow,` elsewhere is fine
    with pytest.raises(Failure) as exc:
        load_master(example_with(tmp_path, swallowing, keywords))
    assert [(e["code"], e["id"]) for e in exc.value.errors] == [
        ("markup_swallows_text", "experience.1.highlights.0")
    ]
    assert "`**Airflow**`" in exc.value.errors[0]["message"]


# Strings RenderCV never runs through Markdown: entry url/doi/dates, the photo, a Google Scholar username.
UNRENDERED_CASES = [
    (
        "        location: Lisbon, Portugal\n        pin",
        "        url: https://github.com/a/b/blob/main/__init__.py\n        location: Lisbon, Portugal\n        pin",
    ),
    (
        "  website: https://example.com/\n",
        "  website: https://example.com/\n  social_networks:\n    - network: Google Scholar\n      username: __x__.y\n",
    ),
]


@pytest.mark.parametrize("old,new", UNRENDERED_CASES, ids=["entry-url", "google-scholar-username"])
def test_markup_swallows_text_skips_unrendered_strings(tmp_path, old, new):
    load_master(example_with(tmp_path, (old, new)))  # does not raise


def test_markup_swallows_text_skips_photo(tmp_path):
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "__me__.jpg").write_bytes(b"\xff\xd8\xff")
    photo = ("  website: https://example.com/\n", "  website: https://example.com/\n  photo: photos/__me__.jpg\n")
    load_master(example_with(tmp_path, photo))  # does not raise


PUBLICATIONS = """\
    publications:
      - title: Incremental views
        authors:
          - Robin Kestrel
        date: 2017-06
        {link}
    honors:
"""


@pytest.mark.parametrize(
    "link,id",
    [
        ("url: https://github.com/a/b/blob/main/__init__.py", "publications.0.url"),
        ("doi: 10.1000/__x__.y", "publications.0.doi"),
    ],
)
def test_markup_swallows_text_publication_link_as_rendered(tmp_path, link, id):
    """A publication prints its url/doi as a `[text](url)` link, which does go through Markdown."""
    with pytest.raises(Failure) as exc:
        load_master(example_with(tmp_path, ("    honors:\n", PUBLICATIONS.format(link=link))))
    assert [(e["code"], e["id"]) for e in exc.value.errors] == [("markup_swallows_text", id)]


def test_markup_swallows_text_website_as_rendered(tmp_path):
    with pytest.raises(Failure) as exc:
        load_master(example_with(tmp_path, ("website: https://example.com/", "website: https://example.com/__x__.y")))
    assert [(e["code"], e["id"]) for e in exc.value.errors] == [("markup_swallows_text", "website")]


def test_markup_swallows_text_social_username_as_url(tmp_path):
    """With display_urls_instead_of_usernames the header prints the network URL instead of the username."""
    network = (
        "  website: https://example.com/\n",
        "  website: https://example.com/\n  social_networks:\n    - network: GitHub\n      username: x__b__.c\n",
    )
    load_master(example_with(tmp_path, network))  # `x__b__.c`: intraword `__` is plain text
    as_urls = ("      show_icons: false\n", "      show_icons: false\n      display_urls_instead_of_usernames: true\n")
    load_master(example_with(tmp_path, network, as_urls))  # `github.com/x__b__.c`: still intraword
    network = (network[0], network[1].replace("x__b__.c", "__b__.c"))
    for replacements in [(network,), (network, as_urls)]:
        with pytest.raises(Failure) as exc:
            load_master(example_with(tmp_path, *replacements))
        assert [(e["code"], e["id"]) for e in exc.value.errors] == [
            ("markup_swallows_text", "social_networks.0.username")
        ]


def test_markup_swallows_text_summary_spanning_lines(tmp_path):
    """RenderCV renders a summary as one `!!! summary` block, so its markup may span lines."""
    summary = (
        "summary: Lead engineer for the analytics platform behind a forecasting product.",
        'summary: "Uses **Go\\nlang**; x"',
    )
    with pytest.raises(Failure) as exc:
        load_master(example_with(tmp_path, summary))
    assert [(e["code"], e["id"]) for e in exc.value.errors] == [("markup_swallows_text", "experience.0.summary")]


def test_markup_swallows_text_section_title_as_rendered(tmp_path):
    """The heading is the key title-cased by RenderCV (`Python; Tools`), then bold_keywords applied."""
    key = ("    honors:\n", "    python;_tools:\n")
    keywords = ("  current_date: 2026-10-20", "  current_date: 2026-10-20\n  bold_keywords:\n    - Python")
    load_master(example_with(tmp_path, key))  # no keywords: plain text
    with pytest.raises(Failure) as exc:
        load_master(example_with(tmp_path, key, keywords))
    assert [(e["code"], e["id"]) for e in exc.value.errors] == [("markup_swallows_text", "python;_tools")]


# --- title_variants -------------------------------------------------------

POSITION = "        position: Senior Data Engineer\n"
VARIANTS_EXAMPLE = "title_variants: Senior Analytics Engineer; Senior ML Engineer"


def with_variants(tmp_path: Path, value: str) -> Path:
    return example_with(tmp_path, (POSITION, f"{POSITION}        title_variants: {value}\n"))


def test_title_variants_load_and_ids_list_the_position_node(tmp_path):
    m = load_master(with_variants(tmp_path, "Senior Analytics Engineer ;Senior ML Engineer"))
    listing = ids_listing(m)
    ids = [i["id"] for i in listing["ids"]]
    # Right after its entry, before the entry's summary and highlights.
    assert ids[ids.index("experience.0") + 1] == "experience.0.position"
    assert listing["ids"][ids.index("experience.0.position")] == {
        "id": "experience.0.position",
        "kind": "position",
        "text": "Senior Data Engineer",
        "variants": ["Senior Analytics Engineer", "Senior ML Engineer"],
    }
    assert "experience.2.position" not in ids  # only entries with variants get the node
    with pytest.raises(KeyError):
        m.text_of("experience.0.position")  # not swappable


def test_example_has_one_entry_with_title_variants():
    """The worked example: experience.1 lists title variants; no other entry gets a position node."""
    nodes = [n for n in load_master(EXAMPLE).nodes.values() if n.kind == "position"]
    assert [n.id for n in nodes] == ["experience.1.position"]
    listing = {i["id"]: i for i in ids_listing(load_master(EXAMPLE))["ids"]}
    assert listing["experience.1.position"]["variants"] == ["Analytics Engineer", "Data Platform Engineer"]


def test_title_variants_need_a_non_empty_position(tmp_path):
    """An empty default would leave the review page's CV layout without a title to mark."""
    p = example_with(tmp_path, (POSITION, "        position: ''\n        title_variants: Senior ML Engineer\n"))
    with pytest.raises(Failure) as exc:
        load_master(p)
    assert "title_variants_invalid" in failure_codes(exc)


def test_title_variant_equal_to_a_padded_position_is_rejected(tmp_path):
    p = example_with(
        tmp_path, (POSITION, "        position: 'Senior Data Engineer '\n        title_variants: Senior Data Engineer\n")
    )
    with pytest.raises(Failure) as exc:
        load_master(p)
    assert "title_variants_invalid" in failure_codes(exc)


def test_title_variant_with_a_line_break_is_rejected(tmp_path):
    with pytest.raises(Failure) as exc:
        load_master(with_variants(tmp_path, '"Senior\\nML Engineer; Senior Analytics Engineer"'))
    assert failure_codes(exc) == ["title_variants_invalid"]
    assert "line" in exc.value.errors[0]["message"]


def test_strip_extra_keys_drops_title_variants():
    out = strip_extra_keys({"company": "Acme", "position": "Eng", "title_variants": "Lead Eng"})
    assert out == {"company": "Acme", "position": "Eng"}


@pytest.mark.parametrize(
    "value,why",
    [
        ("[Senior Analytics Engineer, Senior ML Engineer]", "got a list"),
        ("Senior Analytics Engineer;; Senior ML Engineer", "empty"),
        ("Senior Analytics Engineer;", "empty"),
        ("Senior ML Engineer; Senior ML Engineer", "`Senior ML Engineer` twice"),
        ("Senior ML Engineer;  Senior Data Engineer ", "`Senior Data Engineer` is the entry's `position`"),
    ],
    ids=["not_string", "empty_item", "trailing_separator", "duplicate", "same_as_position"],
)
def test_title_variants_invalid(tmp_path, value, why):
    with pytest.raises(Failure) as exc:
        load_master(with_variants(tmp_path, value))
    assert exc.value.code == 2
    assert failure_codes(exc) == ["title_variants_invalid"]
    err = exc.value.errors[0]
    assert err["id"] == "experience.0.title_variants"
    assert why in err["message"]
    assert VARIANTS_EXAMPLE in err["message"] and "\n" not in err["message"]


def test_title_variants_on_entry_without_position(tmp_path):
    name = "      - name: tidewater\n"
    p = example_with(tmp_path, (name, f"{name}        title_variants: Lead Maintainer\n"))
    with pytest.raises(Failure) as exc:
        load_master(p)
    assert failure_codes(exc) == ["title_variants_invalid"]
    assert exc.value.errors[0]["id"] == "projects.0.title_variants"
    assert "no `position`" in exc.value.errors[0]["message"]


@pytest.mark.parametrize("value,k", [("'**Node**.js Engineer; Senior ML Engineer'", 0), ("Senior ML Engineer; **Node**.js Engineer", 1)])
def test_title_variant_markup_swallows_text(tmp_path, value, k):
    with pytest.raises(Failure) as exc:
        load_master(with_variants(tmp_path, value))
    assert failure_codes(exc) == ["markup_swallows_text"]
    assert exc.value.errors[0]["id"] == f"experience.0.title_variants.{k}"


def test_title_variant_markup_check_uses_bold_keywords(tmp_path):
    # RenderCV bolds keywords in the position slot first: `Node` becomes `**Node**`, then `.js` is swallowed.
    p = with_variants(tmp_path, "Senior ML Engineer; Node.js Engineer")
    text = p.read_text(encoding="utf-8").replace("settings:", "settings:\n  bold_keywords:\n    - Node", 1)
    p.write_text(text, encoding="utf-8")
    with pytest.raises(Failure) as exc:
        load_master(p)
    assert [e["id"] for e in exc.value.errors] == ["experience.0.title_variants.1"]
