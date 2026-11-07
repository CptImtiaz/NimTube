import shutil
from pathlib import Path

import pytest

from app.copyright import provenance
from app.video_editing.captions import caption_events, write_srt
from app.voice_generation.tts import apply_pronunciations, estimate_word_timings, synthesize


def test_pronunciations_and_timing_estimates():
    assert apply_pronunciations("World GDP in km", {"GDP": "G D P", "km": "kilometres"}) == "World G D P in kilometres"
    ws = estimate_word_timings("One two three, four.", 2.0)
    assert len(ws) == 4 and ws[0]["start"] >= 0 and ws[-1]["end"] <= 2.0
    assert all(b["start"] >= a["start"] for a, b in zip(ws, ws[1:]))


@pytest.mark.skipif(not shutil.which("espeak-ng"), reason="espeak-ng not installed")
def test_offline_tts_produces_audio(settings, tmp_path):
    v = synthesize(settings, "Number one: Ganymede.", tmp_path / "v.wav")
    assert v["provider"] == "espeak" and v["duration"] > 0.5 and v["timing"] == "estimated"


def test_captions_highlight_one_word_at_a_time(tmp_path):
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, w in enumerate("Number one is Ganymede.".split())]
    ev = caption_events(words)
    assert len(ev) == 4 and all("Dialogue: 3" in e for e in ev)
    srt = write_srt(tmp_path / "c.srt", [{"offset": 1.0, "words": words}]).read_text()
    assert "00:00:01,000 --> " in srt and "Ganymede." in srt


def test_unknown_asset_source_is_rejected(settings, db, tmp_path):
    f = tmp_path / "x.png"
    f.write_bytes(b"x" * 20000)
    a = provenance.register_asset(settings, db, "j", kind="image", path=f, source="random_google_image")
    assert a["status"] == "rejected" and "unknown rights" in a["reject_reason"]
    assert provenance.check_job_rights(db, "j", "test")


def test_noncommercial_voice_blocks_publish_but_not_test(settings, db, tmp_path):
    f = tmp_path / "v.wav"
    f.write_bytes(b"x" * 20000)
    g = tmp_path / "g.png"
    g.write_bytes(b"y" * 20000)
    provenance.register_asset(settings, db, "j", kind="voice", path=f, source="edge")
    provenance.register_asset(settings, db, "j", kind="graphic", path=g, source="original_graphics")
    assert provenance.check_job_rights(db, "j", "test") == []
    assert any("commercial" in p for p in provenance.check_job_rights(db, "j", "publish"))


def test_music_requires_manifest_licence_and_proof(settings, tmp_path, monkeypatch):
    mdir = tmp_path / "music"
    mdir.mkdir()
    (mdir / "ok.mp3").write_bytes(b"1")
    (mdir / "bad.mp3").write_bytes(b"1")
    (mdir / "manifest.yaml").write_text(
        "tracks:\n- {file: bad.mp3, licence: 'CC BY-NC 4.0', proof: 'x', moods: [cinematic]}\n"
        "- {file: ok.mp3, licence: 'CC0-1.0', proof: 'downloaded 2026', moods: [cinematic]}\n"
        "- {file: ../../etc/passwd, licence: 'CC0-1.0', proof: 'x'}\n")
    monkeypatch.setattr(provenance, "MUSIC_DIR", mdir)
    t = provenance.pick_music(settings, "cinematic")
    assert t["file"] == "ok.mp3"                   # NC licence and path traversal are refused
