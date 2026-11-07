"""Live API health check: one tiny real request per service. Prints OK / FAIL / not set — never key values."""
from __future__ import annotations

import requests

from .config import Settings, env

T = 25


SECRET_VARS = ("NVIDIA_API_KEY", "GEMINI_API_KEY", "TAVILY_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY",
               "COVERR_API_KEY", "HF_TOKEN", "YOUTUBE_API_KEY", "ELEVENLABS_API_KEY", "REDDIT_CLIENT_ID",
               "REDDIT_CLIENT_SECRET", "YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN", "ZAPIER_UPLOAD_HOOK", "STATE_PASSPHRASE", "NASA_API_KEY")


def redact(text: str) -> str:
    """Error messages can echo request URLs — strip every secret value before printing."""
    for v in SECRET_VARS:
        val = env(v)
        if val and len(val) >= 6:
            text = text.replace(val, "***")
    return text


def _err(r: requests.Response) -> str:
    return f"HTTP {r.status_code}: {r.text[:140].replace(chr(10), ' ')}"


def check_nvidia(s: Settings):
    from .llm import OpenAICompatProvider
    p = OpenAICompatProvider(s, None, "nvidia")
    out = p.complete("You are a test.", 'Return {"ok": true}')
    return '"ok"' in out or "ok" in out.lower(), f"model {p.model} answered"


def check_gemini(s: Settings):
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": env("GEMINI_API_KEY")},
                     timeout=T)
    return r.ok, f"{len(r.json().get('models', []))} models visible" if r.ok else _err(r)


def check_tavily(s: Settings):
    r = requests.post("https://api.tavily.com/search", json={"query": "largest moons solar system", "max_results": 1},
                      headers={"Authorization": f"Bearer {env('TAVILY_API_KEY')}"}, timeout=T)
    return r.ok, f"{len(r.json().get('results', []))} result(s)" if r.ok else _err(r)


def check_pexels(s: Settings):
    r = requests.get("https://api.pexels.com/videos/search", params={"query": "ocean", "per_page": 1},
                     headers={"Authorization": env("PEXELS_API_KEY")}, timeout=T)
    return r.ok, f"{r.json().get('total_results', 0)} videos for 'ocean'" if r.ok else _err(r)


def check_pixabay(s: Settings):
    r = requests.get("https://pixabay.com/api/videos/", params={"key": env("PIXABAY_API_KEY"), "q": "ocean",
                                                                "per_page": 3}, timeout=T)
    return r.ok, f"{r.json().get('totalHits', 0)} videos for 'ocean'" if r.ok else _err(r)


def check_coverr(s: Settings):
    r = requests.get("https://api.coverr.co/videos", params={"query": "ocean", "page_size": 1},
                     headers={"Authorization": f"Bearer {env('COVERR_API_KEY')}"}, timeout=T)
    return r.ok, f"{r.json().get('total', len(r.json().get('hits', [])))} videos for 'ocean'" if r.ok else _err(r)


def check_hf(s: Settings):
    r = requests.get("https://huggingface.co/api/whoami-v2", headers={"Authorization": f"Bearer {env('HF_TOKEN')}"},
                     timeout=T)
    return r.ok, "token valid" if r.ok else _err(r)


def check_nasa(s: Settings):
    r = requests.get("https://images-api.nasa.gov/search", params={"q": "jupiter", "media_type": "video"}, timeout=T)
    return r.ok, f"{len(r.json().get('collection', {}).get('items', []))} videos (no key needed)" if r.ok else _err(r)


def check_openverse(s: Settings):
    r = requests.get("https://api.openverse.org/v1/images/", params={"q": "moon", "license": "cc0,pdm,by",
                                                                    "page_size": 1}, timeout=T)
    return r.ok, f"{r.json().get('result_count', 0)} CC images (no key needed)" if r.ok else _err(r)


