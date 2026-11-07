"""Collect signals from all sources, store them historically, and score keywords by worldwide spread."""
from __future__ import annotations

import json
import math
import re
import time
from collections import defaultdict

from ..config import Settings
from ..db import DB
from ..log import get_logger
from . import sources

logger = get_logger("trends")

STOPWORDS = set("""a an and are as at be by for from has have he her his how i in is it its live new news of on or
our out over says she than that the their them they this to up us vs was we what when where which who why will with
you your official video full episode trailer music lyrics feat ft shorts highlights watch day today week after
before into about just more most first last get got can could would should may not no yes all one two three
""".split())
SOURCE_WEIGHT = {"youtube": 1.0, "google_trends": 1.3, "news": 1.0, "publisher_rss": 0.8, "reddit": 0.7}


def tokens(text: str) -> list[str]:
    text = re.sub(r"[^\w\s'-]", " ", text.lower())
    words = [w.strip("'-") for w in text.split()]
    return [w for w in words if len(w) > 2 and w not in STOPWORDS and not w.isdigit()]


def keywords_for(signal: dict) -> set[str]:
    toks = tokens(signal["title"])
    kws = set(toks)
    kws |= {f"{a} {b}" for a, b in zip(toks, toks[1:])}
    for k in signal.get("keywords") or []:
        kt = tokens(str(k))
        if 0 < len(kt) <= 3:
            kws.add(" ".join(kt))
    return kws


def collect(s: Settings, db: DB) -> list[dict]:
    regions = s.get("trends.regions", ["US"])
    signals: list[dict] = []
    for region in regions:
        for fn in (lambda: sources.youtube_most_popular(s, region, db), lambda: sources.google_trends(s, region),
                   lambda: sources.news_rss(s, region)):
            try:
                signals += fn()
            except Exception as e:
                logger.warning(f"source failed for {region}: {str(e)[:200]}")
    for fn in (lambda: sources.extra_feeds(s), lambda: sources.reddit_top(s)):
        try:
            signals += fn()
        except Exception as e:
            logger.warning(f"source failed: {str(e)[:200]}")

    now = time.time()
    with db.tx():
        for sig in signals:
            db.execute("INSERT INTO trend_signals (collected_at, source, country, title, channel, url, views, likes, "
                       "published_at, category, keywords, traffic, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (now, sig["source"], sig["country"], sig["title"][:500], sig.get("channel"), sig.get("url"),
                        sig.get("views"), sig.get("likes"), sig.get("published_at"), sig.get("category"),
                        json.dumps(sig.get("keywords") or []), sig.get("traffic"), None))
    logger.info(f"collected {len(signals)} signals from {len(regions)} regions")
    return signals


def score_keywords(s: Settings, db: DB, signals: list[dict]) -> list[dict]:
    """Score = spread across countries (dominant) + weighted mentions + popularity + growth vs. previous days."""
    by_kw: dict[str, dict] = defaultdict(lambda: {"countries": set(), "mentions": 0.0, "pop": 0.0, "titles": [],
                                                  "sources": set()})
    for sig in signals:
        w = SOURCE_WEIGHT.get(sig["source"], 0.5)
        pop = math.log10((sig.get("views") or 0) + (sig.get("traffic") or 0) * 10 + 10)
        for kw in keywords_for(sig):
            d = by_kw[kw]
            if sig.get("country"):
                d["countries"].add(sig["country"])
            d["mentions"] += w
            d["pop"] += pop * w
            d["sources"].add(sig["source"])
            if len(d["titles"]) < 5:
                d["titles"].append(sig["title"][:140])

    day = time.strftime("%Y-%m-%d", time.gmtime())
    prev = {r["keyword"]: r["score"] for r in db.query(
        "SELECT keyword, AVG(score) score FROM trend_keywords WHERE day < ? AND day >= date(?, '-7 day') GROUP BY keyword",
        (day, day))}
    min_global = s.get("trends.min_countries_for_global", 3)
    results = []
    for kw, d in by_kw.items():
        if d["mentions"] < 2:
            continue
        n_c = len(d["countries"])
        score = n_c * 10 + d["mentions"] * 2 + d["pop"] + len(d["sources"]) * 5
        growth = score / prev[kw] if prev.get(kw) else None
        if growth:
            score *= min(max(growth, 0.5), 2.0) ** 0.5
        results.append({"keyword": kw, "countries": sorted(d["countries"]), "n_countries": n_c,
                        "mentions": round(d["mentions"], 1), "sources": sorted(d["sources"]),
                        "score": round(score, 1), "growth": round(growth, 2) if growth else None,
                        "is_global": n_c >= min_global, "examples": d["titles"]})
    results.sort(key=lambda r: -r["score"])
    with db.tx():
        for r in results[:300]:
            db.execute("INSERT OR REPLACE INTO trend_keywords VALUES (?,?,?,?,?)",
                       (day, r["keyword"], r["n_countries"], r["mentions"], r["score"]))
    return results


def discover(s: Settings, db: DB) -> list[dict]:
    signals = collect(s, db)
    if not signals:
        logger.warning("no trend signals collected (check API keys / network)")
        return []
    return score_keywords(s, db, signals)
