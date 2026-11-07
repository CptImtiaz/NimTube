"""Web research without a model-side search tool: Tavily finds and extracts pages from several independent
websites; the AI only READS those pages and extracts entries with verbatim quotes, which are then verified.
Page text is passed as clearly delimited DATA; the verifier never trusts the AI's quotes without checking."""
from __future__ import annotations

import re
import time

from .. import http
from ..config import Settings, env
from ..log import get_logger
from .fetcher import domain_of

logger = get_logger("tavily")
API = "https://api.tavily.com/search"


def search(query: str, max_results: int = 6) -> list[dict]:
    key = env("TAVILY_API_KEY")
    r = http.request("POST", API, timeout=60, retries=3, headers={"Authorization": f"Bearer {key}"},
                     json={"query": query[:380], "search_depth": "basic", "max_results": max_results,
                           "include_raw_content": True, "include_answer": False})
    if r.status_code >= 400:
        raise RuntimeError(f"tavily {r.status_code}: {r.text[:200]}")
    return r.json().get("results", [])


def build_queries(topic: dict, n: int) -> list[str]:
    core = re.sub(r"(?i)^top\s*10\s*", "", topic["title"]).strip()
    metric = (topic.get("metric") or "").strip()
    year = time.gmtime().tm_year
    qs = [f"list of {core} {metric}".strip(), f"{core} ranked by {metric}".strip() if metric else f"{core} ranking",
          f"{core} statistics {year}", f"{core} official data {metric}".strip()]
    return list(dict.fromkeys(qs))[:n]


def gather_sources(s: Settings, topic: dict, db=None) -> list[dict]:
    """Returns [{url, domain, title, text}] from several queries, de-duplicated, most domains first."""
    per_q = int(s.get("research.tavily_results_per_query", 6))
    max_chars = int(s.get("research.max_source_chars", 12000))
    seen, out = set(), []
    for q in build_queries(topic, int(s.get("research.tavily_queries", 3))):
        try:
            results = search(q, per_q)
            if db:
                db.add_usage("tavily", 1)
        except Exception as e:
            logger.warning(f"tavily query failed: {str(e)[:150]}")
            continue
        for r in results:
            url = r.get("url", "")
            text = r.get("raw_content") or r.get("content") or ""
            if not url.startswith("http") or url in seen or len(text) < 300:
                continue
            seen.add(url)
            out.append({"url": url, "domain": domain_of(url), "title": r.get("title", ""),
                        "text": re.sub(r"\s+", " ", text)[:max_chars]})
    # interleave domains so the AI sees several independent sites first
    by_domain: dict[str, list] = {}
    for src in out:
        by_domain.setdefault(src["domain"], []).append(src)
    ordered = []
    while any(by_domain.values()):
        for d in list(by_domain):
            if by_domain[d]:
                ordered.append(by_domain[d].pop(0))
    logger.info(f"tavily: {len(ordered)} pages from {len({x['domain'] for x in ordered})} domains")
    return ordered[:10]


def sources_block(sources: list[dict]) -> str:
    parts = []
    for i, src in enumerate(sources, 1):
        parts.append(f'<source id="{i}" url="{src["url"]}">\n{src["text"]}\n</source>')
    return "\n\n".join(parts)
