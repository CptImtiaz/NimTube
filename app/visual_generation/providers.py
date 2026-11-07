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


SPACE_WORDS = ("space", "planet", "moon", "star", "galaxy", "nasa", "rocket", "mars", "jupiter", "saturn", "venus",
               "mercury", "neptune", "uranus", "pluto", "orbit", "astronaut", "nebula", "solar", "comet", "asteroid",
               "telescope", "sun", "earth from space", "cosmos", "universe", "launch", "satellite", "eclipse")
NASA_API = "https://images-api.nasa.gov"
NON_FREE_HINTS = ("©", "copyright", "all rights reserved", "getty", "reuters", "associated press", "ap photo")


def _space_query(query: str) -> bool:
    q = query.lower()
    return any(w in q for w in SPACE_WORDS)


def _nasa_items(query: str, media_type: str) -> list[dict]:
    data = http.get_json(f"{NASA_API}/search", params={"q": query[:100], "media_type": media_type, "page_size": 25},
                         timeout=30)
    out = []
    for it in data.get("collection", {}).get("items", []):
        d = (it.get("data") or [{}])[0]
        blob = " ".join(str(d.get(k, "")) for k in ("description", "photographer", "secondary_creator", "title")).lower()
        if any(h in blob for h in NON_FREE_HINTS):
            continue                      # credited to a non-NASA rights holder -> skip
        out.append({"href": it.get("href"), "nasa_id": d.get("nasa_id"), "title": d.get("title", ""),
                    "center": d.get("center", "NASA")})
    return out


def nasa_video(s: Settings, query: str, out: Path, used: set | None = None) -> tuple[Path, str, dict]:
    """NASA Image and Video Library (no key). Used only for space/astronomy queries."""
    if not _space_query(query):
        raise VisualError("nasa: not a space query")
    used = used if used is not None else set()
    for it in _nasa_items(query, "video"):
        vid = f"nasa:{it['nasa_id']}"
        if vid in used or not it["href"]:
            continue
        files = http.get_json(it["href"], timeout=30)
        mp4 = [f for f in files if isinstance(f, str) and f.endswith(".mp4")]
        pick = next((f for k in ("~medium.mp4", "~mobile.mp4", "~orig.mp4") for f in mp4 if f.endswith(k)), None)
        if not pick:
            continue
        try:
            path = _download(pick.replace("http://", "https://"), out)
        except Exception:
            continue
        used.add(vid)
        return path, f"NASA ({it['center']})", {"provider": "nasa", "nasa_id": it["nasa_id"], "title": it["title"],
                                               "file_url": pick, "query": query}
    raise VisualError(f"nasa: no suitable video for '{query}'")


def nasa_image(s: Settings, query: str, out: Path) -> tuple[Path, str, dict]:
    if not _space_query(query):
        raise VisualError("nasa: not a space query")
    for it in _nasa_items(query, "image"):
        files = http.get_json(it["href"], timeout=30)
        pick = next((f for k in ("~large.jpg", "~medium.jpg", "~orig.jpg") for f in files
                     if isinstance(f, str) and f.endswith(k)), None)
        if not pick:
            continue
        r = http.request("GET", pick.replace("http://", "https://"), timeout=60, max_bytes=25_000_000, check_public=True)
        if r.status_code >= 400 or len(r.content) < 30_000:
            continue
        out = out.with_suffix(".jpg")
        out.write_bytes(r.content)
        return out, f"NASA ({it['center']})", {"provider": "nasa", "nasa_id": it["nasa_id"], "title": it["title"],
                                               "file_url": pick, "query": query}
    raise VisualError(f"nasa: no suitable image for '{query}'")


OPENVERSE_LICENCES = {"cc0": ("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/", False),
                      "pdm": ("Public Domain Mark 1.0", "https://creativecommons.org/publicdomain/mark/1.0/", False),
                      "by": ("CC BY", "https://creativecommons.org/licenses/by/4.0/", True)}


def openverse_image(s: Settings, query: str, out: Path) -> tuple[Path, str, dict]:
    """Creative Commons images via Openverse (no key). ONLY CC0, Public Domain Mark and CC BY are accepted —
    BY-SA (share-alike would bind the whole video), NC (non-commercial) and ND (no derivatives) are excluded."""
    data = http.get_json("https://api.openverse.org/v1/images/", params={
        "q": query[:200], "license": "cc0,pdm,by", "page_size": 20, "mature": "false"}, timeout=30)
    for it in data.get("results", []):
        lic = (it.get("license") or "").lower()
        if lic not in OPENVERSE_LICENCES or (it.get("width") or 0) < 900:
            continue
        try:
            r = http.request("GET", it["url"], timeout=60, max_bytes=25_000_000, check_public=True)
        except Exception:
            continue
        if r.status_code >= 400 or len(r.content) < 30_000:
            continue
        out = out.with_suffix(".jpg")
        out.write_bytes(r.content)
        name, url, needs_credit = OPENVERSE_LICENCES[lic]
        return out, it.get("creator") or "Unknown creator", {
            "provider": "openverse", "openverse_id": it.get("id"), "landing_page": it.get("foreign_landing_url"),
            "file_url": it.get("url"), "attribution": it.get("attribution", ""), "query": query,
            "licence_override": {"licence_name": f"{name} {it.get('license_version') or ''}".strip(),
                                 "licence_url": it.get("license_url") or url, "commercial_use": True,
                                 "attribution_required": needs_credit, "modifications_allowed": True,
                                 "synthetic": False, "notes": it.get("attribution", "")}}
    raise VisualError(f"openverse: no suitable CC0/PD/CC-BY image for '{query}'")


def coverr(s: Settings, query: str, out: Path, used: set | None = None) -> tuple[Path, str, dict]:
    """Coverr free stock video API (COVERR_API_KEY). Coverr licence: free for commercial use, no attribution."""
    key = env("COVERR_API_KEY")
    if not key:
        raise VisualError("COVERR_API_KEY not set")
    used = used if used is not None else set()
    data = http.get_json("https://api.coverr.co/videos", params={"query": query[:100], "page_size": 20,
                                                                  "urls": "true"},
                         headers={"Authorization": f"Bearer {key}"}, timeout=30)
    hits = data.get("hits") or []
    hits.sort(key=lambda h: 0 if h.get("is_vertical") or str(h.get("aspect_ratio", "")).startswith("9") else 1)
    for h in hits:
        vid = f"coverr:{h.get('id')}"
        urls = h.get("urls") or {}
        link = urls.get("mp4_download") or urls.get("mp4")
        if vid in used or not link:
            continue
        try:
            path = _download(link, out)
        except Exception:
            continue
        used.add(vid)
        return path, "Coverr", {"provider": "coverr", "coverr_id": h.get("id"), "title": h.get("title", ""),
                                "query": query}
    raise VisualError(f"coverr: no video for '{query}'")


FOOTAGE_PROVIDERS = {"pexels": pexels, "pixabay": pixabay, "coverr": coverr, "nasa": nasa_video}
SEARCH_IMAGE_PROVIDERS = {"nasa_image": nasa_image, "openverse": openverse_image}   # real photos, searched by query
IMAGE_PROVIDERS = {"openai_image": openai_image, "gemini_image": gemini_image}     # AI-generated, by prompt
