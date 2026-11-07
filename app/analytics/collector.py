"""YouTube Analytics collection (24 h and 7 day snapshots) and the learning engine that feeds topic selection.
Only metrics the API actually returns are stored; nothing is estimated."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import date, timedelta

from ..config import Settings
from ..db import DB
from ..log import get_logger

logger = get_logger("analytics")
METRICS = ("views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,"
           "subscribersGained,shares")


def _analytics_client():
    from googleapiclient.discovery import build
    from ..youtube_upload.publisher import credentials
    return build("youtubeAnalytics", "v2", credentials=credentials(), cache_discovery=False)


def query_video(ya, video_id: str, start: str, end: str) -> dict:
    out = {}
    r = ya.reports().query(ids="channel==MINE", startDate=start, endDate=end, metrics=METRICS,
                           filters=f"video=={video_id}").execute()
    if r.get("rows"):
        out = dict(zip([h["name"] for h in r["columnHeaders"]], r["rows"][0]))
    try:
        ts = ya.reports().query(ids="channel==MINE", startDate=start, endDate=end, metrics="views",
                                dimensions="insightTrafficSourceType", filters=f"video=={video_id}").execute()
        out["traffic_sources"] = {row[0]: row[1] for row in ts.get("rows", [])}
    except Exception as e:
        out["traffic_sources_error"] = str(e)[:120]
    try:
        ret = ya.reports().query(ids="channel==MINE", startDate=start, endDate=end, metrics="audienceWatchRatio",
                                 dimensions="elapsedVideoTimeRatio", filters=f"video=={video_id}").execute()
        out["retention"] = [[row[0], row[1]] for row in ret.get("rows", [])]
    except Exception as e:
        out["retention_error"] = str(e)[:120]
    return out


def collect(s: Settings, db: DB, ya=None) -> int:
    """Take 24h / 7d snapshots for uploaded videos that are due. Returns number of snapshots stored."""
    ups = db.query("SELECT u.youtube_id, u.created_at, j.topic FROM uploads u LEFT JOIN jobs j ON j.id=u.job_id "
                   "WHERE u.status='done' AND u.youtube_id IS NOT NULL")
    if not ups:
        return 0
    ya = ya or _analytics_client()
    n = 0
    for u in ups:
        age_h = (time.time() - u["created_at"]) / 3600
        start = date.fromtimestamp(u["created_at"]).isoformat()
        for snap, need_h in (("24h", 24), ("7d", 168)):
            if age_h < need_h or db.one("SELECT 1 FROM analytics WHERE youtube_id=? AND snapshot=?",
                                        (u["youtube_id"], snap)):
                continue
            end = (date.fromtimestamp(u["created_at"]) + timedelta(days=1 if snap == "24h" else 7)).isoformat()
            try:
                m = query_video(ya, u["youtube_id"], start, end)
            except Exception as e:
                logger.warning(f"analytics failed for {u['youtube_id']}: {str(e)[:200]}")
                continue
            db.execute("INSERT OR REPLACE INTO analytics VALUES (?,?,?,?)",
                       (u["youtube_id"], snap, time.time(), json.dumps(m)))
            n += 1
    return n


def learnings(db: DB) -> dict:
    """Aggregate 7-day snapshots by niche / ranking type / duration bucket. Returns bonuses for topic scoring."""
    rows = db.query("""SELECT a.metrics, j.data FROM analytics a JOIN uploads u ON u.youtube_id=a.youtube_id
                       JOIN jobs j ON j.id=u.job_id WHERE a.snapshot='7d'""")
    if len(rows) < 3:
        return {"summary": {"note": f"only {len(rows)} videos with 7-day data; not enough to learn yet"},
                "niche_bonus": {}}
    by_niche, by_len, hooks = defaultdict(list), defaultdict(list), []
    for r in rows:
        m, d = json.loads(r["metrics"]), json.loads(r["data"] or "{}")
        niche = (d.get("topic") or {}).get("niche", "unknown")
        views, avp = float(m.get("views", 0) or 0), float(m.get("averageViewPercentage", 0) or 0)
        by_niche[niche].append((views, avp))
        dur = float((d.get("render") or {}).get("duration", 0) or 0)
        by_len["<75s" if dur < 75 else "75-120s" if dur <= 120 else ">120s"].append(avp)
        hooks.append((avp, (d.get("script") or {}).get("hook", "")))
    all_views = [v for lst in by_niche.values() for v, _ in lst]
    avg_all = sum(all_views) / len(all_views) if all_views else 0
    niche_stats, bonus = {}, {}
    for n, lst in by_niche.items():
        av = sum(v for v, _ in lst) / len(lst)
        ret = sum(p for _, p in lst) / len(lst)
        niche_stats[n] = {"videos": len(lst), "avg_views": round(av), "avg_view_pct": round(ret, 1)}
        if len(lst) >= 2 and avg_all:
            bonus[n] = round(max(-5.0, min(5.0, 5 * (av / avg_all - 1))), 1)
    hooks.sort(reverse=True)
    return {"summary": {"niches": niche_stats,
                        "retention_by_length": {k: round(sum(v) / len(v), 1) for k, v in by_len.items()},
                        "best_hooks": [h for _, h in hooks[:3]], "weakest_hooks": [h for _, h in hooks[-2:]]},
            "niche_bonus": bonus}
