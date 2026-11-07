"""Publish through Zapier: the video is uploaded to private S3-compatible storage (Railway bucket), a
time-limited presigned link is created, and a JSON payload is POSTed to your Zapier "Catch Hook".
Your Zap then runs "YouTube: Upload Video" with those fields.

Idempotency: a job is sent to Zapier at most once. If a previous send ended in an unknown state (network
error after sending), the bot refuses to resend automatically — check your channel, then set
ZAPIER_FORCE_RESEND=1 for one run if the video really is missing.
"""
from __future__ import annotations

import time
from pathlib import Path

from .. import http
from ..config import Settings, env
from ..copyright.provenance import sha256_file
from ..db import DB
from ..log import get_logger
from .publisher import PublishError, decide_privacy, idempotency_key

logger = get_logger("zapier")


def _s3():
    import boto3
    from botocore.config import Config

    need = ["S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]
    missing = [k for k in need if not env(k)]
    if missing:
        raise PublishError(f"storage not configured: missing {', '.join(missing)}")
    return boto3.client("s3", endpoint_url=env("S3_ENDPOINT"), region_name=env("S3_REGION", "auto") or "auto",
                        aws_access_key_id=env("S3_ACCESS_KEY_ID"), aws_secret_access_key=env("S3_SECRET_ACCESS_KEY"),
                        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"})), env("S3_BUCKET")


def store_and_sign(path: Path, key: str, hours: int, content_type: str) -> str:
    client, bucket = _s3()
    client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type})
    return client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                         ExpiresIn=int(hours * 3600))


def upload_via_zapier(s: Settings, db: DB, job_id: str, mode: str, video: Path, meta: dict, signer=store_and_sign,
                      poster=None) -> dict:
    hook = env("ZAPIER_UPLOAD_HOOK")
    if not hook:
        raise PublishError("ZAPIER_UPLOAD_HOOK not set (your Zap's 'Catch Hook' URL)")
    key = idempotency_key(job_id, video)
    if key not in meta["description"]:
        raise PublishError("description must contain the idempotency reference before upload")
    row = db.one("SELECT * FROM uploads WHERE idempotency_key=?", (key,))
    if row and row["status"] == "done":
        return {"status": "already_sent", "privacy": row["privacy"], "idempotency_key": key}
    if row and row["status"] == "uploading" and env("ZAPIER_FORCE_RESEND") != "1":
        raise PublishError("a previous send to Zapier ended in an unknown state — check your channel; set "
                           "ZAPIER_FORCE_RESEND=1 for one run if the video is missing")

    privacy, publish_at = decide_privacy(s, mode)
    hours = int(s.get("publishing.link_valid_hours", 24))
    video_url = signer(video, f"videos/{job_id}/{video.name}", hours, "video/mp4")
    thumb_url = ""
    if meta.get("thumbnail") and Path(meta["thumbnail"]).exists():
        thumb_url = signer(Path(meta["thumbnail"]), f"videos/{job_id}/thumbnail.jpg", hours, "image/jpeg")

    payload = {
        "job_id": job_id, "idempotency_key": key, "title": meta["title"], "description": meta["description"],
        "tags": ", ".join(meta["tags"]), "category_id": s.get("publishing.category_id", "27"),
        "privacy": privacy, "publish_at": publish_at or "", "made_for_kids": False,
        "contains_synthetic_media": bool(meta.get("contains_synthetic_media")),
        "video_url": video_url, "thumbnail_url": thumb_url, "pinned_comment": meta.get("pinned_comment", ""),
    }
    now = time.time()
    db.execute("INSERT INTO uploads (idempotency_key, job_id, video_sha256, status, privacy, publish_at, created_at, "
               "updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO UPDATE SET status='uploading', "
               "updated_at=excluded.updated_at",
               (key, job_id, sha256_file(video), "uploading", privacy, publish_at, now, now))
    try:
        r = (poster or http.request)("POST", hook, json=payload, timeout=60, retries=0)
    except Exception as e:
        raise PublishError(f"could not reach Zapier (state kept as 'uploading'): {e}") from e
    if r.status_code >= 400:
        db.execute("DELETE FROM uploads WHERE idempotency_key=?", (key,))     # definitely not accepted: safe to retry
        raise PublishError(f"Zapier rejected the payload: HTTP {r.status_code} {r.text[:200]}")
    db.execute("UPDATE uploads SET status='done', updated_at=?, error=NULL WHERE idempotency_key=?", (time.time(), key))
    db.add_usage("zapier_task", 1)
    logger.info("video handed to Zapier for YouTube upload")
    return {"status": "sent_to_zapier", "privacy": privacy, "publish_at": publish_at, "idempotency_key": key,
            "video_url_expires_hours": hours}
