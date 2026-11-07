import json
from pathlib import Path

import pytest

from app import llm as llm_mod
from app.youtube_upload import zapier
from app.youtube_upload.publisher import PublishError, idempotency_key


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self._d


def test_claude_research_follows_pause_turn_and_collects_citations(settings, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0000000000000000")
    calls = []
    replies = [
        {"stop_reason": "pause_turn", "usage": {"input_tokens": 10, "output_tokens": 5},
         "content": [{"type": "server_tool_use", "name": "web_search", "input": {"query": "largest moons"}},
                     {"type": "web_search_tool_result", "content": [{"type": "web_search_result",
                                                                      "url": "https://science.nasa.gov/a", "title": "A"}]}]},
        {"stop_reason": "end_turn", "usage": {"input_tokens": 20, "output_tokens": 30,
                                              "server_tool_use": {"web_search_requests": 2}},
         "content": [{"type": "text", "text": '{"entries": [], "sort": "desc"}',
                      "citations": [{"type": "web_search_result_location", "url": "https://www.britannica.com/b",
                                     "title": "B", "cited_text": "x"}]}]},
    ]

    def fake_request(method, url, **kw):
        calls.append(kw["json"])
        assert kw["headers"]["anthropic-version"] == "2023-06-01"
        return Resp(replies[len(calls) - 1])
    monkeypatch.setattr(llm_mod.http, "request", fake_request)
    settings.mock = False
    L = llm_mod.LLM(settings)
    assert L.providers[0].name == "anthropic"
    data, urls = L.research_json("research", "sys", "prompt")
    assert data == {"entries": [], "sort": "desc"}
    assert {u["url"] for u in urls} == {"https://science.nasa.gov/a", "https://www.britannica.com/b"}
    assert calls[0]["tools"][0]["type"] == "web_search_20250305"
    assert len(calls) == 2 and calls[1]["messages"][-1]["role"] == "assistant"   # paused turn was continued


def test_claude_complete_json(settings, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0000000000000000")
    monkeypatch.setattr(llm_mod.http, "request", lambda m, u, **k: Resp(
        {"stop_reason": "end_turn", "usage": {}, "content": [{"type": "text", "text": 'Here:\n{"a": 1}'}]}))
    settings.mock = False
    assert llm_mod.LLM(settings).complete_json("t", "s", "p") == {"a": 1}


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "final.mp4"
    p.write_bytes(b"\x01" * 5000)
    return p


def _meta(key):
    return {"title": "Top 10 X #shorts", "description": f"d\nref: {key}", "tags": ["a", "b"], "thumbnail": None,
            "contains_synthetic_media": False}


def test_zapier_payload_privacy_and_send_once(settings, db, video, monkeypatch):
    monkeypatch.setenv("ZAPIER_UPLOAD_HOOK", "https://hooks.zapier.com/hooks/catch/1/abc/")
    sent = []
    key = idempotency_key("j1", video)
    signer = lambda path, k, hours, ct: f"https://bucket.example/{k}?sig=1"
    poster = lambda m, u, **kw: (sent.append(kw["json"]), Resp({"status": "success"}))[1]
    r = zapier.upload_via_zapier(settings, db, "j1", "publish", video, _meta(key), signer=signer, poster=poster)
    assert r["status"] == "sent_to_zapier" and sent[0]["privacy"] == "private"     # auto_publish off -> private
    assert sent[0]["video_url"].startswith("https://bucket.example/videos/j1/") and sent[0]["tags"] == "a, b"
    r2 = zapier.upload_via_zapier(settings, db, "j1", "publish", video, _meta(key), signer=signer, poster=poster)
    assert r2["status"] == "already_sent" and len(sent) == 1


def test_zapier_unknown_state_is_not_resent(settings, db, video, monkeypatch):
    monkeypatch.setenv("ZAPIER_UPLOAD_HOOK", "https://hooks.zapier.com/hooks/catch/1/abc/")
    key = idempotency_key("j2", video)

    def boom(*a, **k):
        raise TimeoutError("timed out after sending")
    with pytest.raises(PublishError, match="could not reach Zapier"):
        zapier.upload_via_zapier(settings, db, "j2", "private", video, _meta(key),
                                 signer=lambda *a: "https://x/y", poster=boom)
    with pytest.raises(PublishError, match="unknown state"):
        zapier.upload_via_zapier(settings, db, "j2", "private", video, _meta(key),
                                 signer=lambda *a: "https://x/y", poster=lambda *a, **k: Resp({}))


def test_zapier_rejection_allows_retry(settings, db, video, monkeypatch):
    monkeypatch.setenv("ZAPIER_UPLOAD_HOOK", "https://hooks.zapier.com/hooks/catch/1/abc/")
    key = idempotency_key("j3", video)
    with pytest.raises(PublishError, match="rejected"):
        zapier.upload_via_zapier(settings, db, "j3", "private", video, _meta(key), signer=lambda *a: "https://x",
                                 poster=lambda *a, **k: Resp({}, 410))
    r = zapier.upload_via_zapier(settings, db, "j3", "private", video, _meta(key), signer=lambda *a: "https://x",
                                 poster=lambda *a, **k: Resp({}))
    assert r["status"] == "sent_to_zapier"


def test_presigned_url_is_generated_offline(monkeypatch):
    for k, v in {"S3_ENDPOINT": "https://t3.storageapi.dev", "S3_BUCKET": "gt10-videos-abc", "S3_REGION": "auto",
                 "S3_ACCESS_KEY_ID": "tid_test", "S3_SECRET_ACCESS_KEY": "tsec_test_value_123"}.items():
        monkeypatch.setenv(k, v)
    client, bucket = zapier._s3()
    url = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": "videos/j/final.mp4"},
                                        ExpiresIn=3600)
    assert url.startswith("https://gt10-videos-abc.t3.storageapi.dev/videos/j/final.mp4") and "X-Amz-Signature" in url


def test_gemini_auto_picks_newest_stable_flash():
    from app.llm import GeminiProvider
    names = ["models/gemini-2.5-flash", "models/gemini-3.5-flash-lite", "models/gemini-3.6-flash",
             "models/gemini-3.8-flash", "models/gemini-3.8-flash-image", "models/gemini-3.8-pro",
             "models/text-embedding-005"]
    assert GeminiProvider.pick_model(names) == "gemini-3.8-flash"
    assert GeminiProvider.pick_model(names, "gemini-3.6-flash") == "gemini-3.6-flash"
    assert GeminiProvider.pick_model(["models/gemini-4-flash-preview-x"]) == "gemini-4-flash-preview-x"


def test_gemini_discovers_model_and_skips_thought_parts(settings, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "x" * 35)
    calls = []

    def fake(method, url, **kw):
        calls.append(url)
        if url.endswith("/models"):
            return Resp({"models": [{"name": "models/gemini-3.6-flash", "supportedGenerationMethods": ["generateContent"]},
                                    {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]}]})
        return Resp({"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True},
                                                          {"text": '{"ok": true}'}]}}], "usageMetadata": {}})
    monkeypatch.setattr(llm_mod.http, "request", fake)
    settings.mock = False
    settings.raw["ai"]["gemini_min_seconds_between_calls"] = 0
    L = llm_mod.LLM(settings)
    assert L.providers[0].name == "gemini"
    assert L.complete_json("t", "s", "p") == {"ok": True}
    assert "gemini-3.8-flash:generateContent" in calls[-1]


