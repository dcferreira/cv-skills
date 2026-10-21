import json

import pytest

from cv_skills import cli, finalrender
from cv_skills.cli import Failure
from cv_skills.finalrender import check_text, expected_items, final_render, normalise


def test_normalise_ligatures_and_hyphenation():
    assert normalise("efﬁcient ma-\nchine­ learning") == normalise("efficient machine learning")
    assert normalise("machine-\nlearning") == normalise("machine-learning")


def test_normalise_folds_whitespace_dashes_quotes():
    assert normalise("  a – b\n\tc ") == normalise("a - b c")
    assert normalise("it’s “x”") == normalise("it's \"x\"")


def test_normalise_strips_markdown():
    assert normalise("built in **Go** and [the docs](https://x.example) and `sql`") == normalise(
        "built in Go and the docs and sql"
    )


def test_expected_items_ids_and_order(tailored):
    items = dict(expected_items(tailored))
    ids = list(items)
    assert ids.index("experience.0.summary") < ids.index("experience.0.highlights.0") < ids.index("skills.0.details.0")
    assert items["experience.0.highlights.1"].startswith("Designed a streaming ingestion service in **Go**")
    assert items["skills.1.details.0"] == "Python (pandas and polars)"  # split outside brackets
    assert items["honors.0.bullet"].startswith("Speaker at")
    assert items["profile.0"].startswith("Comfortable owning")  # a plain text entry
    assert not any(i.startswith("cv.") for i in ids)  # header fields are out of scope


def test_check_text_ok_in_order():
    items = [("a.0", "alpha **beta**"), ("a.1", "gamma"), ("a.2", "delta")]
    assert check_text(items, "alpha beta\n• gamma\n• delta") == []


def test_check_text_missing_names_id():
    errs = check_text([("a.0", "alpha"), ("a.1", "gamma ray burst")], "alpha only")
    assert [(e["code"], e["id"]) for e in errs] == [("text_missing", "a.1")]
    assert "gamma ray burst" in errs[0]["message"]


def test_check_text_order_names_both_items():
    errs = check_text([("a.0", "alpha"), ("a.1", "gamma"), ("a.2", "delta")], "alpha delta gamma")
    assert [(e["code"], e["id"]) for e in errs] == [("text_order", "a.2")]
    assert "a.1" in errs[0]["message"]


def test_check_text_reports_all_errors():
    errs = check_text([("a.0", "one"), ("a.1", "two"), ("a.2", "three")], "three one")
    assert [(e["code"], e["id"]) for e in errs] == [("text_missing", "a.1"), ("text_order", "a.2")]
    assert all(e["code"] in ("text_missing", "text_order") for e in errs)


def test_normalise_folds_variation_selectors():
    """RenderCV prints a literal `*` as #sym.ast.basic, which pdftotext extracts as `*` + U+FE0E."""
    assert normalise("5 *︎ 3 = 15") == normalise("5 * 3 = 15")
    assert normalise("x️y") == normalise("xy")


@pytest.mark.parametrize("bullet", ["Probe Kappa* after", "Probe Kappa * after", "Probe 5 * 3 = 15", "Probe *Kappa after"])
def test_literal_asterisk_survives_final_render(tailored, tmp_path, bullet):
    from ruamel.yaml import YAML

    yaml = YAML()
    data = yaml.load(tailored.read_text(encoding="utf-8"))
    entry = next(e for e in data["cv"]["sections"]["experience"] if "highlights" in e)
    entry["highlights"] = [bullet]
    path = tmp_path / "cv.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)
    assert final_render(path, tmp_path / "out")["ok"] is True


