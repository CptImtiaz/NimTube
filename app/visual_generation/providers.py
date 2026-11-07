"""External visual providers. Each returns (path, creator, proof) or raises. Every success is registered
in the asset-provenance database by the planner. No provider ever substitutes placeholder media."""
from __future__ import annotations

import base64
from pathlib import Path

from .. import http
from ..config import Settings, env
from ..log import get_logger

logger = get_logger("visuals")
STYLE = ("cinematic documentary still, vertical 9:16 composition, dramatic natural lighting, ultra detailed, "
         "photorealistic, no text, no letters, no watermark, no logos, no real identifiable people")


class VisualError(RuntimeError):
    pass


def openai_image(s: Settings, prompt: str, out: Path) -> tuple[Path, str, dict]:
    key = env("OPENAI_API_KEY")
    if not key:
        raise VisualError("OPENAI_API_KEY not set")
    model = env("OPENAI_IMAGE_MODEL", "gpt-image-1")
    data = http.post_json("https://api.openai.com/v1/images/generations",
                          {"model": model, "prompt": f"{prompt}. {STYLE}", "size": "1024x1536", "n": 1,
                           "quality": env("OPENAI_IMAGE_QUALITY", "medium")},
                          headers={"Authorization": f"Bearer {key}"}, timeout=240, retries=2)
    b64 = data["data"][0].get("b64_json")
    if not b64:
        raise VisualError("openai image: no image data")
    out = out.with_suffix(".png")
    out.write_bytes(base64.b64decode(b64))
    return out, f"OpenAI {model}", {"provider": "openai", "model": model, "prompt": prompt,
                                     "created": data.get("created")}


def gemini_image(s: Settings, prompt: str, out: Path) -> tuple[Path, str, dict]:
    key = env("GEMINI_API_KEY")
    if not key:
        raise VisualError("GEMINI_API_KEY not set")
    model = env("GEMINI_IMAGE_MODEL", "imagen-4.0-generate-001")
    data = http.post_json(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predict",
                          {"instances": [{"prompt": f"{prompt}. {STYLE}"}],
                           "parameters": {"sampleCount": 1, "aspectRatio": "9:16", "personGeneration": "dont_allow"}},
                          headers={"x-goog-api-key": key}, timeout=240, retries=2)
    preds = data.get("predictions") or []
    if not preds or not preds[0].get("bytesBase64Encoded"):
        raise VisualError(f"gemini image: no image returned ({str(data)[:200]})")
    out = out.with_suffix(".png")
    out.write_bytes(base64.b64decode(preds[0]["bytesBase64Encoded"]))
    return out, f"Google {model}", {"provider": "gemini", "model": model, "prompt": prompt}


def _download(url: str, out: Path) -> Path:
    r = http.request("GET", url, timeout=180, max_bytes=150_000_000, check_public=True)
    if r.status_code >= 400 or len(r.content) < 50_000:
        raise VisualError(f"download failed ({r.status_code})")
    out = out.with_suffix(".mp4")
    out.write_bytes(r.content)
    return out


def pexels(s: Settings, query: str, out: Path, used: set | None = None) -> tuple[Path, str, dict]:
    """Licensed stock footage (Pexels License). Prefers portrait clips; landscape clips are centre-cropped."""
    key = env("PEXELS_API_KEY")
    if not key:
        raise VisualError("PEXELS_API_KEY not set")
    used = used if used is not None else set()
    for orientation in ("portrait", "landscape"):
        data = http.get_json("https://api.pexels.com/videos/search",
                             params={"query": query, "orientation": orientation, "size": "medium", "per_page": 15},
                             headers={"Authorization": key}, timeout=30)
        for v in data.get("videos", []):
            vid = f"pexels:{v.get('id')}"
            if vid in used or v.get("duration", 0) < 4:
                continue
            min_h = 1280 if orientation == "portrait" else 1080
            files = sorted([f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"
                            and (f.get("height") or 0) >= min_h], key=lambda f: f["height"])
            if not files:
                continue
            f = files[0]
            try:
                path = _download(f["link"], out)
            except Exception:
                continue
            used.add(vid)
            user = v.get("user", {})
            return path, user.get("name", "Pexels contributor"), {
                "provider": "pexels", "pexels_video_id": v.get("id"), "page_url": v.get("url"),
                "photographer_url": user.get("url"), "file_url": f["link"], "query": query,
                "orientation": orientation, "duration": v.get("duration")}
    raise VisualError(f"pexels: no suitable video for '{query}'")


def pixabay(s: Settings, query: str, out: Path, used: set | None = None) -> tuple[Path, str, dict]:
    """Licensed stock footage (Pixabay Content License)."""
    key = env("PIXABAY_API_KEY")
    if not key:
        raise VisualError("PIXABAY_API_KEY not set")
    used = used if used is not None else set()
    data = http.get_json("https://pixabay.com/api/videos/", params={
        "key": key, "q": query[:100], "video_type": "film", "safesearch": "true", "per_page": 20}, timeout=30)
    for h in data.get("hits", []):
        vid = f"pixabay:{h.get('id')}"
        if vid in used or h.get("duration", 0) < 4:
            continue
        vids = h.get("videos", {})
        f = next((vids[k] for k in ("large", "medium") if vids.get(k, {}).get("url") and vids[k].get("height", 0) >= 720),
                 None)
        if not f:
            continue
        try:
            path = _download(f["url"], out)
        except Exception:
            continue
        used.add(vid)
        return path, h.get("user", "Pixabay contributor"), {
            "provider": "pixabay", "pixabay_video_id": h.get("id"), "page_url": h.get("pageURL"),
            "file_url": f["url"], "query": query, "duration": h.get("duration")}
    raise VisualError(f"pixabay: no suitable video for '{query}'")


FOOTAGE_PROVIDERS = {"pexels": pexels, "pixabay": pixabay}
IMAGE_PROVIDERS = {"openai_image": openai_image, "gemini_image": gemini_image}
