"""Trend signal sources. Each returns a list of normalised signal dicts:
{source, country, title, channel, url, views, likes, published_at, category, traffic, keywords}
"""
from __future__ import annotations

import json
from pathlib import Path

import defusedxml.ElementTree as ET

from .. import http
from ..config import Settings, env, ROOT
from ..log import get_logger

logger = get_logger("trends")
FIXTURES = ROOT / "tests" / "fixtures" / "trends"

YT_CATEGORIES = {
    "1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music", "15": "Pets & Animals", "17": "Sports",
    "19": "Travel & Events", "20": "Gaming", "22": "People & Blogs", "23": "Comedy", "24": "Entertainment",
    "25": "News & Politics", "26": "Howto & Style", "27": "Education", "28": "Science & Technology",
}


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------- YouTube Data API: mostPopular per region (1 quota unit per call) ----------------
def youtube_most_popular(s: Settings, region: str, db=None) -> list[dict]:
    if s.mock:
        data = json.loads(_fixture(f"youtube_{region}.json")) if (FIXTURES / f"youtube_{region}.json").exists() else {"items": []}
    else:
        key = env("YOUTUBE_API_KEY")
        if not key:
            return []
        data = http.get_json("https://www.googleapis.com/youtube/v3/videos", params={
            "part": "snippet,statistics", "chart": "mostPopular", "regionCode": region,
            "maxResults": s.get("trends.youtube_max_results", 50), "key": key}, timeout=30)
        if db:
            db.add_usage("youtube_data_api", 1)
    out = []
    for v in data.get("items", []):
        sn, st = v.get("snippet", {}), v.get("statistics", {})
        out.append({
            "source": "youtube", "country": region, "title": sn.get("title", ""),
            "channel": sn.get("channelTitle"), "url": f"https://www.youtube.com/watch?v={v.get('id')}",
            "views": int(st.get("viewCount", 0) or 0), "likes": int(st.get("likeCount", 0) or 0),
            "published_at": sn.get("publishedAt"), "category": YT_CATEGORIES.get(sn.get("categoryId"), sn.get("categoryId")),
            "traffic": None, "keywords": (sn.get("tags") or [])[:12],
        })
    return out


# ---------------- Google Trends: public "Trending now" RSS per country ----------------
HT = "{https://trends.google.com/trending/rss}"


def _parse_traffic(t: str | None) -> int | None:
    if not t:
        return None
    t = t.replace("+", "").replace(",", "").strip().upper()
    mult = 1
    if t.endswith("K"):
        mult, t = 1_000, t[:-1]
    elif t.endswith("M"):
        mult, t = 1_000_000, t[:-1]
    try:
        return int(float(t) * mult)
    except ValueError:
        return None


def google_trends(s: Settings, region: str) -> list[dict]:
    if s.mock:
        p = FIXTURES / f"gtrends_{region}.xml"
        if not p.exists():
            return []
        xml = p.read_text(encoding="utf-8")
    else:
        r = http.request("GET", "https://trends.google.com/trending/rss", params={"geo": region}, timeout=30,
                         max_bytes=2_000_000)
        if r.status_code >= 400:
            logger.warning(f"google trends {region}: HTTP {r.status_code}")
            return []
        xml = r.text
    out = []
    root = ET.fromstring(xml)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        news = [n.findtext(f"{HT}news_item_title") or "" for n in item.findall(f"{HT}news_item")]
        out.append({
            "source": "google_trends", "country": region, "title": title, "channel": None,
            "url": item.findtext(f"{HT}news_item_url") or None, "views": None, "likes": None,
            "published_at": item.findtext("pubDate"), "category": None,
            "traffic": _parse_traffic(item.findtext(f"{HT}approx_traffic")), "keywords": [title] + news[:2],
        })
    return out


# ---------------- News RSS: Google News per country + configured publisher feeds ----------------
GNEWS_LOCALE = {"US": "en-US", "GB": "en-GB", "IN": "en-IN", "CA": "en-CA", "AU": "en-AU", "NG": "en-NG",
                "ZA": "en-ZA", "MY": "en-MY", "DE": "en-DE", "FR": "en-FR", "BR": "en-BR", "JP": "en-JP"}


def _parse_rss(xml: str, source: str, country: str | None) -> list[dict]:
    out = []
    root = ET.fromstring(xml)
    for item in root.iter("item"):
        out.append({
            "source": source, "country": country, "title": (item.findtext("title") or "").strip(),
            "channel": (item.findtext("source") or "").strip() or None, "url": item.findtext("link"),
            "views": None, "likes": None, "published_at": item.findtext("pubDate"), "category": None,
            "traffic": None, "keywords": [],
        })
    return out


def news_rss(s: Settings, region: str) -> list[dict]:
    if s.mock:
        p = FIXTURES / f"gnews_{region}.xml"
        return _parse_rss(p.read_text(encoding="utf-8"), "news", region) if p.exists() else []
    hl = GNEWS_LOCALE.get(region, "en-US")
    r = http.request("GET", "https://news.google.com/rss", params={"hl": hl, "gl": region, "ceid": f"{region}:en"},
                     timeout=30, max_bytes=3_000_000)
    if r.status_code >= 400:
        return []
    return _parse_rss(r.text, "news", region)


def extra_feeds(s: Settings) -> list[dict]:
    if s.mock:
        return []
    out = []
    for url in s.get("trends.extra_rss_feeds", []) or []:
        try:
            r = http.request("GET", url, timeout=30, max_bytes=3_000_000, check_public=True)
            if r.status_code < 400:
                out += _parse_rss(r.text, "publisher_rss", None)
        except Exception as e:
            logger.warning(f"feed failed {url}: {e}")
    return out


# ---------------- Reddit (optional, official OAuth API, app-only) ----------------
def reddit_top(s: Settings) -> list[dict]:
    cid, secret = env("REDDIT_CLIENT_ID"), env("REDDIT_CLIENT_SECRET")
    if s.mock or not (cid and secret):
        return []
    ua = env("REDDIT_USER_AGENT", "global-top10-ai/1.0")
    r = http.request("POST", "https://www.reddit.com/api/v1/access_token", auth=(cid, secret),
                     data={"grant_type": "client_credentials"}, headers={"User-Agent": ua}, timeout=20)
    if r.status_code >= 400:
        logger.warning(f"reddit auth failed: {r.status_code}")
        return []
    token = r.json().get("access_token")
    out = []
    for sub in s.get("trends.reddit_subreddits", []) or []:
        try:
            d = http.get_json(f"https://oauth.reddit.com/r/{sub}/top", params={"t": "day", "limit": 25},
                              headers={"Authorization": f"bearer {token}", "User-Agent": ua}, timeout=20)
        except Exception as e:
            logger.warning(f"reddit r/{sub} failed: {e}")
            continue
        for c in d.get("data", {}).get("children", []):
            p = c["data"]
            if p.get("over_18"):
                continue
            out.append({"source": "reddit", "country": None, "title": p.get("title", ""), "channel": f"r/{sub}",
                        "url": "https://www.reddit.com" + p.get("permalink", ""), "views": None,
                        "likes": p.get("score"), "published_at": None, "category": sub, "traffic": None, "keywords": []})
    return out
