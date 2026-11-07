"""Interchangeable LLM providers (Claude Messages API, OpenAI Responses API, Gemini API) over plain REST.

Two capabilities:
  complete_json(task, system, prompt)  -> dict         plain generation, JSON output
  research_json(task, system, prompt)  -> (dict, urls) generation grounded with live web search
Providers are tried in settings.ai.llm_provider_order; only providers with an API key are used.
In mock mode a fixture-backed provider answers instead (tests / offline demo only).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import http
from .config import Settings, env, ROOT
from .log import get_logger

logger = get_logger("llm")

# Rough USD per 1M tokens (input, output) for cost reporting only. Update when prices change.
PRICES = {
    "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.00, 8.00),
    "gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-pro": (1.25, 10.0),
}
WEB_SEARCH_CALL_USD = 0.01


class LLMError(RuntimeError):
    pass


def parse_json(text: str) -> dict:
    """Extract the first JSON object from model text (handles ```json fences and prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        raise LLMError("no JSON object in model output")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            esc = (c == "\\") and not esc
            if c == '"' and not esc:
                in_str = False
            elif c != "\\":
                esc = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise LLMError("unterminated JSON object in model output")


class OpenAIProvider:
    name = "openai"
    URL = "https://api.openai.com/v1/responses"

    def __init__(self, s: Settings, db=None):
        self.key = env("OPENAI_API_KEY")
        self.model = s.get("ai.openai_model", "gpt-4.1-mini")
        self.research_model = s.get("ai.openai_research_model", "gpt-4.1")
        self.temperature = s.get("ai.temperature", 0.6)
        self.db = db

    @property
    def available(self):
        return bool(self.key)

    def _call(self, body: dict) -> dict:
        r = http.request("POST", self.URL, json=body, timeout=180,
                         headers={"Authorization": f"Bearer {self.key}"})
        if r.status_code >= 400:
            raise LLMError(f"openai {r.status_code}: {r.text[:400]}")
        data = r.json()
        u = data.get("usage") or {}
        p = PRICES.get(body["model"], (0, 0))
        cost = (u.get("input_tokens", 0) * p[0] + u.get("output_tokens", 0) * p[1]) / 1e6
        if self.db:
            self.db.add_usage("openai", u.get("total_tokens", 0), cost)
        return data

    @staticmethod
    def _text_and_urls(data: dict) -> tuple[str, list[dict]]:
        texts, urls = [], []
        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    texts.append(c.get("text", ""))
                    for a in c.get("annotations", []) or []:
                        if a.get("type") == "url_citation" and a.get("url"):
                            urls.append({"url": a["url"], "title": a.get("title", "")})
        return "\n".join(texts), urls

    def complete(self, system: str, prompt: str) -> str:
        data = self._call({"model": self.model, "instructions": system, "input": prompt,
                           "temperature": self.temperature, "text": {"format": {"type": "json_object"}}})
        return self._text_and_urls(data)[0]

    def research(self, system: str, prompt: str) -> tuple[str, list[dict]]:
        data = self._call({"model": self.research_model, "instructions": system, "input": prompt,
                           "tools": [{"type": "web_search"}], "temperature": 0.2})
        if self.db:
            self.db.add_usage("openai_web_search", 1, WEB_SEARCH_CALL_USD)
        return self._text_and_urls(data)


class AnthropicProvider:
    """Claude via the Messages API. Research uses the server-side web search tool (web_search_20250305);
    the org admin must enable web search in the Claude Console."""
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, s: Settings, db=None):
        self.key = env("ANTHROPIC_API_KEY")
        self.model = s.get("ai.anthropic_model", "claude-sonnet-5-5")
        self.research_model = s.get("ai.anthropic_research_model", self.model)
        self.temperature = s.get("ai.temperature", 0.6)
        self.prices = s.get("ai.anthropic_prices_per_mtok")      # [input, output] USD — set in settings.yaml
        self.max_searches = int(s.get("ai.anthropic_max_searches", 8))
        self.db = db

    @property
    def available(self):
        return bool(self.key)

    def _call(self, body: dict) -> dict:
        r = http.request("POST", self.URL, json=body, timeout=300,
                         headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                                  "content-type": "application/json"})
        if r.status_code >= 400:
            raise LLMError(f"anthropic {r.status_code}: {r.text[:400]}")
        data = r.json()
        u = data.get("usage") or {}
        cost = 0.0
        if self.prices:
            cost = (u.get("input_tokens", 0) * self.prices[0] + u.get("output_tokens", 0) * self.prices[1]) / 1e6
        searches = (u.get("server_tool_use") or {}).get("web_search_requests", 0)
        if self.db:
            self.db.add_usage("anthropic", u.get("input_tokens", 0) + u.get("output_tokens", 0), cost)
            if searches:
                self.db.add_usage("anthropic_web_search", searches, searches * WEB_SEARCH_CALL_USD)
        return data

    @staticmethod
    def _text_and_urls(content: list) -> tuple[str, list[dict]]:
        texts, urls = [], []
        for b in content:
            if b.get("type") == "text":
                texts.append(b.get("text", ""))
                for c in b.get("citations") or []:
                    if c.get("url"):
                        urls.append({"url": c["url"], "title": c.get("title", "")})
            elif b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
                for res in b["content"]:
                    if res.get("url"):
                        urls.append({"url": res["url"], "title": res.get("title", "")})
        return "".join(texts), urls

    def complete(self, system: str, prompt: str) -> str:
        data = self._call({"model": self.model, "max_tokens": 8000, "temperature": self.temperature,
                           "system": system + "\nRespond with a single valid JSON object and nothing else.",
                           "messages": [{"role": "user", "content": prompt}]})
        return self._text_and_urls(data.get("content", []))[0]

    def research(self, system: str, prompt: str) -> tuple[str, list[dict]]:
        messages = [{"role": "user", "content": prompt}]
        body = {"model": self.research_model, "max_tokens": 12000, "temperature": 0.2,
                "system": system + "\nAfter researching, reply with a single valid JSON object as your final text.",
                "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": self.max_searches}],
                "messages": messages}
        all_content: list = []
        for _ in range(5):                       # long searches may pause the turn; continue it
            data = self._call(body)
            all_content += data.get("content", [])
            if data.get("stop_reason") != "pause_turn":
                break
            messages.append({"role": "assistant", "content": data.get("content", [])})
        final_text, _ = self._text_and_urls(data.get("content", []))
        _, urls = self._text_and_urls(all_content)
        return final_text, urls


class GeminiProvider:
    name = "gemini"

    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, s: Settings, db=None):
        self.key = env("GEMINI_API_KEY")
        self.wanted = env("GEMINI_MODEL") or s.get("ai.gemini_model", "auto")
        self._model: str | None = None
        # research may use a different model (e.g. one whose free tier includes Google Search grounding)
        self.research_model = env("GEMINI_RESEARCH_MODEL") or s.get("ai.gemini_research_model") or ""
        self.temperature = s.get("ai.temperature", 0.6)
        self.min_interval = float(s.get("ai.gemini_min_seconds_between_calls", 6))   # free tier: low RPM
        self._last = 0.0
        self.db = db

    @property
    def available(self):
        return bool(self.key)

    @staticmethod
    def pick_model(names: list[str], wanted: str = "auto") -> str:
        """Choose a model ID from the account's ListModels result. 'auto' = newest stable Flash model."""
        ids = [n.split("/", 1)[-1] for n in names]
        if wanted and wanted != "auto" and wanted in ids:
            return wanted
        def version(mid: str) -> tuple:
            m = re.search(r"gemini-(\d+(?:\.\d+)?)", mid)
            return (float(m.group(1)) if m else 0.0,)
        flash = [i for i in ids if re.fullmatch(r"gemini-\d+(\.\d+)?-flash", i)]          # stable, non-lite
        if not flash:
            flash = [i for i in ids if "flash" in i and not any(x in i for x in ("image", "tts", "audio", "live",
                                                                                  "embedding", "thinking"))]
        if not flash:
            raise LLMError(f"no Gemini Flash model available to this key (saw {ids[:8]})")
        return sorted(flash, key=version)[-1]

    @property
    def model(self) -> str:
        """An exact model ID from settings/GEMINI_MODEL is used as-is; 'auto' asks Google which models exist."""
        if self._model is None:
            if self.wanted and self.wanted != "auto":
                self._model = self.wanted
            else:
                r = http.request("GET", f"{self.BASE}/models", params={"pageSize": 200}, timeout=30,
                                 headers={"x-goog-api-key": self.key})
                if r.status_code >= 400:
                    raise LLMError(f"gemini ListModels {r.status_code}: {r.text[:300]}")
                names = [m["name"] for m in r.json().get("models", [])
                         if "generateContent" in (m.get("supportedGenerationMethods") or [])]
                self._model = self.pick_model(names, "auto")
            logger.info(f"gemini model: {self._model}")
        return self._model

    @staticmethod
    def _explain_error(r) -> str:
        try:
            err = r.json().get("error", {})
        except Exception:
            return r.text[:400]
        msg = err.get("message", "")[:200]
        extra = []
        for d in err.get("details", []) or []:
            for v in d.get("violations", []) or []:
                extra.append(f"{v.get('quotaMetric', '')} {v.get('quotaId', '')} limit={v.get('quotaValue', '?')}")
            if d.get("retryDelay"):
                extra.append(f"retry after {d['retryDelay']}")
        return f"{err.get('status', '')} {msg} | {'; '.join(extra)}"[:900]

    def _call(self, body: dict, model: str | None = None) -> dict:
        import time as _t
        wait = self._last + self.min_interval - _t.monotonic()
        if wait > 0:
            _t.sleep(wait)
        use = model or self.model
        url = f"{self.BASE}/models/{use}:generateContent"
        r = http.request("POST", url, json=body, timeout=240, headers={"x-goog-api-key": self.key}, retries=5)
        self._last = _t.monotonic()
        if r.status_code in (404, 400) and model and model != self.model and "not found" in r.text.lower():
            logger.warning(f"gemini research model {model} is not available — using {self.model}")
            self.research_model = ""
            return self._call(body)
        if r.status_code >= 400:
            raise LLMError(f"gemini {r.status_code} ({use}): {self._explain_error(r)}")
        data = r.json()
        u = data.get("usageMetadata") or {}
        p = PRICES.get(self.model, (0, 0))
        cost = (u.get("promptTokenCount", 0) * p[0] + u.get("candidatesTokenCount", 0) * p[1]) / 1e6
        if self.db:
            self.db.add_usage("gemini", u.get("totalTokenCount", 0), cost)
        return data

    @staticmethod
    def _text(data: dict) -> str:
        cands = data.get("candidates") or []
        if not cands:
            raise LLMError(f"gemini returned no candidates: {str(data)[:300]}")
        return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", [])
                       if not p.get("thought"))            # skip thinking parts of newer models

    def complete(self, system: str, prompt: str) -> str:
        data = self._call({
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": self.temperature, "responseMimeType": "application/json"},
        })
        return self._text(data)

    def research(self, system: str, prompt: str) -> tuple[str, list[dict]]:
        data = self._call({
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.2},
        }, model=self.research_model or None)
        meta = (data.get("candidates") or [{}])[0].get("groundingMetadata", {}) or {}
        urls = [{"url": c["web"]["uri"], "title": c["web"].get("title", "")}
                for c in meta.get("groundingChunks", []) if c.get("web", {}).get("uri")]
        return self._text(data), urls


class MockProvider:
    """Answers from tests/fixtures/llm/<task>.json — never used in production modes unless mock=True."""
    name = "mock"
    available = True

    def __init__(self, fixtures_dir: Path | None = None):
        self.dir = fixtures_dir or ROOT / "tests" / "fixtures" / "llm"
        self.task = ""

    def _load(self) -> dict:
        p = self.dir / f"{self.task}.json"
        if not p.exists():
            raise LLMError(f"mock fixture missing: {p}")
        return json.loads(p.read_text(encoding="utf-8"))

    def complete(self, system, prompt):
        return json.dumps(self._load()["response"])

    def research(self, system, prompt):
        d = self._load()
        return json.dumps(d["response"]), d.get("citations", [])


class LLM:
    def __init__(self, s: Settings, db=None):
        if s.mock:
            self.providers = [MockProvider()]
        else:
            all_p = {"anthropic": AnthropicProvider(s, db), "openai": OpenAIProvider(s, db),
                     "gemini": GeminiProvider(s, db)}
            order = s.get("ai.llm_provider_order", ["anthropic", "openai", "gemini"])
            self.providers = [all_p[n] for n in order if n in all_p and all_p[n].available]
        if not self.providers:
            raise LLMError("No LLM provider configured. Set ANTHROPIC_API_KEY (or OPENAI_API_KEY / GEMINI_API_KEY).")

    def _run(self, task: str, fn_name: str, system: str, prompt: str):
        errors = []
        for p in self.providers:
            if isinstance(p, MockProvider):
                p.task = task
            try:
                logger.info(f"{task}: {p.name}.{fn_name}")
                return getattr(p, fn_name)(system, prompt), p.name
            except Exception as e:  # try next provider
                errors.append(f"{p.name}: {e}")
                logger.warning(f"{task}: provider {p.name} failed: {str(e)[:200]}")
        raise LLMError(f"all providers failed for {task}: {' | '.join(errors)}")

    def complete_json(self, task: str, system: str, prompt: str) -> dict:
        text, _ = self._run(task, "complete", system, prompt)
        return parse_json(text)

    def research_json(self, task: str, system: str, prompt: str) -> tuple[dict, list[dict]]:
        (text, urls), _ = self._run(task, "research", system, prompt)
        return parse_json(text), urls
