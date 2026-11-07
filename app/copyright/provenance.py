"""Mandatory asset-provenance database and rights checks.

Every asset used in a video (image, video clip, graphic, voice, music) is registered with its source and
licence facts. Unknown sources are REJECTED. A video may not be uploaded unless every asset passes.
Note: recording a licence is not a guarantee against claims; it is evidence of good-faith due diligence.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

import yaml

from ..config import Settings, ROOT
from ..db import DB
from ..log import get_logger

logger = get_logger("copyright")
MUSIC_DIR = ROOT / "assets" / "music"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def register_asset(s: Settings, db: DB, job_id: str, *, kind: str, path: Path, source: str, creator: str = "",
                   proof: dict | None = None, restrictions: str = "", licence_override: dict | None = None) -> dict:
    """kind: image | video | graphic | voice | music. source: key in providers.yaml (media/voice) or 'music'."""
    table = s.providers.get("voice_sources" if kind == "voice" else "media_sources", {})
    lic = licence_override or table.get(source)
    status, reason = "approved", ""
    if not lic:
        status, reason = "rejected", f"unknown rights: source '{source}' is not an approved provider"
        lic = {}
    elif not lic.get("commercial_use", False):
        status, reason = "test_only", f"{source}: commercial use not permitted/unknown"
    if not Path(path).exists():
        status, reason = "rejected", "file missing"
    asset = {
        "asset_id": f"{kind}-{uuid.uuid4().hex[:12]}", "job_id": job_id, "kind": kind, "path": str(path),
        "sha256": sha256_file(Path(path)) if Path(path).exists() else "", "source": source, "creator": creator,
        "licence_name": lic.get("licence_name") or lic.get("licence") or source,
        "licence_url": lic.get("licence_url", ""), "downloaded_at": time.time(),
        "commercial_use": 1 if lic.get("commercial_use") else 0,
        "attribution": "required" if lic.get("attribution_required") else "not required",
        "modifications_allowed": 1 if lic.get("modifications_allowed", True) else 0,
        "proof": json.dumps(proof or {}, default=str)[:4000], "restrictions": restrictions or lic.get("notes", ""),
        "synthetic": 1 if lic.get("synthetic") else 0, "status": status, "reject_reason": reason,
    }
    db.execute(f"INSERT INTO assets ({', '.join(asset)}) VALUES ({', '.join('?' * len(asset))})", tuple(asset.values()))
    if status != "approved":
        logger.warning(f"asset {asset['asset_id']} {status}: {reason}")
    return asset


# ---------------- Music (checked separately) ----------------
def load_music_manifest() -> list[dict]:
    p = MUSIC_DIR / "manifest.yaml"
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data.get("tracks", []) if isinstance(data, dict) else []


def pick_music(s: Settings, mood: str) -> dict | None:
    allowed = set(s.providers.get("music_sources", {}).get("allowed_licences", []))
    tracks = []
    for t in load_music_manifest():
        f = MUSIC_DIR / str(t.get("file", ""))
        if not f.is_file() or f.resolve().parent != MUSIC_DIR.resolve():
            continue
        if t.get("licence") not in allowed or not t.get("proof"):
            logger.warning(f"music '{t.get('file')}' skipped: licence/proof missing or not allowed")
            continue
        tracks.append({**t, "path": f})
    if not tracks:
        return None
    matching = [t for t in tracks if mood in (t.get("moods") or [])]
    return (matching or tracks)[0] if len(matching or tracks) == 1 else sorted(
        matching or tracks, key=lambda t: hashlib.md5((t["file"] + time.strftime("%Y%m%d")).encode()).hexdigest())[0]


def register_music(s: Settings, db: DB, job_id: str, track: dict) -> dict:
    return register_asset(s, db, job_id, kind="music", path=track["path"], source="music",
                          creator=track.get("creator", ""), proof={"proof": track.get("proof"),
                                                                    "source_url": track.get("source_url")},
                          licence_override={"licence_name": track.get("licence"),
                                            "licence_url": track.get("licence_url", ""),
                                            "commercial_use": bool(track.get("commercial_use", True)),
                                            "attribution_required": bool(track.get("attribution")),
                                            "modifications_allowed": True, "synthetic": False,
                                            "notes": track.get("attribution", "")})


# ---------------- Job-level check ----------------
def check_job_rights(db: DB, job_id: str, mode: str, allow_test_only_private: bool = True) -> list[str]:
    assets = db.query("SELECT * FROM assets WHERE job_id=?", (job_id,))
    problems = []
    if not assets:
        return ["no assets registered for this job"]
    for a in assets:
        if a["status"] == "rejected":
            problems.append(f"{a['asset_id']} ({a['kind']}, {a['source']}): {a['reject_reason']}")
        elif a["status"] == "test_only":
            if mode == "publish" or (mode == "private" and not allow_test_only_private):
                problems.append(f"{a['asset_id']} ({a['kind']}, {a['source']}): {a['reject_reason']}")
        if not a["sha256"]:
            problems.append(f"{a['asset_id']}: no file hash recorded")
        if a["kind"] in ("image", "video") and a["source"] not in ("original_graphics",) and not a["proof"].strip("{}"):
            problems.append(f"{a['asset_id']}: no proof of licence recorded")
    return problems


def attribution_lines(db: DB, job_id: str) -> list[str]:
    lines = []
    for a in db.query("SELECT DISTINCT source, creator, licence_name, licence_url, attribution, kind FROM assets "
                      "WHERE job_id=? AND status!='rejected'", (job_id,)):
        if a["source"] in ("original_graphics", "original_sfx"):
            continue
        who = f" by {a['creator']}" if a["creator"] else ""
        lines.append(f"{a['kind'].title()}{who} — {a['licence_name']} {a['licence_url']}".strip())
    return sorted(set(lines))


def uses_synthetic_media(db: DB, job_id: str) -> bool:
    r = db.one("SELECT COUNT(*) c FROM assets WHERE job_id=? AND synthetic=1 AND kind IN ('image','video')", (job_id,))
    return bool(r and r["c"])