# What the `markup_swallows_text` rule guards against, as RenderCV 2.8 + Typst actually behave.
# Pinned so an upgrade that changes it fails here loudly (and the rule's wording gets revisited).
@pytest.mark.parametrize(
    "bullet,code",
    [
        ("Probe **Go**; after", "text_missing"),
        ("Probe **Go**(x) after", "render_failed"),
        ("Probe **Go** ; after", None),
    ],
)
def test_text_after_markup_real_render(tailored, tmp_path, bullet, code):
    from ruamel.yaml import YAML

    yaml = YAML()
    data = yaml.load(tailored.read_text(encoding="utf-8"))
    entry = next(e for e in data["cv"]["sections"]["experience"] if "highlights" in e)
    entry["highlights"] = [bullet]
    path = tmp_path / "cv.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)
    if code is None:
        assert final_render(path, tmp_path / "out")["ok"] is True
        return
    with pytest.raises(Failure) as exc:
        final_render(path, tmp_path / "out")
    assert exc.value.code == 2 and [e["code"] for e in exc.value.errors] == [code]


def test_final_render_ok_example(tailored, tmp_path):
    out = tmp_path / "out"
    r = final_render(tailored, out)
    assert r["ok"] is True and r["pages"] == 1 and r["checked_items"] >= 10
    assert r["pdf"] == str(out / "cv.pdf") and r["markdown"] == str(out / "cv.md")
    assert (out / "cv.pdf").read_bytes().startswith(b"%PDF")
    assert "Quillfeather Labs" in (out / "cv.md").read_text(encoding="utf-8")


def test_nothing_written_on_text_failure(tailored, tmp_path, monkeypatch):
    monkeypatch.setattr(finalrender, "pdf_text", lambda pdf: "garbled")
    out = tmp_path / "out"
    with pytest.raises(Failure) as exc:
        final_render(tailored, out)
    assert exc.value.code == 2
    assert {e["code"] for e in exc.value.errors} == {"text_missing"}
    assert not out.exists()


