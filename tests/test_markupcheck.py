import pytest

from cv_skills.markupcheck import swallowed

# The probe (RenderCV 2.8 + Typst, one bullet per render): text right after inline
# markup that Typst swallows. Pinned so a RenderCV upgrade that changes the conversion
# fails here loudly.
MARKUPS = {
    "bold": ("**t**", "**t**"),
    "boldu": ("__t__", "**t**"),
    "italic": ("*t*", "*t*"),
    "link": ("[t](https://example.com)", "[t](https://example.com)"),
    "code": ("`t`", None),
    "plain": ("t", None),
}
LOST = {";": ";", ";x": ";", "(x)": "(", ".js": ".j"}
FOLLOWS = [";", ";x", "(x)", ".js", ",", ".", ":", "!", "?", ")", ".5", "..", "[x]", " ;", ":x", "'s", "-x", " x", "s"]


@pytest.mark.parametrize("follow", FOLLOWS)
@pytest.mark.parametrize("markup", list(MARKUPS))
def test_probe_table(markup, follow):
    source, shown = MARKUPS[markup]
    got = swallowed(f"Probe before {source}{follow} after")
    if shown is not None and follow in LOST:
        assert got == [(shown, LOST[follow])]
    else:
        assert got == []


def test_nested_markup_detected_once():
    assert swallowed("Built ***x***; then") == [("***x***", ";")]


def test_markup_inside_markup_is_scanned():
    assert swallowed("**a *b*; c**") == [("*b*", ";")]
    assert swallowed("[x/__init__.py](https://e.com/__init__.py)") == [("**init**", ".p")]
    assert swallowed("[a](https://e.com/x#strong[b];y)") == []  # the URL is a string, not markup


def test_two_occurrences_two_results():
    assert swallowed("**Go**; and *Rust*(x) too") == [("**Go**", ";"), ("*Rust*", "(")]


def test_escaped_brackets_inside_markup():
    assert swallowed(r"**a \[b\] c**; d") == [("**a [b] c**", ";")]


def test_inline_code_containing_markup_like_text_is_ignored():
    assert swallowed("`#strong[a];` then") == []


def test_bold_keywords():
    assert swallowed("Python; Go", ["Python"]) == [("**Python**", ";")]
    assert swallowed("Python, Go", ["Python"]) == []
    assert swallowed("Python; Go") == []
    assert swallowed("Python; Go", []) == []
