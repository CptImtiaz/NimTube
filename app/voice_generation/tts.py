"""Narration with interchangeable TTS providers. Returns audio + word timings for captions.

Timing accuracy by provider: ElevenLabs (character alignment) and Edge (word boundaries) are exact;
OpenAI and espeak timings are ESTIMATED by distributing the measured duration across words.
"""
from __future__ import annotations

import asyncio
import base64
import re
import shutil
import subprocess
from pathlib import Path

from .. import http
from ..config import Settings, env
from ..log import get_logger

logger = get_logger("voice")


class TTSError(RuntimeError):
    pass


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def apply_pronunciations(text: str, prons: dict) -> str:
    for word, spoken in (prons or {}).items():
        text = re.sub(rf"\b{re.escape(word)}\b", spoken, text)
    return text


def estimate_word_timings(text: str, duration: float, lead: float = 0.05) -> list[dict]:
    words = text.split()
    if not words:
        return []
    weights = [len(re.sub(r"\W", "", w)) + 2 + (3 if re.search(r"[.,;:!?]$", w) else 0) for w in words]
    total = sum(weights)
    usable = max(duration - lead * 2, 0.1)
    t, out = lead, []
    for w, wt in zip(words, weights):
        d = usable * wt / total
        out.append({"word": w, "start": round(t, 3), "end": round(t + d * 0.92, 3)})
        t += d
    return out


_POLISH = {"chain": ""}


def _to_wav(src: Path, dst: Path, speed: float = 1.0):
    """Decode to 48 kHz mono WAV with the broadcast polish chain (high-pass, compression, presence EQ)."""
    parts = [f"atempo={speed}"] if abs(speed - 1.0) > 0.01 else []
    if _POLISH["chain"]:
        parts.append(_POLISH["chain"])
    af = ",".join(parts) or "anull"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", af, "-ar", "48000", "-ac", "1",
                    str(dst)], check=True)


