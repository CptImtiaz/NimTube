"""Script review: every number in the narration must be backed by a verified fact for that entry,
no copied source sentences, no banned AI phrasing, and length within the duration limits."""
from __future__ import annotations

import re

from ..config import Settings
from ..research.verifier import numbers_in, normalise
from ..script_generation.writer import BANNED_PHRASES, full_text

WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10}


def _supported_numbers(entry: dict) -> list[float]:
    nums = [float(r) for r in range(1, 11)]
    if isinstance(entry.get("value"), (int, float)):
        nums.append(float(entry["value"]))
    for ev in entry["evidence"]:
        nums += numbers_in(ev.get("quote", "")) + numbers_in(ev.get("claim") or "")
    nums += numbers_in(str(entry.get("as_of") or ""))
    return nums


def _is_supported(n: float, supported: list[float]) -> bool:
    for s in supported:
        if s == n:
            return True
        if s and abs(s - n) / abs(s) < 0.006:
            return True
        # "over 5,200" style lower bounds are allowed if below a supported figure and >= 95% of it
        if s and n < s and n >= 0.95 * s and n == round(n, -1 if n < 1000 else -2):
            return True
    return False


def ngrams(text: str, n: int = 8) -> set[tuple]:
    t = normalise(text).split()
    return {tuple(t[i:i + n]) for i in range(len(t) - n + 1)}


def check_script(s: Settings, script: dict, ranking: dict) -> list[str]:
    problems: list[str] = []
    by_rank = {e["rank"]: e for e in ranking["entries"]}
    ranks = [seg.get("rank") for seg in script.get("segments", [])]
    if sorted(ranks, reverse=True) != list(range(len(by_rank), 0, -1)):
        problems.append(f"segments must cover #{len(by_rank)}..#1 exactly once; got {ranks}")

    for seg in script.get("segments", []):
        e = by_rank.get(seg.get("rank"))
        if not e:
            continue
        text = seg.get("narration", "")
        name_tokens = [t for t in normalise(e["name"]).split() if t not in ("the", "a", "an")] or [e["name"].lower()]
        if name_tokens[0] not in normalise(text):
            problems.append(f"#{seg['rank']}: narration does not name '{e['name']}'")
        supported = _supported_numbers(e)
        for other in ranking["entries"]:          # comparisons with another entry it names are allowed
            if other is not e and normalise(other["name"]).replace("the ", "") in normalise(text):
                supported += _supported_numbers(other)
        for n in numbers_in(text):
            if not _is_supported(n, supported):
                problems.append(f"#{seg['rank']}: number {n:g} is not in the verified facts")
        if e.get("stale"):
            y = re.search(r"(19|20)\d{2}", str(e.get("as_of")))
            if y and y.group(0) not in text:
                problems.append(f"#{seg['rank']}: figure is from {y.group(0)}; narration must say so")
        quotes = " ".join(ev["quote"] for ev in e["evidence"])
        if ngrams(text) & ngrams(quotes):
            problems.append(f"#{seg['rank']}: narration copies 8+ consecutive words from a source")

    # global numbers in hook/intro/ending must be supported by SOME entry
    all_supported = [n for e in ranking["entries"] for n in _supported_numbers(e)]
    for part in ("hook", "intro", "ending"):
        for n in numbers_in(script.get(part, "")):
            if not _is_supported(n, all_supported):
                problems.append(f"{part}: number {n:g} is not in the verified facts")

    text = full_text(script).lower()
    for ph in BANNED_PHRASES:
        if ph in text:
            problems.append(f"banned phrase used: '{ph}'")

    wps = float(s.get("production.words_per_second", 2.5))
    words = len(full_text(script).split())
    est = words / wps
    lo, hi = s.get("production.min_duration_sec", 60), s.get("production.max_duration_sec", 180)
    if est < lo * 0.9:
        problems.append(f"script too short: {words} words ≈ {est:.0f}s (min {lo}s)")
    if est > hi:
        problems.append(f"script too long: {words} words ≈ {est:.0f}s (max {hi}s)")
    if ranking["ranking_type"] == "editorial" and not re.search(r"(?i)\b(our|editorial)\b", script.get("intro", "")):
        problems.append("editorial ranking must be labelled as our editorial assessment in the intro")
    return problems
