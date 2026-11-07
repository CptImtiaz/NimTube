"""Title, description, tags, hashtags, thumbnail and optional pinned comment — built deterministically from
the verified ranking (no clickbait, no invented search volumes)."""
from __future__ import annotations

import re
from pathlib import Path

from ..visual_generation.graphics import fmt_value, thumbnail

MAX_TITLE = 100
MAX_DESC = 4900


def make_title(ranking: dict) -> str:
    t = re.sub(r"\s+", " ", ranking["title"]).strip()
    if ranking["ranking_type"] == "editorial" and "ranked" not in t.lower():
        t += " (Our Ranking)"
    t = f"{t} #shorts"
    return t if len(t) <= MAX_TITLE else t[:MAX_TITLE - 8].rstrip() + " #shorts"


def make_tags(ranking: dict, niche: str) -> list[str]:
    base = re.sub(r"(?i)^top\s*10\s*", "", ranking["title"]).strip().lower()
    tags = ["top 10", base, f"top 10 {base}", niche, "ranking", "documentary", "facts"]
    tags += [e["name"].lower() for e in ranking["entries"]][:8]
    out, total = [], 0
    for t in dict.fromkeys(t for t in tags if t):
        if total + len(t) + 1 > 450:
            break
        out.append(t)
        total += len(t) + 1
    return out


def make_description(ranking: dict, script: dict, attributions: list[str], synthetic: bool, idem_key: str,
                     niche: str) -> str:
    lines = [script.get("hook", ""), "", f"{ranking['label']}: {ranking.get('methodology', '')}".strip(), ""]
    for e in sorted(ranking["entries"], key=lambda x: -x["rank"]):
        val = fmt_value(e.get("value"), e.get("unit")) if isinstance(e.get("value"), (int, float)) else ""
        lines.append(f"#{e['rank']} {e['name']}" + (f" — {val}" if val else ""))
    lines += ["", "Sources:"]
    seen = set()
    for e in ranking["entries"]:
        for ev in e["evidence"]:
            if ev["url"] not in seen and len(seen) < 15:
                seen.add(ev["url"])
                lines.append(f"- {ev['url']}")
    if attributions:
        lines += ["", "Media credits:"] + [f"- {a}" for a in attributions]
    lines += ["", "Narration is AI-generated." + (" Some visuals are AI-generated illustrations." if synthetic else ""),
              "Researched and produced with the help of AI, checked against the sources above.", "",
              f"#shorts #top10 #{re.sub(r'[^a-z0-9]', '', niche.lower()) or 'facts'}", "", f"ref: {idem_key}"]
    desc = "\n".join(lines)
    return desc.replace("<", "(").replace(">", ")")[:MAX_DESC]


def pinned_comment(ranking: dict) -> str:
    return f"Which entry surprised you most? Every figure in this ranking is listed with its source in the description."


def build_metadata(ranking: dict, script: dict, niche: str, attributions: list[str], synthetic: bool,
                   idem_key: str, work: Path) -> dict:
    thumb = thumbnail(ranking["title"], work / "thumbnail.jpg")
    return {"title": make_title(ranking), "description": make_description(ranking, script, attributions, synthetic,
                                                                          idem_key, niche),
            "tags": make_tags(ranking, niche), "thumbnail": str(thumb), "pinned_comment": pinned_comment(ranking),
            "contains_synthetic_media": synthetic}
