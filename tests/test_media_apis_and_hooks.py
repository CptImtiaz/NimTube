"""NASA / Openverse providers, Kokoro voice wiring, hook checks, SFX and the teaser overlay."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.fact_checking.checker import check_script
from app.script_generation.writer import full_text, segment_voice_text
from app.video_editing.captions import section_ass
from app.video_editing.sfx import make_sfx
from app.visual_generation import providers as P
from app.voice_generation import tts

FIX = Path(__file__).parent / "fixtures" / "llm"


def test_nasa_only_for_space_queries(settings, tmp_path):
    with pytest.raises(P.VisualError, match="not a space query"):
        P.nasa_video(settings, "kitchen blender close up", tmp_path / "x")
    assert P._space_query("jupiter planet space")
    assert not P._space_query("volcano lava night")


def test_nasa_skips_non_nasa_credit(monkeypatch):
    monkeypatch.setattr(P.http, "get_json", lambda *a, **k: {"collection": {"items": [
        {"href": "h1", "data": [{"nasa_id": "a", "description": "Photo © Getty Images", "title": "x"}]},
        {"href": "h2", "data": [{"nasa_id": "b", "description": "Saturn by Cassini", "title": "Saturn"}]}]}})
    items = P._nasa_items("saturn", "image")
    assert [i["nasa_id"] for i in items] == ["b"]


def test_openverse_rejects_share_alike_and_nc(monkeypatch, settings, tmp_path):
    calls = {}

    def fake_get_json(url, params=None, **k):
        calls["params"] = params
        return {"results": [{"license": "by-sa", "width": 2000, "url": "u1"},
                            {"license": "by-nc", "width": 2000, "url": "u2"}]}
    monkeypatch.setattr(P.http, "get_json", fake_get_json)
    with pytest.raises(P.VisualError):
        P.openverse_image(settings, "red panda", tmp_path / "img")
    assert calls["params"]["license"] == "cc0,pdm,by"


def test_openverse_cc_by_carries_licence_override(monkeypatch, settings, tmp_path):
    monkeypatch.setattr(P.http, "get_json", lambda *a, **k: {"results": [
        {"license": "by", "license_version": "4.0", "width": 2000, "url": "https://img.example/x.jpg",
         "creator": "Jane", "id": "ov1", "foreign_landing_url": "https://flickr.example/1",
         "license_url": "https://creativecommons.org/licenses/by/4.0/", "attribution": "x by Jane, CC BY 4.0"}]})

    class R:
        status_code, content = 200, b"\xff" * 40_000
    monkeypatch.setattr(P.http, "request", lambda *a, **k: R())
    path, creator, proof = P.openverse_image(settings, "red panda", tmp_path / "img")
    assert path.exists() and creator == "Jane"
    lic = proof["licence_override"]
    assert lic["attribution_required"] and lic["commercial_use"] and "CC BY" in lic["licence_name"]


def test_providers_registered_in_licence_table(settings):
    media = settings.providers["media_sources"]
    for k in ("nasa", "nasa_image", "openverse", "original_sfx"):
        assert media[k]["commercial_use"] is True
    assert settings.providers["voice_sources"]["kokoro"]["commercial_use"] is True
    assert "kokoro" in tts.PROVIDERS


def test_kokoro_missing_raises_tts_error(monkeypatch, settings, tmp_path):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "kokoro":
            raise ImportError("no kokoro", name="kokoro")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(tts.TTSError, match="kokoro not installed"):
        tts.tts_kokoro(settings, "hello world", tmp_path / "v.wav")


def _fixture_script():
    return json.loads((FIX / "script.json").read_text())["response"]


def test_rehook_is_spoken_before_number_three():
    sc = _fixture_script()
    seg3 = next(x for x in sc["segments"] if x["rank"] == 3)
    assert segment_voice_text(sc, seg3).startswith(sc["rehook"])
    assert sc["rehook"] in full_text(sc)


@pytest.fixture
def ranking_fixture(settings, db):
    from app.llm import LLM
    from app.ranking.engine import build_ranking
    from app.research.engine import research_topic
    topic = {"title": "Top 10 Largest Moons in the Solar System", "ranking_type": "factual",
             "metric": "mean diameter", "unit": "km"}
    return build_ranking(settings, topic, research_topic(settings, db, LLM(settings), topic, "j"))


def test_hook_rules(settings, ranking_fixture):
    from app.script_generation.writer import write_script
    from app.llm import LLM
    sc = write_script(settings, LLM(settings), ranking_fixture)
    assert not [p for p in check_script(settings, sc, ranking_fixture) if "hook" in p]
    bad = copy.deepcopy(sc)
    bad["hook_text"] = "this hook text is far too long to read"
    assert any("at most 6 words" in p for p in check_script(settings, bad, ranking_fixture))
    bad = copy.deepcopy(sc)
    bad["hook_text"] = "GANYMEDE WINS"
    assert any("must not reveal" in p for p in check_script(settings, bad, ranking_fixture))
    bad = copy.deepcopy(sc)
    bad["hook"] = "Did you know moons are big?"
    assert any("weak opener" in p for p in check_script(settings, bad, ranking_fixture))
    bad = copy.deepcopy(sc)
    bad["hook_text"] = "#1 IS 4321 TIMES BIGGER"
    assert any("4321" in p for p in check_script(settings, bad, ranking_fixture))


def test_sfx_generated(tmp_path):
    fx = make_sfx(tmp_path)
    assert set(fx) == {"whoosh", "impact", "riser"}
    assert all(p.stat().st_size > 10_000 for p in fx.values())


def test_hook_overlay_and_banner(tmp_path):
    ass = section_ass(tmp_path / "i.ass", 6.0, [], title="Top 10 Largest Moons", hook_text="Wait until you see #1",
                      hook_until=1.6, brand=True)
    txt = ass.read_text()
    assert ",Hook,," in txt and "WAIT UNTIL" in txt
    assert "0:00:01.60,0:00:06.00,Top10" in txt           # title waits for the teaser to finish
    b = section_ass(tmp_path / "b.ass", 5.0, [], rank=3, name="Io", banner="The top 3").read_text()
    assert ",Banner,," in b and "THE TOP 3" in b