def test_out_dir_unwritable(tailored, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(Failure) as exc:
        final_render(tailored, blocker / "sub")
    assert exc.value.code == 2 and exc.value.errors[0]["code"] == "out_unwritable"


def test_final_render_invalid_yaml(tmp_path):
    with pytest.raises(Failure) as exc:
        final_render(tmp_path / "nope.yaml", tmp_path / "out")
    assert exc.value.errors[0]["code"] == "yaml_invalid"


def test_existing_outputs_replaced(tailored, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "cv.pdf").write_text("stale")
    final_render(tailored, out)
    assert (out / "cv.pdf").read_bytes().startswith(b"%PDF")


def test_cli_ok(tailored, tmp_path, capsys):
    assert cli.main(["final-render", "--yaml", str(tailored), "--out-dir", str(tmp_path / "o")]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_cli_text_failure_exit_2(tailored, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(finalrender, "pdf_text", lambda pdf: "")
    assert cli.main(["final-render", "--yaml", str(tailored), "--out-dir", str(tmp_path / "o")]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["errors"][0]["code"] == "text_missing"


@pytest.fixture
def icons_on(tailored, tmp_path):
    """The tailored example with classic's default contact icons (FontAwesome glyphs in the header)."""
    from ruamel.yaml import YAML

    yaml = YAML()
    data = yaml.load(tailored.read_text(encoding="utf-8"))
    data["design"] = {"theme": "classic"}
    path = tmp_path / "icons.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)
    return path


def test_private_use_glyphs_fail_with_icons_on(icons_on, tmp_path):
    out = tmp_path / "out"
    with pytest.raises(Failure) as exc:
        final_render(icons_on, out)
    assert exc.value.code == 2
    [err] = exc.value.errors
    assert err["code"] == "text_private_use"
    assert "U+F3C5" in err["message"] and "show_icons: false" in err["message"]
    assert not out.exists()


def test_check_private_use_covers_supplementary_planes_and_reports_each_once():
    from cv_skills.finalrender import check_private_use

    assert check_private_use("clean text é –") == []
    [err] = check_private_use("a b c\U000f0000 d\U00100000")
    assert err["code"] == "text_private_use"
    assert err["message"].count("U+E000") == 1 and "U+F0000" in err["message"] and "U+100000" in err["message"]


def test_private_use_reported_with_text_errors(tailored, tmp_path, monkeypatch):
    monkeypatch.setattr(finalrender, "pdf_text", lambda pdf: " garbled")
    with pytest.raises(Failure) as exc:
        final_render(tailored, tmp_path / "out")
    assert {e["code"] for e in exc.value.errors} == {"text_missing", "text_private_use"}


def test_short_item_is_not_matched_inside_a_longer_word():
    assert [e["code"] for e in check_text([("a", "Go")], "Google")] == ["text_missing"]
    assert check_text([("a", "Go")], "Google Go") == []
    assert check_text([("a", "Go")], "(Go)") == []  # punctuation is a boundary


def test_short_item_cannot_advance_the_order_cursor():
    items = [("a", "Alpha"), ("b", "Go"), ("c", "Beta")]
    assert check_text(items, "Alpha Go Beta") == []
    assert [e["code"] for e in check_text(items, "Alpha Google Beta Go")] == ["text_order"]


def test_wrapped_hyphenated_word_still_matches_with_boundaries():
    assert check_text([("a", "machine-learning teams")], "built machine-\nlearning teams today") == []
    assert check_text([("a", "machine learning")], "machine-\nlearning") == []


def test_duplicate_item_with_one_copy_is_missing_not_misordered():
    errs = check_text([("a", "Python"), ("b", "Python")], "Python")
    assert [(e["code"], e["id"]) for e in errs] == [("text_missing", "b")]
    assert check_text([("a", "Python"), ("b", "Python")], "Python then Python") == []


def test_no_partial_write_when_second_output_cannot_be_written(tailored, tmp_path):
    out = tmp_path / "out"
    (out / "cv.md").mkdir(parents=True)
    with pytest.raises(Failure) as exc:
        final_render(tailored, out)
    assert exc.value.errors[0]["code"] == "out_unwritable"
    assert not (out / "cv.pdf").exists()
    assert sorted(p.name for p in out.iterdir()) == ["cv.md"]  # no temp files either


def test_out_dir_that_is_a_file_is_left_alone(tailored, tmp_path):
    out = tmp_path / "out"
    out.write_text("a file, so out_dir cannot be created")
    with pytest.raises(Failure) as exc:
        final_render(tailored, out)
    assert exc.value.errors[0]["code"] == "out_unwritable"
    assert out.read_text() == "a file, so out_dir cannot be created"


def test_failed_copy_removes_the_out_dir_it_created_and_its_temp_files(tailored, tmp_path, monkeypatch):
    real_copy = finalrender.shutil.copyfile
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 2:  # the first file is staged, the second fails
            raise OSError(28, "No space left on device")
        return real_copy(src, dst)

    monkeypatch.setattr(finalrender.shutil, "copyfile", flaky)
    out = tmp_path / "out"
    with pytest.raises(Failure) as exc:
        final_render(tailored, out)
    assert len(calls) == 2 and exc.value.errors[0]["code"] == "out_unwritable"
    assert not out.exists()


def test_failed_copy_keeps_a_preexisting_out_dir_and_its_files(tailored, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "cv.pdf").write_text("previous")
    monkeypatch.setattr(finalrender.shutil, "copyfile", lambda src, dst: (_ for _ in ()).throw(OSError(28, "full")))
    with pytest.raises(Failure):
        final_render(tailored, out)
    assert sorted(p.name for p in out.iterdir()) == ["cv.pdf"] and (out / "cv.pdf").read_text() == "previous"


@pytest.mark.parametrize("text", ["- []\n", "cv: 3\n", "cv:\n  name: A\n  sections:\n    s:\n      - highlights: [1]\n"])
def test_malformed_yaml_is_yaml_invalid_not_internal_error(tmp_path, text):
    path = tmp_path / "bad.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(Failure) as exc:
        final_render(path, tmp_path / "out")
    assert exc.value.code == 2 and exc.value.errors[0]["code"] == "yaml_invalid"
    assert "Master" not in exc.value.errors[0]["message"]