def check_youtube(s: Settings):
    r = requests.get("https://www.googleapis.com/youtube/v3/videos", params={
        "part": "id", "chart": "mostPopular", "regionCode": "US", "maxResults": 1, "key": env("YOUTUBE_API_KEY")},
        timeout=T)
    return r.ok, "trending list readable" if r.ok else _err(r)


def check_elevenlabs(s: Settings):
    r = requests.get("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": env("ELEVENLABS_API_KEY")}, timeout=T)
    return r.ok, "key valid" if r.ok else _err(r)


def check_reddit(s: Settings):
    r = requests.post("https://www.reddit.com/api/v1/access_token", data={"grant_type": "client_credentials"},
                      auth=(env("REDDIT_CLIENT_ID"), env("REDDIT_CLIENT_SECRET")),
                      headers={"User-Agent": env("REDDIT_USER_AGENT", "global-top10-ai/1.0")}, timeout=T)
    return r.ok and "access_token" in r.text, "token issued" if r.ok else _err(r)


def check_youtube_upload(s: Settings):
    from .youtube_upload.publisher import client
    ch = client().channels().list(part="snippet,status", mine=True).execute().get("items", [])
    if not ch:
        return False, "signed in, but this Google account has no YouTube channel"
    return True, f"can upload to channel '{ch[0]['snippet']['title']}'"


def check_kokoro(s: Settings):
    import importlib.util
    ok = importlib.util.find_spec("kokoro") is not None
    return ok, "installed (free voice ready)" if ok else "not installed in this environment"


CHECKS = [  # (label, required env var or None, function, needed?)
    ("NVIDIA (DeepSeek default LLM)", "NVIDIA_API_KEY", check_nvidia, True),
    ("Gemini (backup LLM)", "GEMINI_API_KEY", check_gemini, False),
    ("Tavily (research)", "TAVILY_API_KEY", check_tavily, True),
    ("Pixabay (footage)", "PIXABAY_API_KEY", check_pixabay, False),
    ("Coverr (footage)", "COVERR_API_KEY", check_coverr, False),
    ("NASA library (footage)", None, check_nasa, False),
    ("Openverse (CC photos)", None, check_openverse, False),
    ("Hugging Face (voice model)", "HF_TOKEN", check_hf, False),
    ("Kokoro voice", None, check_kokoro, False),
    ("YouTube sign-in (analytics)", "YT_REFRESH_TOKEN", check_youtube_upload, False),
]
PRESENCE = [("Zapier upload webhook", "ZAPIER_UPLOAD_HOOK", True), ("State passphrase", "STATE_PASSPHRASE", True)]


def run_checks(s: Settings) -> tuple[list[tuple[str, str, str]], bool]:
    rows, ok_all = [], True
    for label, var, fn, needed in CHECKS:
        if var and not env(var):
            rows.append((label, "NOT SET", "required" if needed else "optional"))
            ok_all &= not needed
            continue
        try:
            ok, note = fn(s)
        except Exception as e:
            ok, note = False, redact(str(e))[:160]
        note = redact(note)
        if fn is check_kokoro and not ok:
            rows.append((label, "INFO", "installed during video runs, not in this check"))
            continue
        rows.append((label, "OK" if ok else "FAIL", note))
        ok_all &= ok or not needed
    for label, var, needed in PRESENCE:
        rows.append((label, "SET" if env(var) else "NOT SET", "presence only (not called)"))
        ok_all &= bool(env(var)) or not needed
    footage = [r for r in rows if "(footage)" in r[0] and r[1] == "OK" and "NASA" not in r[0]]
    if not footage:
        rows.append(("Footage overall", "FAIL", "no working Pixabay/Coverr key — videos would use cards only"))
        ok_all = False
    return rows, ok_all


def report(rows) -> str:
    icon = {"OK": "✅", "SET": "✅", "FAIL": "❌", "NOT SET": "⚪", "INFO": "ℹ️"}
    lines = ["| Service | Status | Detail |", "|---|---|---|"]
    lines += [f"| {a} | {icon.get(b, '')} {b} | {c} |" for a, b, c in rows]
    return "\n".join(lines)
