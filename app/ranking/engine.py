"""Top 10 ranking intelligence: builds #10..#1 from VERIFIED research only and enforces methodology rules."""
from __future__ import annotations

import re
import time
from statistics import median

from ..config import Settings
from ..log import get_logger

logger = get_logger("ranking")


class RankingRejected(RuntimeError):
    """The topic must not be published (not enough evidence / inconsistent methodology)."""


def _norm_name(n: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (n or "").lower())


def _year(as_of) -> int | None:
    m = re.search(r"(19|20)\d{2}", str(as_of or ""))
    return int(m.group(0)) if m else None


def build_ranking(s: Settings, topic: dict, research: dict) -> dict:
    rules = s.rules.get("general", {})
    size = int(s.get("production.ranking_size", 10))
    rtype = topic.get("ranking_type", "factual")
    min_sources = int(s.get("research.min_independent_sources", 2))

    # merge duplicates (same entity listed twice)
    merged: dict[str, dict] = {}
    for e in research["entries"]:
        k = _norm_name(e["name"])
        if k in merged:
            merged[k]["verified_evidence"] += e["verified_evidence"]
            merged[k]["evidence_domains"] = sorted(set(merged[k]["evidence_domains"]) | set(e["evidence_domains"]))
        else:
            merged[k] = dict(e)
    entries = list(merged.values())

    problems = []
    if rtype == "factual":
        units = {(e.get("unit") or "").strip().lower() for e in entries}
        if rules.get("require_single_metric", True) and len(units) > 1:
            raise RankingRejected(f"mixed units in factual ranking: {sorted(units)}")
        entries = [e for e in entries if isinstance(e.get("value"), (int, float))]
        reverse = research.get("sort", "desc") != "asc"
        entries.sort(key=lambda e: e["value"], reverse=reverse)
    if len(entries) < size:
        raise RankingRejected(f"only {len(entries)} entries have verified evidence (need {size})")

    top = entries[:size]
    domains = sorted({d for e in top for d in e["evidence_domains"]})
    if len(domains) < min_sources:
        raise RankingRejected(f"evidence comes from {len(domains)} domain(s); need {min_sources} independent sources")

    if rtype == "factual" and rules.get("require_monotonic_values", True):
        vals = [e["value"] for e in top]
        ok = all(a >= b for a, b in zip(vals, vals[1:])) or all(a <= b for a, b in zip(vals, vals[1:]))
        if not ok:
            raise RankingRejected("values are not monotonic")
        # ties at the cut-off make the ranking arbitrary
        if len(entries) > size and entries[size]["value"] == top[-1]["value"]:
            problems.append(f"tie at #10 with {entries[size]['name']}")

    max_age = int(rules.get("max_value_age_years", 5))
    this_year = time.gmtime().tm_year
    out = []
    for i, e in enumerate(top):
        rank = i + 1                                  # position 1 = best
        y = _year(e.get("as_of"))
        out.append({
            "rank": rank, "name": e["name"], "value": e.get("value"), "unit": e.get("unit"),
            "as_of": e.get("as_of"), "stale": bool(y and this_year - y > max_age),
            "evidence": [{"url": ev["url"], "publisher": ev.get("publisher"), "quote": ev["quote"],
                          "claim": ev.get("claim"), "published": ev.get("published")} for ev in e["verified_evidence"]],
            "evidence_domains": e["evidence_domains"], "notes": e.get("notes", ""),
        })
    # countdown order for the video: #10 first ... #1 last
    countdown = sorted(out, key=lambda x: -x["rank"])
    return {
        "title": topic["title"], "ranking_type": rtype,
        "label": "Editorial ranking — our assessment" if rtype == "editorial" else "Factual ranking",
        "metric": topic.get("metric"), "unit": topic.get("unit"), "methodology": research.get("methodology", ""),
        "entries": countdown, "domains": domains, "problems": problems,
        "missing_information": research.get("missing_information", ""),
        "median_value": median([e["value"] for e in top]) if rtype == "factual" else None,
    }
