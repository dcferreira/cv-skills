import http.server
import threading
import time
from pathlib import Path

import pytest

from cv_skills import fetch
from cv_skills.cli import Failure
from cv_skills.fetch import fetch_url, html_to_text, normalise_text

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_html_to_text_drops_script_style():
    text = html_to_text((FIXTURES / "posting.html").read_text(encoding="utf-8"))
    for leak in ("SCRIPT-LEAK", "TEMPLATE-LEAK", "SVG-LEAK", "color:red", "Enable JavaScript", "Data Engineer - Harbourlight"):
        assert leak not in text
    assert "Harbourlight Analytics is hiring" in text


def test_html_to_text_keeps_structure():
    assert html_to_text("<h1>Title</h1><p>a</p><ul><li>x</li><li>y</li></ul>") == "Title\n\na\n\n- x\n- y\n"
    assert html_to_text("<p>one<br>two</p>") == "one\ntwo\n"


def test_html_to_text_collapses_whitespace():
    assert html_to_text("<p>a   b\n\n  c &amp; d&nbsp;e</p>") == "a b c & d e\n"


def test_html_to_text_empty():
    assert html_to_text("<div></div>") == ""


def test_normalise_text():
    raw = "﻿line one  \r\nline two\r\n\r\n\r\n\r\nline three\t\n\n"
    assert normalise_text(raw) == "line one\nline two\n\nline three\n"


def test_js_only_fixture_has_little_text():
    assert len(html_to_text((FIXTURES / "posting_js_only.html").read_text(encoding="utf-8"))) < 200


