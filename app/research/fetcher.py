"""Fetch source pages safely and extract visible text. Page content is DATA: it is only string-matched
by the verifier and is never inserted into an LLM prompt (prompt-injection resistant by design)."""
from __future__ import annotations

import hashlib
import json
import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .. import http
from ..config import Settings, ROOT
from ..log import get_logger

logger = get_logger("fetch")
PAGE_FIXTURES = ROOT / "tests" / "fixtures" / "pages"


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in ("p", "br", "li", "tr", "h1", "h2", "h3", "h4", "div", "td", "th"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    text = unescape(" ".join(p.parts))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return p.title.strip(), text.strip()


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "gov", "ac", "org", "net", "edu") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def fetch_page(s: Settings, url: str) -> dict:
    """Returns {url, final_url, domain, title, text, sha256, status}. status: ok | error:<reason>"""
    if s.mock:
        idx = json.loads((PAGE_FIXTURES / "index.json").read_text(encoding="utf-8"))
        name = idx.get(url)
        if not name:
            return {"url": url, "final_url": url, "domain": domain_of(url), "title": "", "text": "",
                    "sha256": "", "status": "error:not in fixtures"}
        text = (PAGE_FIXTURES / name).read_text(encoding="utf-8")
        return {"url": url, "final_url": url, "domain": domain_of(url), "title": name, "text": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(), "status": "ok"}
    max_bytes = int(s.get("research.max_source_fetch_kb", 1500)) * 1024
    try:
        # follow redirects manually so EVERY hop is validated as a public address (SSRF protection)
        current = url
        for _ in range(6):
            r = http.request("GET", current, timeout=s.get("research.fetch_timeout_sec", 20), max_bytes=max_bytes,
                             check_public=True, allow_redirects=False, retries=2,
                             headers={"Accept": "text/html,application/xhtml+xml"})
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                current = urljoin(current, r.headers["Location"])
                continue
            break
        else:
            raise http.HttpError("too many redirects")
    except Exception as e:
        return {"url": url, "final_url": url, "domain": domain_of(url), "title": "", "text": "", "sha256": "",
                "status": f"error:{str(e)[:120]}"}
    final = current
    ctype = r.headers.get("Content-Type", "")
    if r.status_code >= 400:
        status = f"error:http {r.status_code}"
        title, text = "", ""
    elif "html" not in ctype and "text" not in ctype:
        status, title, text = f"error:unsupported content-type {ctype[:40]}", "", ""
    else:
        r.encoding = r.encoding or r.apparent_encoding
        title, text = html_to_text(r.text)
        status = "ok" if len(text) > 200 else "error:page too short (blocked or JS-only)"
    return {"url": url, "final_url": final, "domain": domain_of(final), "title": title[:200], "text": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest() if text else "", "status": status}
