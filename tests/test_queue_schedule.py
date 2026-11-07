import json
import shutil
from datetime import datetime, timezone

import pytest

from app.youtube_upload import queue
from app.youtube_upload.publisher import idempotency_key


class Resp:
    def __init__(self, status=200):
        self.status_code, self.text = status, ""


def test_upcoming_slots_after_nightly_run(settings):
    settings.raw["production"]["videos_per_day"] = 3
    run_at = datetime(2026, 11, 8, 14, 0, tzinfo=timezone.utc)          # 22:00 Malaysia
    assert queue.upcoming_slots(settings, run_at) == [
        "2026-11-08T17:30:00Z", "2026-11-08T23:30:00Z", "2026-11-09T11:30:00Z"]


def test_slots_need_lead_time(settings):
    settings.raw["production"]["videos_per_day"] = 3
    late = datetime(2026, 11, 8, 17, 0, tzinfo=timezone.utc)            # too close to 17:30
    assert queue.upcoming_slots(settings, late)[0] == "2026-11-08T23:30:00Z"


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "final.mp4"
    p.write_bytes(b"\x02" * 3000)
    return p


def _meta(key):
    return {"title": "Top 10 X #shorts", "description": f"d\nref: {key}", "tags": ["x"], "thumbnail": None}


def test_single_daily_video_uses_priority_slot(settings):
    settings.raw["production"]["videos_per_day"] = 1
    assert queue.upcoming_slots(settings, datetime(2026, 11, 8, 14, 0, tzinfo=timezone.utc)) == ["2026-11-08T23:30:00Z"]


def test_enqueue_then_publish_only_when_due_and_once(settings, db, video, monkeypatch):
    settings.raw["production"]["videos_per_day"] = 3
    monkeypatch.setenv("ZAPIER_UPLOAD_HOOK", "https://hooks.zapier.com/hooks/catch/1/x/")
    settings.raw["publishing"]["backend"] = "zapier"
    key = idempotency_key("j1", video)
    hoster = lambda p, name, ct: f"https://github.com/CptImtiaz/NimTube/releases/download/queue-1/{name}"
    r = queue.enqueue(settings, db, "j1", video, _meta(key), "2026-11-08T17:30:00Z", hoster=hoster)
    assert r["status"] == "queued" and r["video_url"].endswith("/j1.mp4")
    assert queue.enqueue(settings, db, "j1", video, _meta(key), "2026-11-08T17:30:00Z", hoster=hoster)["status"] \
        == "already_queued"
    assert queue.open_slots(settings, db, datetime(2026, 11, 8, 14, 0, tzinfo=timezone.utc)) == [
        "2026-11-08T23:30:00Z", "2026-11-09T11:30:00Z"]                        # only tonight's 3 slots
    sent = []
    poster = lambda m, u, **kw: (sent.append(kw["json"]), Resp())[1]
    assert queue.publish_due(settings, db, datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc), poster) == []
    out = queue.publish_due(settings, db, datetime(2026, 11, 8, 17, 10, tzinfo=timezone.utc), poster)
    assert out[0]["status"] == "sent" and sent[0]["privacy"] == "private" and sent[0]["publish_at"] == ""
    assert queue.publish_due(settings, db, datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc), poster) == []
    assert len(sent) == 1


def test_public_when_auto_publish(settings, db, video, monkeypatch):
    monkeypatch.setenv("ZAPIER_UPLOAD_HOOK", "https://hooks.zapier.com/hooks/catch/1/x/")
    settings.raw["publishing"]["backend"] = "zapier"
    settings.raw["publishing"].update(auto_publish=True, visibility="public", schedule_publish_at=None)
    key = idempotency_key("j2", video)
    queue.enqueue(settings, db, "j2", video, _meta(key), "2026-11-08T17:30:00Z", hoster=lambda *a: "https://x/v.mp4")
    sent = []
    queue.publish_due(settings, db, datetime(2026, 11, 8, 17, 30, tzinfo=timezone.utc),
                      lambda m, u, **kw: (sent.append(kw["json"]), Resp())[1])
    assert sent[0]["privacy"] == "public"


