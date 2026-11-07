"""Official YouTube Data API v3 publisher with idempotency.

Duplicate protection (three layers):
 1. uploads table keyed by idempotency_key = sha256(job_id + video hash); a 'done' row is never re-uploaded.
 2. an interrupted upload ('uploading' row) triggers a lookup of the channel's recent uploads for the
    'ref: <key>' marker in the description before any new upload is attempted.
 3. the GitHub workflow uses a concurrency group so two runs never upload at the same time.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import Settings, env
from ..copyright.provenance import sha256_file
from ..db import DB
from ..log import get_logger

logger = get_logger("youtube")

# Least privilege: upload + read-only (needed for duplicate lookup and status) + analytics read-only.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]
CAPTION_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"   # only if captions/pinned comment enabled


class PublishError(RuntimeError):
    pass


def idempotency_key(job_id: str, video: Path) -> str:
    return "gt10-" + hashlib.sha256(f"{job_id}:{sha256_file(video)}".encode()).hexdigest()[:16]


def credentials(scopes: list[str] | None = None):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    cid, secret, refresh = env("YT_CLIENT_ID"), env("YT_CLIENT_SECRET"), env("YT_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        raise PublishError("YouTube OAuth not configured: set YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN "
                           "(run scripts/get_youtube_token.py once)")
    creds = Credentials(None, refresh_token=refresh, client_id=cid, client_secret=secret,
                        token_uri="https://oauth2.googleapis.com/token", scopes=scopes or SCOPES)
    creds.refresh(Request())
    return creds


def client():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=credentials(), cache_discovery=False)


def find_existing_upload(yt, key: str) -> str | None:
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    items = ch.get("items") or []
    if not items:
        return None
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=25).execute()
    ids = [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
    if not ids:
        return None
    vids = yt.videos().list(part="snippet", id=",".join(ids)).execute()
    for v in vids.get("items", []):
        if f"ref: {key}" in v["snippet"].get("description", ""):
            return v["id"]
    return None


def decide_privacy(s: Settings, mode: str) -> tuple[str, str | None]:
    if mode != "publish" or not s.get("publishing.auto_publish", False):
        if mode == "publish":
            logger.warning("mode=publish but publishing.auto_publish is false -> uploading as PRIVATE")
        return "private", None
    vis = s.get("publishing.visibility", "private")
    if vis not in ("private", "unlisted", "public"):
        raise PublishError(f"invalid visibility {vis}")
    at = s.get("publishing.schedule_publish_at")
    if at and vis == "public":
        tz = ZoneInfo(s.get("publishing.channel_timezone", "UTC"))
        hh, mm = map(int, str(at).split(":"))
        now = datetime.now(tz)
        when = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if when <= now + timedelta(minutes=15):
            when += timedelta(days=1)
        return "private", when.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return vis, None


def youtube_configured() -> bool:
    return all(env(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))


def upload(s: Settings, db: DB, job_id: str, mode: str, video: Path, meta: dict, srt: Path | None = None,
           yt=None, schedule_at: str | None = None) -> dict:
    """schedule_at: ISO UTC time -> upload now as private with YouTube's own publishAt (native scheduling).
    Only honoured when publishing.auto_publish is true; otherwise the video stays private (safe default)."""
    key = idempotency_key(job_id, video)
    if key not in meta["description"]:
        raise PublishError("description must contain the idempotency reference before upload")
    row = db.one("SELECT * FROM uploads WHERE idempotency_key=?", (key,))
    if row and row["status"] == "done":
        logger.info(f"already uploaded ({row['youtube_id']}), skipping")
        return {"youtube_id": row["youtube_id"], "status": "already_uploaded", "privacy": row["privacy"]}

    yt = yt or client()
    if row and row["status"] == "uploading":
        existing = find_existing_upload(yt, key)
        if existing:
            db.execute("UPDATE uploads SET status='done', youtube_id=?, updated_at=? WHERE idempotency_key=?",
                       (existing, time.time(), key))
            return {"youtube_id": existing, "status": "recovered_existing", "privacy": row["privacy"]}

    privacy, publish_at = decide_privacy(s, mode)
    if schedule_at:
        if s.get("publishing.auto_publish", False):
            privacy, publish_at = "private", schedule_at.replace(":00Z", ":00.000Z")
        else:
            logger.warning("auto_publish is false -> uploading PRIVATE without a publish time")
            privacy, publish_at = "private", None
    now = time.time()
    db.execute("INSERT INTO uploads (idempotency_key, job_id, video_sha256, status, privacy, publish_at, created_at, "
               "updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO UPDATE SET status='uploading', "
               "updated_at=excluded.updated_at", (key, job_id, sha256_file(video), "uploading", privacy, publish_at,
                                                  now, now))
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {"title": meta["title"], "description": meta["description"], "tags": meta["tags"],
                    "categoryId": s.get("publishing.category_id", "27"), "defaultLanguage": "en",
                    "defaultAudioLanguage": "en"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": bool(meta.get("contains_synthetic_media"))},
    }
    if publish_at:
        body["status"]["publishAt"] = publish_at
    try:
        req = yt.videos().insert(part="snippet,status", body=body, notifySubscribers=privacy == "public",
                                 media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True,
                                                            chunksize=8 * 1024 * 1024))
        resp = None
        while resp is None:
            status, resp = req.next_chunk(num_retries=5)
            if status:
                logger.info(f"upload {int(status.progress() * 100)}%")
    except Exception as e:
        db.execute("UPDATE uploads SET error=?, updated_at=? WHERE idempotency_key=?", (str(e)[:500], time.time(), key))
        raise PublishError(f"upload failed (state kept as 'uploading' for safe retry): {e}") from e

    vid = resp["id"]
    db.execute("UPDATE uploads SET status='done', youtube_id=?, updated_at=?, error=NULL WHERE idempotency_key=?",
               (vid, time.time(), key))
    db.add_usage("youtube_upload", 1)
    result = {"youtube_id": vid, "url": f"https://youtube.com/shorts/{vid}", "status": "uploaded", "privacy": privacy,
              "publish_at": publish_at, "idempotency_key": key, "extras": {}}

    if s.get("publishing.upload_thumbnail", True) and meta.get("thumbnail"):
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(meta["thumbnail"], mimetype="image/jpeg")).execute()
            result["extras"]["thumbnail"] = "ok"
        except Exception as e:   # unverified channels cannot set custom thumbnails
            result["extras"]["thumbnail"] = f"skipped: {str(e)[:150]}"
    if s.get("publishing.upload_captions", False) and srt and Path(srt).exists():
        try:
            yt.captions().insert(part="snippet", body={"snippet": {"videoId": vid, "language": "en", "name": "English"}},
                                 media_body=MediaFileUpload(str(srt), mimetype="application/octet-stream")).execute()
            result["extras"]["captions"] = "ok"
        except Exception as e:
            result["extras"]["captions"] = f"skipped (needs youtube.force-ssl scope?): {str(e)[:150]}"
    try:
        st = yt.videos().list(part="status,processingDetails", id=vid).execute()["items"][0]
        result["upload_status"] = st["status"].get("uploadStatus")
        result["processing"] = (st.get("processingDetails") or {}).get("processingStatus")
    except Exception:
        pass
    return result
