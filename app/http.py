"""HTTP client: timeouts, retries with exponential backoff + jitter, per-host rate limiting,
response size caps, and scheme/host validation (no file://, no private network targets)."""
from __future__ import annotations

import ipaddress
import random
import socket
import threading
import time
from urllib.parse import urlparse

import requests

from .log import get_logger, redact

logger = get_logger("http")
USER_AGENT = "GlobalTop10AI/1.0 (+https://github.com/; research bot)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    pass


class RateLimiter:
    def __init__(self, min_interval: float = 0.5):
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str):
        with self._lock:
            now = time.monotonic()
            delay = self._last.get(host, 0) + self.min_interval - now
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()


_limiter = RateLimiter()
_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


def validate_public_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise HttpError(f"blocked URL scheme/host: {url[:120]}")
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror as e:
        raise HttpError(f"DNS failure for {p.hostname}: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HttpError(f"blocked non-public address for {p.hostname}")
    return url


def request(method: str, url: str, *, retries: int = 4, timeout: float = 30, max_bytes: int | None = None,
            check_public: bool = False, **kw) -> requests.Response:
    if check_public:
        validate_public_url(url)
    host = urlparse(url).hostname or ""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        _limiter.wait(host)
        try:
            resp = _session.request(method, url, timeout=timeout, stream=max_bytes is not None, **kw)
            if resp.status_code in RETRY_STATUS and attempt < retries:
                ra = resp.headers.get("Retry-After")
                delay = float(ra) if ra and ra.isdigit() else (2 ** attempt) + random.random()
                logger.warning(f"{resp.status_code} from {host}, retry in {delay:.1f}s")
                time.sleep(min(delay, 60))
                continue
            if max_bytes is not None:
                chunks, total = [], 0
                for chunk in resp.iter_content(65536):
                    total += len(chunk)
                    if total > max_bytes:
                        break
                    chunks.append(chunk)
                resp._content = b"".join(chunks)
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep((2 ** attempt) + random.random())
    raise HttpError(redact(f"{method} {url} failed after {retries + 1} attempts: {last_exc}"))


def get_json(url: str, **kw) -> dict:
    r = request("GET", url, **kw)
    if r.status_code >= 400:
        raise HttpError(redact(f"GET {url} -> {r.status_code}: {r.text[:300]}"))
    return r.json()


def post_json(url: str, payload: dict, **kw) -> dict:
    r = request("POST", url, json=payload, **kw)
    if r.status_code >= 400:
        raise HttpError(redact(f"POST {url} -> {r.status_code}: {r.text[:500]}"))
    return r.json()
