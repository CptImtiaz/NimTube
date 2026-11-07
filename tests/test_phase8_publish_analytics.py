import json
import time
from pathlib import Path

import pytest

from app.analytics import collector
from app.youtube_upload import publisher


class _Req:
    def __init__(self, resp):
        self.resp = resp

    def next_chunk(self, num_retries=0):
        return None, self.resp

    def execute(self):
        return self.resp


class FakeYouTube:
    """Minimal stand-in for googleapiclient's YouTube resource (unit tests only)."""
    def __init__(self, existing_desc=None):
        self.inserts = []
        self.existing_desc = existing_desc

    def videos(self):
        yt = self

        class V:
            def insert(self, part, body, media_body, notifySubscribers):
                yt.inserts.append(body)
                return _Req({"id": f"vid{len(yt.inserts)}"})

            def list(self, part, id):
                if "snippet" in part:
                    return _Req({"items": [{"id": "old123", "snippet": {"description": yt.existing_desc or ""}}]})
                return _Req({"items": [{"status": {"uploadStatus": "uploaded"}, "processingDetails": {}}]})
        return V()

    def channels(self):
        class C:
            def list(self, part, mine):
                return _Req({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
        return C()

    def playlistItems(self):
        class P:
            def list(self, part, playlistId, maxResults):
                return _Req({"items": [{"contentDetails": {"videoId": "old123"}}]})
        return P()

    def thumbnails(self):
        class T:
            def set(self, videoId, media_body):
                return _Req({})
        return T()


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "final.mp4"
    p.write_bytes(b"\x00" * 4096)
    return p


def _meta(key):
    return {"title": "Top 10 X #shorts", "description": f"desc\nref: {key}", "tags": ["a"], "thumbnail": None,
            "contains_synthetic_media": True}


def test_private_by_default_and_never_twice(settings, db, video):
    key = publisher.idempotency_key("job1", video)
    yt = FakeYouTube()
    r1 = publisher.upload(settings, db, "job1", "publish", video, _meta(key), yt=yt)
    assert r1["privacy"] == "private"                         # auto_publish false -> private
    assert yt.inserts[0]["status"]["containsSyntheticMedia"] is True
    assert yt.inserts[0]["status"]["selfDeclaredMadeForKids"] is False
    r2 = publisher.upload(settings, db, "job1", "publish", video, _meta(key), yt=yt)
    assert r2["status"] == "already_uploaded" and len(yt.inserts) == 1


def test_interrupted_upload_is_recovered_not_reuploaded(settings, db, video):
    key = publisher.idempotency_key("job2", video)
    db.execute("INSERT INTO uploads (idempotency_key, job_id, status, created_at, updated_at) VALUES (?,?,?,?,?)",
               (key, "job2", "uploading", time.time(), time.time()))
    yt = FakeYouTube(existing_desc=f"... ref: {key}")
    r = publisher.upload(settings, db, "job2", "private", video, _meta(key), yt=yt)
    assert r["status"] == "recovered_existing" and r["youtube_id"] == "old123" and not yt.inserts


def test_public_and_scheduled_require_auto_publish(settings):
    assert publisher.decide_privacy(settings, "private") == ("private", None)
    settings.raw["publishing"].update(auto_publish=True, visibility="public", schedule_publish_at=None)
    assert publisher.decide_privacy(settings, "publish") == ("public", None)
    settings.raw["publishing"]["schedule_publish_at"] = "18:00"
    priv, at = publisher.decide_privacy(settings, "publish")
    assert priv == "private" and at.endswith("Z")              # publishAt requires private until release


def test_upload_refuses_without_reference(settings, db, video):
    with pytest.raises(publisher.PublishError):
        publisher.upload(settings, db, "j", "private", video, {"title": "t", "description": "no ref", "tags": []},
                         yt=FakeYouTube())


def test_learnings_need_data_and_produce_bounded_bonus(db):
    assert collector.learnings(db)["niche_bonus"] == {}
    for i, (niche, views) in enumerate([("science", 9000), ("science", 11000), ("history", 1000), ("history", 2000)]):
        db.save_job(f"j{i}", data={"topic": {"niche": niche}, "render": {"duration": 90}, "script": {"hook": f"h{i}"}})
        db.execute("INSERT INTO uploads (idempotency_key, job_id, status, youtube_id, created_at) VALUES (?,?,?,?,?)",
                   (f"k{i}", f"j{i}", "done", f"y{i}", 0))
        db.execute("INSERT INTO analytics VALUES (?,?,?,?)",
                   (f"y{i}", "7d", 0, json.dumps({"views": views, "averageViewPercentage": 70})))
    L = collector.learnings(db)
    assert L["niche_bonus"]["science"] > 0 > L["niche_bonus"]["history"]
    assert all(-5 <= b <= 5 for b in L["niche_bonus"].values())
