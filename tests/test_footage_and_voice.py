import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.llm import LLM
from app.ranking.engine import build_ranking
from app.research.engine import research_topic
from app.script_generation.writer import write_script
from app.video_editing.render import _split
from app.visual_generation import planner, providers
from app.voice_generation import tts

TOPIC = {"title": "Top 10 Largest Moons in the Solar System", "ranking_type": "factual",
         "metric": "mean diameter", "unit": "km"}


def _fake_clip(path: Path, seconds=6):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc2=s=1280x720:r=30:d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(path)], check=True)
    return path


def test_split_gives_footage_most_time(settings):
    items = [{"source": "pexels", "footage": True}, {"source": "pexels", "footage": True},
             {"source": "original_graphics", "role": "data_card"}]
    shots = _split(settings, 10.0, items)
    assert abs(sum(x["dur"] for x in shots) - 10.0) < 1e-6
    card = [x for x in shots if x.get("role") == "data_card"][0]
    assert 1.6 <= card["dur"] <= 3.5 and card["dur"] < shots[0]["dur"] + shots[1]["dur"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_planner_uses_unique_real_footage_first(settings, db, tmp_path, monkeypatch):
    calls = []

    def fake_pexels(s, query, out, used):
        clip_id = f"pexels:{len(calls)}"
        calls.append(query)
        used.add(clip_id)
        return _fake_clip(out.with_suffix(".mp4"), 5), "Test Videographer", {"provider": "pexels", "id": clip_id,
                                                                            "query": query}
    monkeypatch.setitem(providers.FOOTAGE_PROVIDERS, "pexels", fake_pexels)
    monkeypatch.setattr(planner, "FOOTAGE_PROVIDERS", providers.FOOTAGE_PROVIDERS)
    llm = LLM(settings)
    ranking = build_ranking(settings, TOPIC, research_topic(settings, db, llm, TOPIC, "j"))
    script = write_script(settings, llm, ranking)
    settings.mock = False                      # let the planner call the (fake) footage provider
    settings.raw["visuals"]["footage_providers"] = ["pexels"]
    settings.raw["visuals"]["image_providers"] = []
    plan = planner.plan_visuals(settings, db, "j", ranking, script, tmp_path / "vis")
    assert plan["intro_has_footage"] and plan["footage_share"] == 1.0
    for items in plan["segments"].values():
        assert items[0]["footage"] and items[-1]["role"] == "data_card"
        assert sum(1 for i in items if i["footage"]) == 2
    assert len({i["path"] for v in plan["segments"].values() for i in v}) == 30   # nothing reused
    assert calls[0] == "solar system planets"                                     # writer's queries are used
    a = db.one("SELECT licence_name, creator, proof FROM assets WHERE source='pexels' LIMIT 1")
    assert a["licence_name"] == "Pexels License" and a["creator"] == "Test Videographer"


def test_whisper_alignment_maps_back_to_script_words(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-xxxxxxxxxxxxxxxxxxxx")
    wav = tmp_path / "v.wav"
    wav.write_bytes(b"RIFF0000")

    class R:
        status_code = 200

        def json(self):
            return {"words": [{"word": "Number", "start": 0.1, "end": 0.4}, {"word": "one", "start": 0.45, "end": 0.6},
                              {"word": "Ganymede", "start": 0.8, "end": 1.4}, {"word": "five", "start": 1.6, "end": 1.8},
                              {"word": "thousand", "start": 1.85, "end": 2.2}, {"word": "kilometres", "start": 2.9, "end": 3.5}]}
    monkeypatch.setattr(tts.http, "request", lambda *a, **k: R())
    words = tts.align_with_whisper("Number one: Ganymede, 5,268 kilometres.", wav)
    assert [w["word"] for w in words] == ["Number", "one:", "Ganymede,", "5,268", "kilometres."]
    assert words[2]["start"] == 0.8 and words[4]["start"] == 2.9
    assert words[2]["end"] <= words[3]["start"] <= words[4]["start"]          # number interpolated in between


def test_robotic_voice_never_used_for_real_runs(settings):
    settings.mock = False
    settings.raw["voice"]["provider_order"] = ["openai", "espeak"]
    assert tts.provider_order(settings) == ["openai"]
