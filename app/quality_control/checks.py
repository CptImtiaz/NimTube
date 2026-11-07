"""Pre-upload quality & policy gate. Any failure blocks publishing and produces an error report."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..config import Settings
from ..copyright.provenance import check_job_rights
from ..db import DB
from ..fact_checking.checker import check_script
from ..topic_selection.selector import fingerprint, similarity

POLICY_TERMS = ["kill yourself", "suicide method", "how to make a bomb", "nazi", "porn", "nude", "gore",
                "miracle cure", "guaranteed returns", "get rich quick"]


def _ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return {}
    return json.loads(out.stdout)


def _mean_volume(path: Path) -> float | None:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d\.]+) dB", p.stderr)
    return float(m.group(1)) if m else None


def _black_ratio(path: Path) -> float:
    """Share of the video that is (near) black — catches missing scenes."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "blackdetect=d=0.8:pix_th=0.06", "-an",
                        "-f", "null", "-"], capture_output=True, text=True)
    total = sum(float(x) for x in re.findall(r"black_duration:([\d\.]+)", p.stderr))
    dur = re.search(r"Duration: (\d+):(\d+):([\d\.]+)", p.stderr)
    if not dur:
        return 1.0
    secs = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))
    return total / secs if secs else 1.0


def validate_video_file(s: Settings, video: Path) -> list[str]:
    problems = []
    info = _ffprobe(video)
    if not info:
        return ["video file is not readable by ffprobe"]
    v = next((x for x in info["streams"] if x["codec_type"] == "video"), None)
    a = next((x for x in info["streams"] if x["codec_type"] == "audio"), None)
    if not v:
        problems.append("no video stream")
    else:
        if (v.get("width"), v.get("height")) != (s.get("video.width", 1080), s.get("video.height", 1920)):
            problems.append(f"resolution {v.get('width')}x{v.get('height')} is not 1080x1920")
        if v.get("codec_name") != "h264":
            problems.append(f"video codec {v.get('codec_name')} is not h264")
        if v.get("pix_fmt") != "yuv420p":
            problems.append(f"pixel format {v.get('pix_fmt')} is not yuv420p")
        num, den = (v.get("r_frame_rate", "0/1").split("/") + ["1"])[:2]
        fps = float(num) / float(den or 1)
        if abs(fps - s.get("video.fps", 30)) > 0.5:
            problems.append(f"frame rate {fps:.2f} != {s.get('video.fps', 30)}")
    if not a:
        problems.append("no audio stream")
    elif a.get("codec_name") != "aac":
        problems.append(f"audio codec {a.get('codec_name')} is not aac")
    dur = float(info.get("format", {}).get("duration", 0))
    lo, hi = s.get("production.min_duration_sec", 60), s.get("production.max_duration_sec", 180)
    if dur < lo * 0.9 or dur > hi:
        problems.append(f"duration {dur:.1f}s outside {lo}-{hi}s")
    mv = _mean_volume(video)
    if mv is None or mv < -40:
        problems.append(f"audio is silent or too quiet (mean {mv} dB)")
    br = _black_ratio(video)
    if br > 0.08:
        problems.append(f"{br:.0%} of the video is black (missing scenes?)")
    return problems


def run_quality_checks(s: Settings, db: DB, job_id: str, *, video: Path, ranking: dict, script: dict, voices: dict,
                       visuals: dict, metadata: dict, mode: str) -> dict:
    report: dict[str, list[str]] = {}
    report["video_file"] = validate_video_file(s, video)

    cap = []
    for key, v in voices.items():
        ws = v["words"]
        if not ws:
            cap.append(f"{key}: no caption words")
            continue
        if ws[-1]["end"] > v["duration"] + 0.3:
            cap.append(f"{key}: captions run past the narration ({ws[-1]['end']:.2f}s > {v['duration']:.2f}s)")
        if any(b["start"] < a["start"] for a, b in zip(ws, ws[1:])):
            cap.append(f"{key}: caption timings are not monotonic")
    report["captions"] = cap

    sc = []
    expected = {e["rank"] for e in ranking["entries"]}
    if set(visuals["segments"]) != expected:
        sc.append("a ranking entry has no visuals")
    for rank, items in visuals["segments"].items():
        for it in items:
            if not Path(it["path"]).exists() or Path(it["path"]).stat().st_size < 10_000:
                sc.append(f"#{rank}: visual missing or suspiciously small ({it['path']})")
    min_share = float(s.get("visuals.min_footage_share", 0.7))
    if mode in ("private", "publish") and not s.mock and visuals.get("footage_share", 0) < min_share:
        sc.append(f"only {visuals.get('footage_share', 0):.0%} of entries have real footage "
                  f"(minimum {min_share:.0%}) — add PEXELS_API_KEY / PIXABAY_API_KEY or adjust footage queries")
    if visuals.get("unique_assets", 0) < int(s.get("visuals.min_unique_assets", 12)):
        sc.append(f"only {visuals.get('unique_assets')} unique visual assets — too repetitive")
    report["scenes"] = sc

    ev = []
    for e in ranking["entries"]:
        if not e["evidence"]:
            ev.append(f"#{e['rank']} {e['name']}: no verified evidence")
    if len(ranking["domains"]) < int(s.get("research.min_independent_sources", 2)):
        ev.append("fewer than the required independent source domains")
    report["evidence"] = ev
    report["script"] = check_script(s, script, ranking)
    report["rights"] = check_job_rights(db, job_id, mode)

    meta = []
    title = metadata["title"]
    core = re.sub(r"(?i)\s*#shorts|\(our ranking\)", "", title).strip()
    if fingerprint(core) and similarity(fingerprint(core), fingerprint(ranking["title"])) < 0.99:
        meta.append("title does not match the ranking topic")
    if len(title) > 100:
        meta.append("title longer than 100 characters")
    if len(metadata["description"]) > 5000:
        meta.append("description longer than 5000 characters")
    if "ref: " not in metadata["description"]:
        meta.append("idempotency reference missing from description")
    report["metadata"] = meta

    pol = []
    blob = " ".join([title, metadata["description"], json.dumps(script)]).lower()
    for t in POLICY_TERMS:
        if t in blob:
            pol.append(f"policy-sensitive term present: '{t}'")
    for kw in s.get("topics.excluded_keywords", []) or []:
        if re.search(rf"\b{re.escape(kw.lower())}\b", title.lower()):
            pol.append(f"excluded topic keyword in title: '{kw}'")
    report["policy"] = pol

    dis = []
    from ..copyright.provenance import uses_synthetic_media
    if uses_synthetic_media(db, job_id) and not metadata.get("contains_synthetic_media"):
        dis.append("AI-generated realistic visuals used but synthetic-media disclosure not set")
    report["disclosure"] = dis

    rep = []
    recent = db.query("SELECT title FROM topics WHERE selected=1 AND job_id != ? ORDER BY created_at DESC LIMIT 30",
                      (job_id,))
    for r in recent:
        if similarity(fingerprint(r["title"]), fingerprint(ranking["title"])) >= 0.6:
            rep.append(f"near-duplicate of recent video '{r['title']}'")
    if visuals.get("unique_assets", 0) < 12:
        rep.append("visuals too repetitive for an original documentary")
    report["originality"] = rep

    failures = [f"[{k}] {p}" for k, v in report.items() for p in v]
    return {"passed": not failures, "failures": failures, "checks": report}
