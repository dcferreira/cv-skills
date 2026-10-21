"""`fetch-posting`: a posting URL or file -> plain-text `posting.txt`."""

from __future__ import annotations

import http.client
import re
import time
import unicodedata
import urllib.error
import urllib.request
from html.parser import HTMLParser
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from cv_skills.build import _check_out_not_input, _error, _write_atomic
from cv_skills.cli import Failure

MIN_CHARS = 200  # below this a page has no posting text (JavaScript-only site, login wall)
TIMEOUT_S = 20
MAX_BYTES = 5_000_000
FALLBACK_HINT = "Fall back to the browser step or paste the posting into a file."

# Content of these tags is never visible posting text.
_SKIP = {"script", "style", "noscript", "template", "svg", "title", "nav"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_BLOCK = {
    "p", "div", "section", "article", "main", "aside", "header", "footer", "blockquote", "pre", "form", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "dl", "dt", "dd", "table", "tr", "td", "th",
}  # fmt: skip


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._hidden: list | None = None  # [tag, nesting depth] of the `hidden` element being skipped
        self._in_li = False

    def _put(self, s: str) -> None:
        # Never start a line or follow a space with a space.
        if s.startswith(" ") and (not self.parts or self.parts[-1][-1:] in (" ", "\n")):
            s = s.lstrip(" ")
        if s:
            self.parts.append(s)

    def _break(self) -> None:
        # Inside a list item a block must not split the `- ` item from its text.
        if self._in_li:
            self._put(" ")
        else:
            self.parts.append("\n\n")

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self._hidden:
            if tag == self._hidden[0]:
                self._hidden[1] += 1
            return
        if tag not in _VOID and any(k == "hidden" for k, _ in attrs):
            self._hidden = [tag, 1]
            self._skip_depth += 1
            return
        if tag in _SKIP:
            self._skip_depth += 1
        elif self._skip_depth:
            return
        elif tag in _BLOCK:
            self._break()
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self._in_li = True
            self.parts.append("\n- ")

    def handle_endtag(self, tag: str) -> None:
        if self._hidden:
            if tag == self._hidden[0]:
                self._hidden[1] -= 1
                if not self._hidden[1]:
                    self._hidden = None
                    self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif self._skip_depth:
            return
        elif tag in _BLOCK:
            self._break()
        elif tag == "li":
            self._in_li = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if not data.strip():
            self._put(" ")
            return
        # Collapse inline whitespace but keep one space at a chunk edge (`foo <b>bar</b>`).
        lead = " " if data[0].isspace() else ""
        trail = " " if data[-1].isspace() else ""
        self._put(lead + " ".join(data.split()) + trail)


def normalise_text(text: str) -> str:
    """The `posting.txt` form: no BOM, `\\n` newlines, stripped lines, at most one blank line in a row."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text + "\n" if text else ""


def html_to_text(html: str) -> str:
    """Visible text of an HTML page, structure kept (paragraph breaks, `- ` list items)."""
    parser = _TextParser()
    parser.feed(html)
    parser.close()
    return normalise_text("".join(parser.parts))


_HTML_START = re.compile(r"\s*<(!doctype\s+html|html)\b", re.IGNORECASE)


def _fetch_failed(url: str, reason: str) -> Failure:
    return Failure(3, [_error("fetch_failed", url, f"Could not fetch `{url}`: {reason}. {FALLBACK_HINT}")])


def _url_invalid(url: str, flag: str) -> Failure:
    return Failure(
        2,
        [_error("url_invalid", url, f"`{flag}{url}` is not an absolute http:// or https:// URL. Pass the full posting URL.")],
    )


def check_url(url: str, flag: str = "--url ") -> None:
    """The one URL rule (fetch, review-page, the workflow's posting_url): an absolute http(s) URL with a
    host, a valid port, and no whitespace, control or invisible format characters (bidi overrides and
    zero-width marks would let a link's text lie). Else Failure url_invalid (exit 2); its message quotes
    `flag` + `url`."""
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018  (raises ValueError for a non-numeric or out-of-range port)
    except ValueError:
        raise _url_invalid(url, flag)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or re.search(r"[\s\x00-\x1f\x7f]", url)
        or any(unicodedata.category(c) == "Cf" for c in url)
    ):
        raise _url_invalid(url, flag)


def _decode(body: bytes, declared: str | None) -> str:
    charset = declared
    if charset is None:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:2048], re.IGNORECASE)
        charset = m.group(1).decode("ascii") if m else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except (LookupError, UnicodeError):
        return body.decode("utf-8", errors="replace")


class _HttpOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if urlsplit(newurl).scheme not in ("http", "https"):
            raise urllib.error.URLError(f"redirected to a non-http URL ({newurl})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_body(resp, deadline: float) -> bytes:  # noqa: ANN001
    """Read the response, at most MAX_BYTES + 1; TimeoutError once `deadline` passes between reads."""
    chunks: list[bytes] = []
    total = 0
    while total <= MAX_BYTES:
        if time.monotonic() > deadline:
            raise TimeoutError
        chunk = resp.read1(65536)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def fetch_url(url: str) -> str:
    """GET `url` and return the posting text. Raises Failure: exit 2 for a bad URL, exit 3 (browser fallback)
    when the page cannot be fetched or has no usable text."""
    check_url(url)
    request = urllib.request.Request(
        url, headers={"User-Agent": f"cv-skills/{version('cv-skills')} (+posting fetch)", "Accept": "text/html,text/plain"}
    )
    opener = urllib.request.build_opener(_HttpOnlyRedirects)
    timed_out = f"timed out after {TIMEOUT_S}s"
    deadline = time.monotonic() + TIMEOUT_S
    try:
        with opener.open(request, timeout=TIMEOUT_S) as resp:
            ctype = resp.headers.get_content_type() if resp.headers.get("Content-Type") else None
            charset = resp.headers.get_content_charset()
            declared = resp.headers.get("Content-Length")
            body = _read_body(resp, deadline)
    except urllib.error.HTTPError as e:
        raise _fetch_failed(url, f"HTTP {e.code}")
    except TimeoutError:
        raise _fetch_failed(url, timed_out)
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise _fetch_failed(url, timed_out)
        raise _fetch_failed(url, f"network error: {e.reason}")
    except http.client.HTTPException as e:
        raise _fetch_failed(url, f"invalid HTTP response ({type(e).__name__})")
    except OSError as e:
        raise _fetch_failed(url, f"network error: {e}")
    if ctype not in (None, "text/html", "text/plain", "application/xhtml+xml"):
        raise _fetch_failed(url, f"response is not HTML or text ({ctype})")
    if len(body) > MAX_BYTES:
        raise _fetch_failed(url, f"response larger than {MAX_BYTES / 1_000_000:g} MB")
    if declared and declared.isdigit() and len(body) < int(declared):
        raise _fetch_failed(url, f"response incomplete (got {len(body)} of {declared} bytes)")
    html = _decode(body, charset)
    is_html = ctype in ("text/html", "application/xhtml+xml") or (ctype is None and _HTML_START.match(html))
    text = html_to_text(html) if is_html else normalise_text(html)
    if len(text) < MIN_CHARS:
        raise _fetch_failed(
            url, f"page text is only {len(text)} characters ({MIN_CHARS} needed): the page probably needs JavaScript or a login"
        )
    return text



def read_file(path: Path) -> str:
    """Posting text from a user-supplied file: HTML is converted, anything else normalised as plain text."""
    try:
        raw = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        why = "is not UTF-8 text" if isinstance(e, UnicodeDecodeError) else (e.strerror or str(e))
        raise Failure(
            2,
            [_error("input_unreadable", str(path), f"Cannot read `--file {path}`: {why}. Pass a readable UTF-8 text or HTML file.")],
        )
    raw = raw.lstrip("﻿")
    return html_to_text(raw) if _HTML_START.match(raw) else normalise_text(raw)


def fetch_posting(url: str | None, file: Path | None, out: Path) -> dict:
    """Write `posting.txt` from a URL or a file; returns the ok JSON. Raises Failure (nothing written)."""
    out = Path(out)
    if file is not None:
        _check_out_not_input(out, {"--file": Path(file)})
        text = read_file(Path(file))
        if len(text) < MIN_CHARS:
            raise Failure(
                2,
                [
                    _error(
                        "posting_empty",
                        str(file),
                        f"`--file {file}` has only {len(text)} characters of text ({MIN_CHARS} needed). "
                        f"Paste the whole posting into the file.",
                    )
                ],
            )
    else:
        assert url is not None
        text = fetch_url(url)
    result = {"ok": True, "out": str(out), "source": "file" if file is not None else "url", "chars": len(text)}
    try:
        _write_atomic(out, text)
    except OSError as e:
        raise Failure(
            2,
            [
                _error(
                    "out_unwritable",
                    str(out),
                    f"Cannot write `--out {out}`: {e.strerror or e}. Choose a writable file path (not a directory).",
                )
            ],
        )
    return result