def test_gemini_research_model_with_fallback(settings, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "y" * 35)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("GEMINI_RESEARCH_MODEL", "gemini-2.5-flash")
    calls = []

    def fake(method, url, **kw):
        calls.append(url)
        if "gemini-2.5-flash" in url:
            r = Resp({"error": {"code": 404, "message": "models/gemini-2.5-flash is not found"}}, 404)
            r.text = "models/gemini-2.5-flash is not found for API version v1beta"
            return r
        return Resp({"candidates": [{"content": {"parts": [{"text": '{"entries": []}'}]},
                                     "groundingMetadata": {}}], "usageMetadata": {}})
    monkeypatch.setattr(llm_mod.http, "request", fake)
    settings.mock = False
    settings.raw["ai"]["gemini_min_seconds_between_calls"] = 0
    data, _ = llm_mod.LLM(settings).research_json("research", "s", "p")
    assert data == {"entries": []}
    assert "gemini-2.5-flash" in calls[0] and "gemini-3.8-flash" in calls[1]


def test_gemini_quota_error_is_explained():
    from app.llm import GeminiProvider
    r = Resp({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota",
                        "details": [{"violations": [{"quotaMetric": "generativelanguage.googleapis.com/search_grounding",
                                                     "quotaId": "SearchGroundingPerDay", "quotaValue": "0"}]},
                                    {"retryDelay": "41s"}]}}, 429)
    msg = GeminiProvider._explain_error(r)
    assert "search_grounding" in msg and "limit=0" in msg and "41s" in msg
