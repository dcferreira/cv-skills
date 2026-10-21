import json

import pytest

from cv_skills import cli
from cv_skills.appdir import app_dir, slug

REQS = {
    "company": "Acme Analytics",
    "role": "Senior Data Engineer",
    "requirements": [{"id": "r1", "text": "Feature store", "kind": "must", "evidence": "cv"}],
}


def run(capsys, *argv):
    rc = cli.main([str(a) for a in argv])
    return rc, json.loads(capsys.readouterr().out)


@pytest.fixture
def reqs(tmp_path):
    def make(company="Acme Analytics", role="Senior Data Engineer"):
        path = tmp_path / "requirements.json"
        path.write_text(json.dumps({**REQS, "company": company, "role": role}), encoding="utf-8")
        return path

    return make


@pytest.fixture
def parent(tmp_path):
    p = tmp_path / "applications"
    p.mkdir()
    return p


def test_slug_folds_accents():
    assert slug("Zürich Café GmbH") == "zurich-cafe-gmbh"


def test_slug_collapses_punctuation_and_space():
    assert slug("Sr. ML/AI  Engineer (Remote)") == "sr-ml-ai-engineer-remote"


def test_slug_cap_has_no_trailing_dash():
    s = slug("a" * 39 + " bbbbbb")  # the 40th char would be the dash
    assert s == "a" * 39
    assert len(slug("word " * 30)) <= 40
    assert not slug("word " * 30).endswith("-")


def test_slug_non_latin_is_unknown():
    assert slug("数据工程师") == "unknown"
    assert slug("") == "unknown"


def test_app_dir_name(reqs, parent):
    r = app_dir(reqs(), parent, "2026-10-20")
    assert r == {"ok": True, "dir": str(parent / "2026-10-20-acme-analytics-senior-data-engineer")}
    assert list(parent.iterdir()) == []  # pure


def test_collisions(reqs, parent):
    base = "2026-10-20-acme-analytics-senior-data-engineer"
    (parent / base).mkdir()
    assert app_dir(reqs(), parent, "2026-10-20")["dir"] == str(parent / f"{base}-2")
    (parent / f"{base}-2").mkdir()
    assert app_dir(reqs(), parent, "2026-10-20")["dir"] == str(parent / f"{base}-3")


def test_non_latin_parts(reqs, parent):
    r = app_dir(reqs("数据", "工程师"), parent, "2026-10-20")
    assert r["dir"] == str(parent / "2026-10-20-unknown-unknown")


def test_invalid_requirements_exit_2(tmp_path, parent, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    rc, out = run(capsys, "app-dir", "--requirements", bad, "--parent", parent, "--date", "2026-10-20")
    assert rc == 2
    assert out["errors"][0]["code"] == "requirements_invalid"
    assert list(parent.iterdir()) == []


@pytest.mark.parametrize("date", ["2026-1-20", "20261020", "2026-02-30", "2026-13-01", "abcd-ef-gh", "2026-10-20\n"])
def test_bad_date_exit_2(reqs, parent, capsys, date):
    rc, out = run(capsys, "app-dir", "--requirements", reqs(), "--parent", parent, "--date", date)
    assert rc == 2
    assert out["errors"][0]["code"] == "usage"
    assert out["errors"][0]["id"] == "--date"


def test_cli_happy_path(reqs, parent, capsys):
    rc, out = run(capsys, "app-dir", "--requirements", reqs(), "--parent", parent, "--date", "2026-10-20")
    assert rc == 0
    assert out == {"ok": True, "dir": str(parent / "2026-10-20-acme-analytics-senior-data-engineer")}
