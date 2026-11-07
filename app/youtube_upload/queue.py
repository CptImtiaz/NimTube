"""Nightly batch -> timed publishing.

  nightly : the pipeline produces one video per open slot; each finished video is hosted (GitHub Release asset
            on the public repo, or an S3 bucket) and stored in the publish_queue with its slot time.
  publish : a short workflow at each slot sends the due video to Zapier (YouTube: Upload Video) — immediately,
            so it works on every channel (Zapier's "Publish At" is limited to YouTube Partner accounts).
Every queued video is sent at most once.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import http
from ..config import Settings, env
from ..db import DB
from ..log import get_logger
from .publisher import PublishError, decide_privacy, idempotency_key

logger = get_logger("queue")
GH_API = "https://api.github.com"


# ---------------- slots ----------------
def upcoming_slots(s: Settings, now: datetime | None = None, count: int | None = None,
                   min_lead_min: int = 75) -> list[str]:
    """Next `count` slot datetimes (ISO, UTC) at least `min_lead_min` minutes from now."""
    now = now or datetime.now(timezone.utc)
    count = count or int(s.get("production.videos_per_day", 3))
    times = list(s.get("publishing.slots_utc", ["23:30", "17:30", "11:30"]))[:max(count, 1)]   # priority order
    out = []
    day = now.date()
    while len(out) < count:
        for t in sorted(times):
            hh, mm = map(int, t.split(":"))
            dt = datetime(day.year, day.month, day.day, hh, mm, tzinfo=timezone.utc)
            if dt >= now + timedelta(minutes=min_lead_min):
                out.append(dt.strftime("%Y-%m-%dT%H:%M:00Z"))
                if len(out) == count:
                    break
        day += timedelta(days=1)
    return out


def open_slots(s: Settings, db: DB, now: datetime | None = None) -> list[str]:
    taken = {r["slot_utc"] for r in db.query("SELECT slot_utc FROM publish_queue")}
    return [x for x in upcoming_slots(s, now) if x not in taken]


# ---------------- hosting ----------------
def _gh_headers():
    tok = env("GITHUB_TOKEN")
    if not tok or not env("GITHUB_REPOSITORY"):
        raise PublishError("GITHUB_TOKEN / GITHUB_REPOSITORY not available (run inside GitHub Actions)")
    return {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _release(tag: str) -> dict:
    repo, h = env("GITHUB_REPOSITORY"), _gh_headers()
    r = http.request("GET", f"{GH_API}/repos/{repo}/releases/tags/{tag}", headers=h, timeout=30)
    if r.status_code == 200:
        return r.json()
    r = http.request("POST", f"{GH_API}/repos/{repo}/releases", headers=h, timeout=30, json={
        "tag_name": tag, "name": f"Publish queue {tag}", "prerelease": True,
        "body": "Temporary hosting for scheduled YouTube uploads. Deleted automatically after a few days."})
    if r.status_code >= 400:
        raise PublishError(f"could not create release {tag}: {r.status_code} {r.text[:200]}")
    return r.json()


def host_github_release(path: Path, name: str, content_type: str) -> str:
    rel = _release(f"queue-{time.strftime('%Y%m%d', time.gmtime())}")
    upload = rel["upload_url"].split("{")[0]
    with open(path, "rb") as f:
        r = http.request("POST", upload, params={"name": name}, data=f.read(), timeout=600, retries=2,
                         headers={**_gh_headers(), "Content-Type": content_type})
    if r.status_code >= 400:
        raise PublishError(f"asset upload failed: {r.status_code} {r.text[:200]}")
    return r.json()["browser_download_url"]


def host(s: Settings, path: Path, name: str, content_type: str) -> str:
    if s.get("publishing.hosting", "github_release") == "s3":
        from .zapier import store_and_sign
        return store_and_sign(path, f"videos/{name}", int(s.get("publishing.link_valid_hours", 24)), content_type)
    return host_github_release(path, name, content_type)


def cleanup_hosting(s: Settings) -> int:
    if s.get("publishing.hosting", "github_release") != "github_release" or not env("GITHUB_TOKEN"):
        return 0
    repo, h = env("GITHUB_REPOSITORY"), _gh_headers()
    keep = int(s.get("publishing.keep_hosted_days", 3))
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep)
    n = 0
    r = http.request("GET", f"{GH_API}/repos/{repo}/releases", headers=h, params={"per_page": 100}, timeout=30)
    for rel in (r.json() if r.status_code == 200 else []):
        tag = rel.get("tag_name", "")
        if not tag.startswith("queue-"):
            continue
        created = datetime.fromisoformat(rel["created_at"].replace("Z", "+00:00"))
        if created < cutoff:
            http.request("DELETE", f"{GH_API}/repos/{repo}/releases/{rel['id']}", headers=h, timeout=30)
            http.request("DELETE", f"{GH_API}/repos/{repo}/git/refs/tags/{tag}", headers=h, timeout=30)
            n += 1
    return n


# ---------------- queue ----------------
def enqueue(s: Settings, db: DB, job_id: str, video: Path, meta: dict, slot_utc: str, hoster=None) -> dict:
    key = idempotency_key(job_id, video)
    if key not in meta["description"]:
        raise PublishError("description must contain the idempotency reference")
    row = db.one("SELECT * FROM publish_queue WHERE idempotency_key=?", (key,))
    if row:
        return {"status": f"already_{row['status']}", "slot_utc": row["slot_utc"], "video_url": row["video_url"]}
    hoster = hoster or (lambda p, n, ct: host(s, p, n, ct))
    video_url = hoster(video, f"{job_id}.mp4", "video/mp4")
    thumb_url = ""
    if meta.get("thumbnail") and Path(meta["thumbnail"]).exists():
        thumb_url = hoster(Path(meta["thumbnail"]), f"{job_id}-thumbnail.jpg", "image/jpeg")
    db.execute("INSERT INTO publish_queue VALUES (?,?,?,?,?,?,?,?,?,?)",
               (key, job_id, slot_utc, video_url, thumb_url, json.dumps(meta), "queued", time.time(), None, None))
    if s.get("publishing.backend", "zapier") == "claude_zapier":
        # the scheduled Claude upload task reads this manifest from the release and uploads via Zapier
        row = db.one("SELECT * FROM publish_queue WHERE idempotency_key=?", (key,))
        manifest = Path(video).with_name(f"{job_id}.json")
        manifest.write_text(json.dumps(payload_for(s, row), indent=2), encoding="utf-8")
        hoster(manifest, f"{job_id}.json", "application/json")
    logger.info(f"queued {job_id} for {slot_utc}")
    return {"status": "queued", "slot_utc": slot_utc, "video_url": video_url, "idempotency_key": key}


def payload_for(s: Settings, row: dict) -> dict:
    meta = json.loads(row["meta"])
    privacy, _ = decide_privacy(s, "publish")
    return {"job_id": row["job_id"], "idempotency_key": row["idempotency_key"], "title": meta["title"],
            "description": meta["description"], "tags": ", ".join(meta["tags"]),
            "category_id": s.get("publishing.category_id", "27"), "privacy": privacy, "publish_at": "",
            "made_for_kids": False, "contains_synthetic_media": bool(meta.get("contains_synthetic_media")),
            "video_url": row["video_url"], "thumbnail_url": row["thumbnail_url"] or "",
            "pinned_comment": meta.get("pinned_comment", ""), "slot_utc": row["slot_utc"]}


def publish_due(s: Settings, db: DB, now: datetime | None = None, poster=None) -> list[dict]:
    """Send every queued video whose slot is now (or past, or within the early window) to Zapier."""
    if s.get("publishing.backend", "zapier") == "claude_zapier":
        return []        # uploads are done by the scheduled Claude task through its Zapier connection
    hook = env("ZAPIER_UPLOAD_HOOK")
    if not hook:
        raise PublishError("ZAPIER_UPLOAD_HOOK not set")
    now = now or datetime.now(timezone.utc)
    early = timedelta(minutes=int(s.get("publishing.slot_send_early_min", 25)))
    limit = (now + early).strftime("%Y-%m-%dT%H:%M:00Z")
    rows = db.query("SELECT * FROM publish_queue WHERE status='queued' AND slot_utc <= ? ORDER BY slot_utc", (limit,))
    results = []
    for row in rows:
        payload = payload_for(s, row)
        db.execute("UPDATE publish_queue SET status='sending' WHERE idempotency_key=? AND status='queued'",
                   (row["idempotency_key"],))
        try:
            r = (poster or http.request)("POST", hook, json=payload, timeout=60, retries=0)
        except Exception as e:     # unknown outcome: never resend automatically
            db.execute("UPDATE publish_queue SET status='unknown', error=? WHERE idempotency_key=?",
                       (str(e)[:300], row["idempotency_key"]))
            results.append({"job_id": row["job_id"], "status": "unknown", "error": str(e)[:200]})
            continue
        if r.status_code >= 400:
            db.execute("UPDATE publish_queue SET status='queued', error=? WHERE idempotency_key=?",
                       (f"HTTP {r.status_code}", row["idempotency_key"]))
            results.append({"job_id": row["job_id"], "status": "rejected", "http": r.status_code})
            continue
        db.execute("UPDATE publish_queue SET status='sent', sent_at=?, error=NULL WHERE idempotency_key=?",
                   (time.time(), row["idempotency_key"]))
        db.execute("INSERT OR REPLACE INTO uploads (idempotency_key, job_id, status, privacy, publish_at, created_at, "
                   "updated_at) VALUES (?,?,?,?,?,?,?)", (row["idempotency_key"], row["job_id"], "done",
                                                         payload["privacy"], row["slot_utc"], time.time(), time.time()))
        db.add_usage("zapier_task", 1)
        results.append({"job_id": row["job_id"], "status": "sent", "slot_utc": row["slot_utc"],
                        "privacy": payload["privacy"], "title": payload["title"]})
    return results
