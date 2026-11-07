import json
import shutil
import subprocess

import pytest

from app.pipeline import PipelineStop, run


def test_dry_run_stops_before_media(settings):
    settings.mode = "dry-run"
    res = run(settings)
    assert res["status"] == "dry-run-complete"
    assert not list(settings.output_dir.glob("*/render"))


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("espeak-ng")), reason="needs ffmpeg + espeak-ng")
def test_full_test_mode_produces_valid_video_and_resume_is_idempotent(settings):
    res = run(settings)
    assert res["status"].startswith("rendered")
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", res["video"]],
                                     capture_output=True, text=True).stdout)
    v = [x for x in info["streams"] if x["codec_type"] == "video"][0]
    assert (v["width"], v["height"]) == (1080, 1920)
    again = run(settings, resume_job=res["job_id"])          # resume: nothing re-done, same video
    assert again["video"] == res["video"]


def test_quality_gate_blocks_upload_when_rights_fail(settings, monkeypatch):
    settings.mode = "private"
    # edge (non-commercial) voice + private allowed, but an unknown asset source must stop the run
    from app.copyright import provenance
    real = provenance.register_asset

    def tamper(s, db, job_id, **kw):
        if kw.get("kind") == "graphic" and "title" in str(kw.get("proof")):
            kw["source"] = "some_website"
        return real(s, db, job_id, **kw)
    monkeypatch.setattr("app.visual_generation.planner.register_asset", tamper)
    if not (shutil.which("ffmpeg") and shutil.which("espeak-ng")):
        pytest.skip("needs ffmpeg + espeak-ng")
    with pytest.raises(PipelineStop, match="rights"):
        run(settings)


def test_daily_target_prevents_second_upload(settings):
    import time
    from app.db import DB
    db = DB(settings.db_path)
    db.execute("INSERT INTO uploads (idempotency_key, job_id, status, youtube_id, created_at) VALUES (?,?,?,?,?)",
               ("k", "old", "done", "y", time.time()))
    settings.mode = "private"
    with pytest.raises(PipelineStop, match="daily target"):
        run(settings)
