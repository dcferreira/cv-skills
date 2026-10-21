import json

import pytest
from ruamel.yaml import YAML

from cv_skills import cli
from cv_skills.cli import Failure
from cv_skills.pagecheck import page_check


def test_one_page_fits(tailored):
    r = page_check(tailored, 2)
    assert (r["pages"], r["limit"], r["fits"]) == (1, 2, True)
    assert set(r) == {"ok", "pages", "limit", "fits", "last_page_remaining_mm", "last_page_fill_ratio"}


def test_footer_not_counted_as_body(tailored):
    # The page footer sits inside the bottom margin; counting it would leave ~0 mm on every CV.
    r = page_check(tailored, 2)
    assert r["last_page_remaining_mm"] > 30


def test_fill_ratio_between_0_and_1(tailored):
    r = page_check(tailored, 2)
    assert 0 < r["last_page_fill_ratio"] < 1
    # ratio and remaining are two views of one measurement: usable height 792 - 2*50.4 pt = 691.2 pt
    usable_mm = 691.2 / 72 * 25.4
    top_used = r["last_page_fill_ratio"] * usable_mm
    assert top_used + r["last_page_remaining_mm"] == pytest.approx(usable_mm, abs=0.2)


def test_remaining_decreases_when_bullets_are_added(make_cv):
    few = page_check(make_cv(0, "a.yaml"), 2)
    more = page_check(make_cv(3, "b.yaml"), 2)
    assert more["last_page_remaining_mm"] < few["last_page_remaining_mm"]
    assert more["last_page_fill_ratio"] > few["last_page_fill_ratio"]


def test_too_long_does_not_fit_limit_one(make_cv):
    r = page_check(make_cv(60), 1)
    assert r["pages"] >= 2 and r["fits"] is False


def test_fits_boundary_is_pages_equal_limit(make_cv):
    path = make_cv(60)
    pages = page_check(path, 99)["pages"]
    assert page_check(path, pages)["fits"] is True
    assert page_check(path, pages - 1)["fits"] is False


def test_limit_default_is_two(tailored):
    assert page_check(tailored)["limit"] == 2


@pytest.mark.parametrize("bad", [0, -1])
def test_limit_invalid(tailored, bad):
    with pytest.raises(Failure) as exc:
        page_check(tailored, bad)
    assert exc.value.code == 2 and exc.value.errors[0]["code"] == "limit_invalid"


def test_cv_without_sections(tailored, tmp_path):
    yaml = YAML()
    data = yaml.load(tailored.read_text(encoding="utf-8"))
    del data["cv"]["sections"]
    path = tmp_path / "empty.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)
    r = page_check(path, 2)
    assert r["pages"] == 1 and r["fits"] is True and r["last_page_remaining_mm"] > 100


def test_cli_ok(tailored, capsys):
    assert cli.main(["page-check", "--yaml", str(tailored), "--limit", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["limit"] == 1


def test_cli_over_limit_is_exit_0(make_cv, capsys):
    assert cli.main(["page-check", "--yaml", str(make_cv(60)), "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["fits"] is False


def test_cli_limit_not_an_integer(tailored, capsys):
    assert cli.main(["page-check", "--yaml", str(tailored), "--limit", "two"]) == 2
    err = json.loads(capsys.readouterr().out)["errors"][0]
    assert err["code"] == "limit_invalid" and "--limit" in err["message"]


def test_cli_missing_yaml(tmp_path, capsys):
    assert cli.main(["page-check", "--yaml", str(tmp_path / "x.yaml")]) == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "yaml_invalid"


def test_cli_requires_yaml(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["page-check"])
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "usage"


@pytest.mark.parametrize("margin", ["0.1in", "0.02in"])  # footer top edge above the margin edge
def test_footer_excluded_with_small_bottom_margin(tailored, tmp_path, margin):
    # A small bottom margin leaves the footer's top edge above the margin edge; it is still not body text.
    yaml = YAML()
    data = yaml.load(tailored.read_text(encoding="utf-8"))
    data["design"] = {"theme": "classic", "page": {"top_margin": margin, "bottom_margin": margin}}
    path = tmp_path / "m.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)
    r = page_check(path, 2)
    # counting the footer as body text would give ~0 mm and a ratio of ~1
    assert r["last_page_remaining_mm"] > 80 and r["last_page_fill_ratio"] < 0.8


@pytest.mark.parametrize("bad", ["1_0", " 2", "2.0", "+2", "\uff12", "", pytest.param("9" * 5000, id="5000-digits")])
def test_cli_limit_must_be_plain_digits(tailored, capsys, bad):
    assert cli.main(["page-check", "--yaml", str(tailored), "--limit", bad]) == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "limit_invalid"