class _Site:
    """Local test server: `routes` maps a path to (status, headers, body, delay_seconds)."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, dict, bytes, float]] = {}
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                status, headers, body, delay = site.routes[self.path]
                time.sleep(delay)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def add(self, path, body=b"", status=200, ctype="text/html; charset=utf-8", delay=0.0, **headers):
        if ctype:
            headers["Content-Type"] = ctype
        self.routes[path] = (status, headers, body, delay)
        return self.base + path


@pytest.fixture
def site():
    s = _Site()
    yield s
    s.httpd.shutdown()
    s.httpd.server_close()


def _fail(url) -> dict:
    with pytest.raises(Failure) as exc:
        fetch_url(url)
    assert len(exc.value.errors) == 1
    return {"exit": exc.value.code, **exc.value.errors[0]}


def test_fetch_url_returns_text(site):
    url = site.add("/p", (FIXTURES / "posting.html").read_bytes())
    text = fetch_url(url)
    assert "Harbourlight Analytics is hiring" in text
    assert "- 3+ years of Python & SQL" in text
    assert "<" not in text


def test_js_only_page_exits_3(site):
    url = site.add("/p", (FIXTURES / "posting_js_only.html").read_bytes())
    err = _fail(url)
    assert (err["exit"], err["code"], err["id"]) == (3, "fetch_failed", url)
    assert "JavaScript" in err["message"] and "paste the posting into a file" in err["message"]


def test_http_403_exits_3(site):
    err = _fail(site.add("/p", b"nope", status=403))
    assert (err["exit"], err["code"]) == (3, "fetch_failed")
    assert "HTTP 403" in err["message"]


def test_decodes_declared_charset(site):
    body = "<p>" + "Café résumé engineer. " * 12 + "</p>"
    assert "Café résumé" in fetch_url(site.add("/h", body.encode("latin-1"), ctype="text/html; charset=iso-8859-1"))
    meta = ('<meta charset="iso-8859-1">' + body).encode("latin-1")
    assert "Café résumé" in fetch_url(site.add("/m", meta, ctype="text/html"))


def test_non_html_content_type_exits_3(site):
    err = _fail(site.add("/p", b"%PDF-1.4", ctype="application/pdf"))
    assert err["exit"] == 3 and "application/pdf" in err["message"]


def test_too_large_exits_3(site, monkeypatch):
    monkeypatch.setattr(fetch, "MAX_BYTES", 100)
    err = _fail(site.add("/p", b"<p>" + b"x" * 500 + b"</p>"))
    assert err["exit"] == 3 and "larger than" in err["message"]


def test_timeout_exits_3(site, monkeypatch):
    monkeypatch.setattr(fetch, "TIMEOUT_S", 0.2)
    err = _fail(site.add("/p", b"<p>late</p>", delay=1.0))
    assert err["exit"] == 3 and "timed out" in err["message"]


def test_connection_refused_exits_3(site):
    base = site.base
    site.httpd.shutdown()
    site.httpd.server_close()
    err = _fail(base + "/p")
    assert err["exit"] == 3 and "network error" in err["message"]


@pytest.mark.parametrize("url", ["ftp://example.com/x", "example.com/jobs", "file:///etc/passwd", "http://"])
def test_url_invalid(url):
    with pytest.raises(Failure) as exc:
        fetch_url(url)
    assert exc.value.code == 2 and exc.value.errors[0]["code"] == "url_invalid"


def test_follows_redirect(site):
    target = site.add("/p", (FIXTURES / "posting.html").read_bytes())
    url = site.add("/r", status=302, ctype="", Location=target)
    assert "Harbourlight Analytics is hiring" in fetch_url(url)


def _posting_text() -> str:
    return "Harbourlight Analytics is hiring a Data Engineer.\n" + "You will build pipelines in Python and SQL. " * 8


def test_file_plain_text_roundtrip(tmp_path):
    src, out = tmp_path / "in.txt", tmp_path / "posting.txt"
    src.write_text("﻿" + _posting_text().replace("\n", "\r\n") + "  \n\n\n", encoding="utf-8")
    result = fetch.fetch_posting(None, src, out)
    expected = normalise_text(_posting_text())
    assert out.read_text(encoding="utf-8") == expected
    assert result == {"ok": True, "out": str(out), "source": "file", "chars": len(expected)}


def test_file_html_is_converted(tmp_path):
    out = tmp_path / "posting.txt"
    fetch.fetch_posting(None, FIXTURES / "posting.html", out)
    text = out.read_text(encoding="utf-8")
    assert "Harbourlight Analytics is hiring" in text and "SCRIPT-LEAK" not in text and "<h1>" not in text


def test_file_missing_and_not_utf8(tmp_path):
    out = tmp_path / "posting.txt"
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe" + b"x" * 300)
    for src in (tmp_path / "nope.txt", bad):
        with pytest.raises(Failure) as exc:
            fetch.fetch_posting(None, src, out)
        assert exc.value.code == 2 and exc.value.errors[0]["code"] == "input_unreadable"
        assert exc.value.errors[0]["id"] == str(src)
    assert not out.exists()


def test_file_too_short(tmp_path):
    src, out = tmp_path / "in.txt", tmp_path / "posting.txt"
    src.write_text("Data Engineer wanted.", encoding="utf-8")
    with pytest.raises(Failure) as exc:
        fetch.fetch_posting(None, src, out)
    assert exc.value.code == 2 and exc.value.errors[0]["code"] == "posting_empty"
    assert not out.exists()


def test_out_is_input(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text(_posting_text(), encoding="utf-8")
    with pytest.raises(Failure) as exc:
        fetch.fetch_posting(None, src, src)
    assert exc.value.code == 2 and exc.value.errors[0]["code"] == "out_is_input"
    assert src.read_text(encoding="utf-8") == _posting_text()


def test_out_unwritable(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text(_posting_text(), encoding="utf-8")
    with pytest.raises(Failure) as exc:
        fetch.fetch_posting(None, src, tmp_path)
    assert exc.value.code == 2 and exc.value.errors[0]["code"] == "out_unwritable"


def test_nothing_written_on_fetch_failure(site, tmp_path):
    out = tmp_path / "posting.txt"
    url = site.add("/p", (FIXTURES / "posting_js_only.html").read_bytes())
    with pytest.raises(Failure) as exc:
        fetch.fetch_posting(url, None, out)
    assert exc.value.code == 3
    assert not out.exists()


def test_url_source_and_creates_parent_dir(site, tmp_path):
    out = tmp_path / "applications" / "tmp-1" / "posting.txt"
    url = site.add("/p", (FIXTURES / "posting.html").read_bytes())
    result = fetch.fetch_posting(url, None, out)
    assert result["source"] == "url" and result["chars"] == len(out.read_text(encoding="utf-8"))


def test_html_to_text_drops_nav():
    assert html_to_text("<nav><ul><li>Home</li></ul></nav><p>Job text</p>") == "Job text\n"


# --- review fixes ---

def test_html_to_text_unclosed_head():
    body = "Plain static posting text. " * 12
    assert body.strip() in html_to_text(f"<!doctype html><html><head><title>t</title><body><p>{body}</p></body></html>")


def test_html_to_text_list_items_with_blocks():
    assert html_to_text("<ul><li><p>Python</p></li><li><p>SQL</p></li></ul>") == "- Python\n- SQL\n"
    assert html_to_text("<ul>\n<li>\n  Python\n</li>\n</ul>") == "- Python\n"
    assert html_to_text("<ul><li><p>a</p><p>b</p></li></ul>") == "- a b\n"


@pytest.mark.parametrize(
    "html, expected",
    [
        ("<table><tr><td>Location</td><td>Remote</td></tr></table>", "Location\n\nRemote\n"),
        ("<dl><dt>Location</dt><dd>Remote</dd></dl>", "Location\n\nRemote\n"),
        ("<main>A</main><aside>B</aside><blockquote>C</blockquote>", "A\n\nB\n\nC\n"),
        ("x<hr>y", "x\n\ny\n"),
    ],
)
def test_html_to_text_separates_blocks(html, expected):
    assert html_to_text(html) == expected


def test_html_to_text_drops_hidden_attribute():
    assert html_to_text("<p>Job</p><div hidden><p>HIDDEN <div>inner</div></p></div><p>End</p>") == "Job\n\nEnd\n"
    assert html_to_text("<p>a<input hidden>b</p>") == "ab\n"


def _raw_server(payload: bytes, drip: float = 0.0, close_early: bool = False):
    """One-shot raw socket server: sends `payload` (optionally a byte at a time) after reading the request."""
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        conn.recv(65536)
        try:
            if drip:
                for i in range(len(payload)):
                    conn.sendall(payload[i : i + 1])
                    time.sleep(drip)
            else:
                conn.sendall(payload)
        except OSError:
            pass
        conn.close()
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return f"http://127.0.0.1:{srv.getsockname()[1]}/"


def test_garbage_status_line_exits_3():
    err = _fail(_raw_server(b"NOT HTTP AT ALL\r\n\r\n"))
    assert err["exit"] == 3 and err["code"] == "fetch_failed"


def test_truncated_body_exits_3():
    html = b"<p>" + b"Posting text. " * 50 + b"</p>"
    payload = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 100000\r\n\r\n" + html
    err = _fail(_raw_server(payload))
    assert err["exit"] == 3 and "incomplete" in err["message"]


def test_total_time_limit(monkeypatch):
    monkeypatch.setattr(fetch, "TIMEOUT_S", 0.5)
    html = b"<p>" + b"Posting text. " * 200 + b"</p>"
    payload = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + html
    start = time.monotonic()
    err = _fail(_raw_server(payload, drip=0.05))
    assert err["exit"] == 3 and "timed out" in err["message"]
    assert time.monotonic() - start < 3


@pytest.mark.parametrize("url", ["http://example.com:abc/", "http://host/a b", "http://host/\nx"])
def test_url_invalid_port_and_whitespace(url):
    with pytest.raises(Failure) as exc:
        fetch_url(url)
    assert exc.value.code == 2 and exc.value.errors[0]["code"] == "url_invalid"


def test_exotic_charset_does_not_crash(site):
    body = ("<p>" + "Plain posting text. " * 15 + "</p>").encode()
    assert "Plain posting text." in fetch_url(site.add("/p", body, ctype="text/html; charset=idna"))


def test_missing_content_type_sniffs_html(site):
    body = b"<html><body><p>" + b"We build data pipelines. " * 12 + b"</p></body></html>"
    text = fetch_url(site.add("/p", body, ctype=""))
    assert text.startswith("We build data pipelines.") and "<" not in text


def test_non_html_text_types_rejected(site):
    err = _fail(site.add("/p", b"body{color:red}" * 40, ctype="text/css"))
    assert err["exit"] == 3 and "text/css" in err["message"]


def test_redirect_to_non_http_is_refused(site):
    err = _fail(site.add("/r", status=302, ctype="", Location="ftp://example.com/x"))
    assert err["exit"] == 3 and "ftp://example.com/x" in err["message"]


@pytest.mark.parametrize("url", ["http://jobs.example.com/acme/123", "https://jobs.example.com:8443/a?x=1&y=2#f"])
def test_check_url_accepts_http_and_https(url):
    assert fetch.check_url(url) is None


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "data:text/html,<b>x</b>", "//jobs.example.com/x", "ftp://jobs.example.com/x",
     "/acme/123", "https://jobs.example.com/a b", "https://jobs.example.com:abc/", "JaVaScRiPt:alert(1)",
     "https://jobs.example.com/‮gnp.exe", "https://jobs.example.com/a​b"],
)  # fmt: skip  (the last two: bidi override / zero-width, which would let a link's text lie)
def test_check_url_rejects(url):
    with pytest.raises(Failure) as exc:
        fetch.check_url(url)
    assert exc.value.code == 2 and exc.value.errors[0]["code"] == "url_invalid"
