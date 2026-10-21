import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from cv_skills import cli

REPO = Path(__file__).resolve().parent.parent


def test_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0


def test_unknown_subcommand_exits_2():
    with pytest.raises(SystemExit) as exc:
        cli.main(["no-such-subcommand"])
    assert exc.value.code == 2


def test_console_script():
    result = subprocess.run(
        ["uv", "run", "cv-skills", "--help"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "cv-skills" in result.stdout


EXAMPLE = REPO / "examples" / "master.yaml"


def test_ids_cli_outputs_hash_and_ids(capsys):
    rc = cli.main(["ids", "--master", str(EXAMPLE)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["master_hash"] == "sha256:" + hashlib.sha256(EXAMPLE.read_bytes()).hexdigest()
    ids = {i["id"]: i for i in out["ids"]}
    assert ids["experience.0.highlights.1"]["kind"] == "highlight"
    assert ids["skills.0.details.2"]["text"] == "SQL"
    assert ids["honors.0.bullet"]["kind"] == "bullet"
    assert ids["experience.0"]["pin"] is True


def test_ids_cli_missing_master_exits_2(capsys, tmp_path):
    rc = cli.main(["ids", "--master", str(tmp_path / "missing.yaml")])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["errors"][0]["code"] == "master_invalid"


def test_ids_cli_rule_violation_exits_2_with_all_errors(capsys, tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(
        "cv:\n  name: Pat Example\n  sections:\n    a.b:\n      - Some text\n"
        "    experience:\n      - company: Acme\n        position: Eng\n"
        "        pin: true\n",
        encoding="utf-8",
    )
    rc = cli.main(["ids", "--master", str(p)])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert sorted(e["code"] for e in out["errors"]) == ["pin_not_yes", "section_key_has_dot"]


def test_failure_emits_error_json_and_returns_code(capsys, monkeypatch):
    def boom(args):
        raise cli.Failure(1, [{"code": "x", "id": "y", "message": "z — ü"}])

    monkeypatch.setitem(cli.HANDLERS, "ids", boom)
    rc = cli.main(["ids", "--master", "whatever.yaml"])
    assert rc == 1
    raw = capsys.readouterr().out
    assert "ü" in raw  # ensure_ascii=False
    assert json.loads(raw) == {
        "ok": False,
        "errors": [{"code": "x", "id": "y", "message": "z — ü"}],
    }



def test_unexpected_exception_is_internal_error_json(capsys, monkeypatch):
    def boom(args):
        raise ValueError("kaboom")

    monkeypatch.setitem(cli.HANDLERS, "ids", boom)
    rc = cli.main(["ids", "--master", "whatever.yaml"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    (err,) = out["errors"]
    assert err["code"] == "internal_error"
    assert err["id"] == ""
    assert err["message"].startswith("ValueError: kaboom")
    assert "cv-skills bug" in err["message"]


@pytest.mark.parametrize(
    "argv",
    [["no-such-subcommand"], [], ["build", "--master", "m.yaml"], ["ids", "--bogus"]],
    ids=["unknown_sub", "no_sub", "missing_required", "unknown_flag"],
)
def test_usage_errors_are_json(capsys, argv):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["ok"] is False
    (err,) = out["errors"]
    assert err["code"] == "usage" and err["id"] == ""
    assert err["message"]


@pytest.mark.parametrize("argv", [["--help"], ["build", "--help"]])
def test_help_still_prints_help(capsys, argv):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out


def _posting_file(tmp_path):
    f = tmp_path / "in.txt"
    f.write_text("Harbourlight Analytics is hiring a Data Engineer. " * 8, encoding="utf-8")
    return f


def test_fetch_posting_cli_file(tmp_path, capsys):
    out = tmp_path / "app" / "posting.txt"
    rc = cli.main(["fetch-posting", "--file", str(_posting_file(tmp_path)), "--out", str(out)])
    result = json.loads(capsys.readouterr().out)
    assert rc == 0 and result["ok"] is True and result["source"] == "file" and out.exists()


def test_fetch_posting_cli_exit_3_json(tmp_path, capsys):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><div id=root></div></body></html>")

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_port}/"
        out = tmp_path / "posting.txt"
        rc = cli.main(["fetch-posting", "--url", url, "--out", str(out)])
    finally:
        httpd.shutdown()
        httpd.server_close()
    result = json.loads(capsys.readouterr().out)
    assert rc == 3 and result["ok"] is False
    assert result["errors"][0]["code"] == "fetch_failed" and result["errors"][0]["id"] == url
    assert not out.exists()


@pytest.mark.parametrize("extra", [[], ["--url", "http://x.test/", "--file", "f.txt"]])
def test_fetch_posting_requires_one_source(extra, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["fetch-posting", *extra, "--out", str(tmp_path / "o.txt")])
    assert exc.value.code == 2
    err = json.loads(capsys.readouterr().out)["errors"][0]
    assert err["code"] == "usage"


def test_fetch_posting_help(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["fetch-posting", "--help"])
    assert exc.value.code == 0
    assert "--url" in capsys.readouterr().out
