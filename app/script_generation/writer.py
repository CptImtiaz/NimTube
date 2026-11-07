"""Documentary script writer: original narration built ONLY from verified facts, sized to the target duration."""
from __future__ import annotations

import json

from ..config import Settings
from ..llm import LLM
from ..log import get_logger

logger = get_logger("script")

BANNED_PHRASES = ["delve", "in today's video", "buckle up", "without further ado", "let's dive in", "dive into",
                  "smash that like", "you won't believe", "mind-blowing", "game-changer", "in conclusion",
                  "tapestry", "testament to", "it's worth noting", "unleash"]

SYSTEM = """You are the head writer of GLOBAL TOP 10 AI, a cinematic documentary Shorts channel.
Voice: calm, confident, curious documentary narrator; conversational, vivid, precise. Short sentences.
You write ORIGINAL narration. You may only state facts that are in the VERIFIED FACTS provided —
no extra numbers, dates, records or superlatives. Round numbers naturally only if the meaning stays exact
("about 5,300 kilometres" for 5,268 km is NOT allowed; say "5,268 kilometres" or "over 5,200 kilometres").
Never use these phrases: """ + ", ".join(BANNED_PHRASES)

PROMPT = """VIDEO: {title}
Ranking type: {label}. Metric: {metric} ({unit}). Methodology: {methodology}
Target narration length: about {words} words total (≈{seconds}s). Spend ~{per_entry} words per entry,
more on #1. Entries with "stale": true must mention the year of the figure.

VERIFIED FACTS (countdown order, #10 first):
{facts}

Write the script. Structure: powerful hook (1 sentence, a surprising verified fact or question) -> brief intro
(state what is ranked and how{editorial_note}) -> #10 down to #1 with smooth transitions -> strong #1 reveal ->
memorable ending -> optional one-line subscribe invitation.
For each entry also plan the REAL FOOTAGE that will be on screen (licensed stock video search terms).
Footage queries must be concrete and filmable, 1-4 words, things a stock camera crew could shoot
(e.g. "jupiter planet space", "ice cracks close up", "volcano lava night") — never names of private people,
brands, logos, movies or TV shows. Give 3 alternatives per entry, most specific first.
Also give one AI image prompt as a fallback (no text, no logos, no real identifiable people).

Return JSON:
{{"hook": "...", "intro": "...",
  "intro_footage": ["...", "...", "..."],
  "segments": [{{"rank": 10, "name": "...", "narration": "Number ten: ...", "on_screen": "<short value label>",
                "footage": ["...", "...", "..."], "image_prompt": "...", "fact_ids": [0]}}],
  "ending": "...", "outro_footage": ["...", "..."], "subscribe": "..."}}
fact_ids are indexes into that entry's evidence list."""


def _facts_block(ranking: dict) -> str:
    lines = []
    for e in ranking["entries"]:
        ev = "; ".join(f"[{i}] {x['claim'] or x['quote']}" for i, x in enumerate(e["evidence"]))
        val = f"{e['value']:,} {e['unit']}" if isinstance(e.get("value"), (int, float)) else "n/a"
        lines.append(f"#{e['rank']} {e['name']} | value: {val} | as_of: {e.get('as_of')} | stale: {e['stale']} | facts: {ev}")
    return "\n".join(lines)


def write_script(s: Settings, llm: LLM, ranking: dict, feedback: str = "") -> dict:
    seconds = int(s.get("production.target_duration_sec", 90))
    wps = float(s.get("production.words_per_second", 2.5))
    words = int(seconds * wps)
    per_entry = max(12, int((words - 45) / len(ranking["entries"])))
    prompt = PROMPT.format(
        title=ranking["title"], label=ranking["label"], metric=ranking.get("metric"), unit=ranking.get("unit"),
        methodology=ranking.get("methodology", ""), words=words, seconds=seconds, per_entry=per_entry,
        facts=_facts_block(ranking),
        editorial_note="; say clearly that this is our editorial ranking and name the criteria"
        if ranking["ranking_type"] == "editorial" else "")
    if feedback:
        prompt += f"\n\nYOUR PREVIOUS DRAFT FAILED REVIEW. Fix these problems:\n{feedback}"
    script = llm.complete_json("script", SYSTEM, prompt)
    by_rank = {e["rank"]: e for e in ranking["entries"]}
    script["segments"] = sorted([seg for seg in script.get("segments", []) if seg.get("rank") in by_rank],
                                key=lambda x: -x["rank"])
    return script


def full_text(script: dict) -> str:
    parts = [script.get("hook", ""), script.get("intro", "")]
    parts += [seg.get("narration", "") for seg in script.get("segments", [])]
    parts += [script.get("ending", ""), script.get("subscribe", "")]
    return " ".join(p for p in parts if p)


def to_json(script: dict) -> str:
    return json.dumps(script, indent=2, ensure_ascii=False)
