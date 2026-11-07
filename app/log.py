"""Structured JSON logging with secret redaction."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time

SECRET_ENV_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSPHRASE", "CREDENTIAL")
_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),                 # OpenAI-style keys
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),               # Google API keys
    re.compile(r"ya29\.[0-9A-Za-z_\-\.]+"),                # Google OAuth access tokens
    re.compile(r"1//[0-9A-Za-z_\-]{20,}"),                 # Google refresh tokens
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.=]{12,}"),
    re.compile(r"(?i)([?&](key|api_key|token|access_token)=)[^&\s]+"),
]


def _secret_values() -> list[str]:
    vals = []
    for k, v in os.environ.items():
        if v and len(v) >= 8 and any(h in k.upper() for h in SECRET_ENV_HINTS):
            vals.append(v)
    return sorted(vals, key=len, reverse=True)


def redact(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    for v in _secret_values():
        text = text.replace(v, "***")
    for p in _PATTERNS:
        text = p.sub(lambda m: (m.group(1) if m.groups() and m.group(1) else "") + "***", text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "module": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "data", None)
        if extra:
            payload["data"] = json.loads(redact(json.dumps(extra, default=str)))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"gt10.{name}")
    if not logging.getLogger("gt10").handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(JsonFormatter())
        root = logging.getLogger("gt10")
        root.addHandler(h)
        root.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
        root.propagate = False
    return logger


def log(logger: logging.Logger, msg: str, **data):
    logger.info(msg, extra={"data": data} if data else None)