def test_unknown_send_is_never_retried_and_rejection_is(settings, db, video, monkeypatch):
    monkeypatch.setenv("ZAPIER_UPLOAD_HOOK", "https://hooks.zapier.com/hooks/catch/1/x/")
    settings.raw["publishing"]["backend"] = "zapier"
    for j in ("a", "b"):
        queue.enqueue(settings, db, j, video, _meta(idempotency_key(j, video)), "2026-11-08T17:30:00Z" if j == "a"
                      else "2026-11-08T23:30:00Z", hoster=lambda *a: "https://x/v.mp4")

    def boom(*a, **k):
        raise TimeoutError("timeout")
    t = datetime(2026, 11, 8, 17, 30, tzinfo=timezone.utc)
    assert queue.publish_due(settings, db, t, boom)[0]["status"] == "unknown"
    assert queue.publish_due(settings, db, t, lambda *a, **k: Resp()) == []          # 'a' not resent
    t2 = datetime(2026, 11, 8, 23, 30, tzinfo=timezone.utc)
    assert queue.publish_due(settings, db, t2, lambda *a, **k: Resp(500))[0]["status"] == "rejected"
    assert queue.publish_due(settings, db, t2, lambda *a, **k: Resp())[0]["status"] == "sent"   # retried OK


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("espeak-ng")), reason="needs ffmpeg + espeak-ng")
def test_nightly_queues_one_video_per_slot(settings, monkeypatch, capsys):
    monkeypatch.setattr(queue, "host", lambda s, p, name, ct: f"https://example.org/{name}")
    monkeypatch.setattr(queue, "upcoming_slots", lambda s, now=None, count=None, min_lead_min=75:
                        ["2026-11-08T17:30:00Z", "2026-11-08T23:30:00Z"])
    from app.cli import main
    from app.config import load_settings
    from app.db import DB
    assert main(["nightly", "--mock"]) == 0
    rows = DB(load_settings("queue", mock=True).db_path).query("SELECT slot_utc, status FROM publish_queue")
    assert rows and all(r["status"] == "queued" for r in rows)
    assert {r["slot_utc"] for r in rows} <= {"2026-11-08T17:30:00Z", "2026-11-08T23:30:00Z"}


def test_upload_uses_native_youtube_schedule(settings, db, tmp_path):
    from app.youtube_upload import publisher as pub
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 1000)
    key = pub.idempotency_key("job1", video)
    meta = {"title": "T", "description": f"d ref: {key}", "tags": []}
    sent = {}

    class Req:
        def next_chunk(self, num_retries=0):
            return None, {"id": "vid123"}

    class Videos:
        def insert(self, part, body, notifySubscribers, media_body):
            sent["body"] = body
            return Req()

    class YT:
        def videos(self):
            return Videos()
    settings.raw["publishing"]["auto_publish"] = True
    settings.raw["publishing"]["upload_thumbnail"] = False
    res = pub.upload(settings, db, "job1", "publish", video, meta, yt=YT(), schedule_at="2026-11-09T23:30:00Z")
    assert sent["body"]["status"]["privacyStatus"] == "private"
    assert sent["body"]["status"]["publishAt"] == "2026-11-09T23:30:00.000Z"
    assert res["publish_at"] == "2026-11-09T23:30:00.000Z"
    # safety: without auto_publish the video stays private and unscheduled
    settings.raw["publishing"]["auto_publish"] = False
    video2 = tmp_path / "v2.mp4"
    video2.write_bytes(b"y" * 1000)
    key2 = pub.idempotency_key("job2", video2)
    pub.upload(settings, db, "job2", "publish", video2, {"title": "T", "description": f"ref: {key2}", "tags": []},
               yt=YT(), schedule_at="2026-11-09T23:30:00Z")
    assert "publishAt" not in sent["body"]["status"]


def test_claude_zapier_mode_hosts_manifest_and_skips_webhook(settings, db, video):
    import json as _json
    settings.raw["publishing"]["backend"] = "claude_zapier"
    hosted = {}

    def hoster(path, name, ct):
        hosted[name] = path.read_text() if name.endswith(".json") else "video"
        return f"https://example.com/{name}"
    key = queue.idempotency_key("jobm", video)
    queue.enqueue(settings, db, "jobm", video, _meta(key), "2026-11-08T23:30:00Z", hoster=hoster)
    m = _json.loads(hosted["jobm.json"])
    assert m["video_url"] == "https://example.com/jobm.mp4" and m["slot_utc"] == "2026-11-08T23:30:00Z"
    assert m["idempotency_key"] == key and m["privacy"] == "private"
    assert queue.publish_due(settings, db, datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)) == []