def _norm_word(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


def align_with_whisper(text: str, wav: Path) -> list[dict] | None:
    """Exact word timings from OpenAI transcription, aligned back onto the ORIGINAL script words."""
    import difflib

    key = env("OPENAI_API_KEY")
    if not key:
        return None
    with open(wav, "rb") as f:
        r = http.request("POST", "https://api.openai.com/v1/audio/transcriptions", timeout=120,
                         headers={"Authorization": f"Bearer {key}"},
                         files={"file": (wav.name, f, "audio/wav")},
                         data={"model": "whisper-1", "response_format": "verbose_json",
                               "timestamp_granularities[]": "word"})
    if r.status_code >= 400:
        logger.warning(f"whisper alignment failed: {r.status_code}")
        return None
    heard = r.json().get("words") or []
    if not heard:
        return None
    script = text.split()
    a = [_norm_word(w) for w in script]
    b = [_norm_word(h.get("word", "")) for h in heard]
    times: list[tuple[float, float] | None] = [None] * len(script)
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            h = heard[blk.b + k]
            times[blk.a + k] = (float(h["start"]), float(h["end"]))
    if sum(t is not None for t in times) < 0.6 * len(script):
        return None
    # interpolate unmatched words (numbers spoken as words, etc.) between their matched neighbours
    known = [i for i, t in enumerate(times) if t]
    out = []
    for i, w in enumerate(script):
        if times[i]:
            st, en = times[i]
        else:
            prev = max((k for k in known if k < i), default=None)
            nxt = min((k for k in known if k > i), default=None)
            p_end = times[prev][1] if prev is not None else 0.0
            n_st = times[nxt][0] if nxt is not None else p_end + 0.4
            gap_words = (nxt if nxt is not None else len(script)) - (prev if prev is not None else -1) - 1
            pos = i - (prev if prev is not None else -1)
            step = max(n_st - p_end, 0.05) / max(gap_words, 1)
            st = p_end + step * (pos - 1)
            en = st + step * 0.9
        out.append({"word": w, "start": round(st, 3), "end": round(max(en, st + 0.05), 3)})
    for x, y in zip(out, out[1:]):          # keep monotonic
        if y["start"] < x["start"]:
            y["start"] = x["start"]
            y["end"] = max(y["end"], y["start"] + 0.05)
    return out


# ---------------- providers ----------------
def tts_openai(s: Settings, text: str, out: Path) -> list[dict] | None:
    key = env("OPENAI_API_KEY")
    if not key:
        raise TTSError("OPENAI_API_KEY not set")
    r = http.request("POST", "https://api.openai.com/v1/audio/speech", timeout=120,
                     headers={"Authorization": f"Bearer {key}"},
                     json={"model": s.get("voice.openai_model", "gpt-4o-mini-tts"), "input": text,
                           "voice": s.get("voice.openai_voice", "onyx"),
                           "instructions": s.get("voice.openai_instructions", ""), "response_format": "wav"})
    if r.status_code >= 400:
        raise TTSError(f"openai tts {r.status_code}: {r.text[:300]}")
    raw = out.with_suffix(".raw.wav")
    raw.write_bytes(r.content)
    _to_wav(raw, out, float(s.get("voice.speed", 1.0)))
    raw.unlink(missing_ok=True)
    return None


def tts_elevenlabs(s: Settings, text: str, out: Path) -> list[dict] | None:
    key, voice = env("ELEVENLABS_API_KEY"), s.get("voice.elevenlabs_voice_id") or env("ELEVENLABS_VOICE_ID")
    if not (key and voice):
        raise TTSError("ELEVENLABS_API_KEY / voice id not set")
    data = http.post_json(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps",
                          {"text": text, "model_id": s.get("voice.elevenlabs_model", "eleven_multilingual_v2"),
                           "voice_settings": {"stability": 0.5, "similarity_boost": 0.75,
                                              "speed": float(s.get("voice.speed", 1.0))}},
                          headers={"xi-api-key": key}, timeout=180)
    mp3 = out.with_suffix(".mp3")
    mp3.write_bytes(base64.b64decode(data["audio_base64"]))
    _to_wav(mp3, out)
    al = data.get("alignment") or {}
    chars, starts, ends = al.get("characters", []), al.get("character_start_times_seconds", []), \
        al.get("character_end_times_seconds", [])
    words, cur, ws = [], "", None
    for c, st, en in zip(chars, starts, ends):
        if c.isspace():
            if cur:
                words.append({"word": cur, "start": ws, "end": prev_end})
            cur, ws = "", None
        else:
            if ws is None:
                ws = st
            cur += c
            prev_end = en
    if cur:
        words.append({"word": cur, "start": ws, "end": prev_end})
    return words


def tts_edge(s: Settings, text: str, out: Path) -> list[dict] | None:
    import edge_tts

    speed = float(s.get("voice.speed", 1.0))
    rate = f"{int(round((speed - 1) * 100)):+d}%"
    pitch = f"{int(s.get('voice.pitch_hz', 0)):+d}Hz"

    async def run():
        comm = edge_tts.Communicate(text, s.get("voice.edge_voice", "en-US-AndrewNeural"), rate=rate, pitch=pitch,
                                    boundary="WordBoundary")
        words = []
        mp3 = out.with_suffix(".mp3")
        with open(mp3, "wb") as f:
            async for ch in comm.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    st = ch["offset"] / 1e7
                    words.append({"word": ch["text"], "start": st, "end": st + ch["duration"] / 1e7})
        return words, mp3

    words, mp3 = asyncio.run(run())
    if not mp3.exists() or mp3.stat().st_size < 1000:
        raise TTSError("edge-tts returned no audio")
    _to_wav(mp3, out)
    return words


def tts_espeak(s: Settings, text: str, out: Path) -> list[dict] | None:
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        raise TTSError("espeak-ng not installed")
    raw = out.with_suffix(".raw.wav")
    wpm = int(165 * float(s.get("voice.speed", 1.0)))
    subprocess.run([exe, "-v", "en-us+m3", "-s", str(wpm), "-p", "42", "-w", str(raw), text], check=True,
                   capture_output=True)
    _to_wav(raw, out)
    raw.unlink(missing_ok=True)
    return None


_KOKORO = {}


def tts_kokoro(s: Settings, text: str, out: Path) -> list[dict] | None:
    """Kokoro-82M (Apache-2.0 open weights) running locally on the runner's CPU — free, no API key, natural voice.
    Install with: pip install -r requirements-voice.txt  (needs espeak-ng for out-of-vocabulary words).
    Returns exact word timings from the model's own token timestamps."""
    try:
        import numpy as np
        from kokoro import KPipeline
    except ImportError as e:
        raise TTSError(f"kokoro not installed ({e.name}); pip install -r requirements-voice.txt")
    voice = s.get("voice.kokoro_voice", "am_michael")
    lang = voice[0] if voice[:1] in "ab" else "a"                 # a = American, b = British English voices
    if lang not in _KOKORO:
        _KOKORO[lang] = KPipeline(lang_code=lang)
    pipe = _KOKORO[lang]
    sr, chunks, words, offset = 24000, [], [], 0.0
    for res in pipe(text, voice=voice, speed=float(s.get("voice.speed", 1.0)), split_pattern=r"\n+"):
        audio = res.audio
        if audio is None:
            continue
        a = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
        for t in (res.tokens or []):
            st, en = getattr(t, "start_ts", None), getattr(t, "end_ts", None)
            if st is None or en is None or not any(ch.isalnum() for ch in t.text):
                continue
            words.append({"word": t.text, "start": offset + st, "end": offset + en})
        chunks.append(a)
        offset += len(a) / sr
    if not chunks:
        raise TTSError("kokoro produced no audio")
    pcm = (np.clip(np.concatenate(chunks), -1, 1) * 32767).astype("<i2")
    raw = out.with_suffix(".kokoro.wav")
    import wave
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    _to_wav(raw, out)
    raw.unlink(missing_ok=True)
    # Kokoro tokenises punctuation separately; only trust its timings if every spoken word got one
    n_words = len([w for w in text.split() if any(c.isalnum() for c in w)])
    return words if words and abs(len(words) - n_words) <= max(2, n_words // 20) else None


PROVIDERS = {"kokoro": tts_kokoro, "openai": tts_openai, "elevenlabs": tts_elevenlabs, "edge": tts_edge, "espeak": tts_espeak}


def provider_order(s: Settings) -> list[str]:
    if s.mock:
        return ["espeak"]
    # the robotic offline engine is never used for real videos
    return [p for p in s.get("voice.provider_order", ["elevenlabs", "openai", "edge"]) if p != "espeak"]


def synthesize(s: Settings, text: str, out: Path, forced: str | None = None) -> dict:
    """Returns {path, duration, words:[{word,start,end}], provider, timing: exact|estimated}."""
    spoken = apply_pronunciations(text, s.get("voice.pronunciations", {}))
    _POLISH["chain"] = "" if s.mock else s.get("voice.polish", "")
    errors = []
    for name in ([forced] if forced else provider_order(s)):
        try:
            words = PROVIDERS[name](s, spoken, out)
            dur = ffprobe_duration(out)
            exact = bool(words) and spoken == text
            timing = "exact" if exact else "estimated"
            if not exact and not s.mock and s.get("voice.align_with_whisper", True):
                aligned = align_with_whisper(text, out)
                if aligned:
                    words, exact, timing = aligned, True, "whisper-aligned"
            if not exact:
                words = estimate_word_timings(text, dur)
            return {"path": out, "duration": dur, "words": words, "provider": name, "timing": timing}
        except Exception as e:
            errors.append(f"{name}: {str(e)[:160]}")
            logger.warning(f"tts provider {name} failed: {str(e)[:160]}")
    raise TTSError("all TTS providers failed: " + " | ".join(errors))
