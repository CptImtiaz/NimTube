"""FFmpeg renderer: sections (intro, #10..#1, outro) -> concat -> music with sidechain ducking -> loudness
normalisation -> H.264/AAC MP4 1080x1920."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..config import Settings
from ..log import get_logger
from ..visual_generation.graphics import fmt_value
from .captions import section_ass, write_srt

logger = get_logger("render")
VOICE_LEAD = 0.25     # seconds of picture before narration starts in each section
TAIL = 0.45           # seconds after narration ends


class RenderError(RuntimeError):
    pass


def run(cmd: list[str]):
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if p.returncode != 0:
        raise RenderError(f"ffmpeg failed: {' '.join(map(str, cmd[:8]))} ...\n{p.stderr[-2000:]}")
    return p.stdout


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path])
    return json.loads(out)


def _ass_path(p: Path) -> str:
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_section(s: Settings, shots: list[dict], voice: dict, ass: Path, out: Path, duration: float):
    """shots: [{kind: image|video, path, dur}] whose durations sum to `duration`."""
    w, h, fps = s.get("video.width", 1080), s.get("video.height", 1920), s.get("video.fps", 30)
    tr = float(s.get("video.transition_sec", 0.35))
    g = s.get("visuals.grade", "")
    grade = f",{g}" if g else ""
    inputs, filters, labels = [], [], []
    for i, sh in enumerate(shots):
        d = sh["dur"]
        if sh["kind"] == "image":
            inputs += ["-loop", "1", "-framerate", fps, "-t", f"{d:.3f}", "-i", sh["path"]]
            # slow camera move: scale to 110% cover, then pan across the spare margin
            dx, dy = ("(iw-ow)*t/{d}", "(ih-oh)/2") if i % 2 == 0 else ("(iw-ow)*(1-t/{d})", "(ih-oh)*t/{d}")
            filters.append(
                f"[{i}:v]scale={int(w * 1.1)}:{int(h * 1.1)}:force_original_aspect_ratio=increase,"
                f"crop={int(w * 1.1)}:{int(h * 1.1)},"
                f"crop={w}:{h}:x='{dx.format(d=d)}':y='{dy.format(d=d)}',setsar=1,fps={fps}"
                f"{grade if sh.get('source') != 'original_graphics' else ''},format=yuv420p[s{i}]")
        else:
            try:
                clip_dur = float(probe(Path(sh["path"]))["format"]["duration"])
            except Exception:
                clip_dur = 0.0
            # start a little into the clip (skip shaky first frames); loop only if the clip is too short
            start = min(1.0, max(0.0, (clip_dur - d) / 3)) if clip_dur > d else 0.0
            loop = ["-stream_loop", "-1"] if clip_dur and clip_dur < d + start else []
            inputs += [*loop, "-ss", f"{start:.2f}", "-t", f"{d:.3f}", "-i", sh["path"]]
            filters.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
                           f"fps={fps},trim=duration={d:.3f},setpts=PTS-STARTPTS{grade}," +
                           f"format=yuv420p[s{i}]")
        labels.append(f"[s{i}]")
    n = len(shots)
    a_idx = n
    inputs += ["-i", voice["path"]]
    filters.append(f"{''.join(labels)}concat=n={n}:v=1:a=0,ass='{_ass_path(ass)}',"
                   f"fade=t=in:st=0:d={tr},fade=t=out:st={max(duration - tr, 0):.3f}:d={tr}[v]")
    lead_ms = int(VOICE_LEAD * 1000)
    filters.append(f"[{a_idx}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={lead_ms}|{lead_ms},"
                   f"apad,atrim=duration={duration:.3f}[a]")
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters),
         "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}", "-r", fps,
         "-c:v", "libx264", "-preset", s.get("video.preset", "veryfast"), "-crf", s.get("video.crf", 20),
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", out])
    return out


def _split(s: Settings, duration: float, items: list[dict]) -> list[dict]:
    """Footage/stills share most of the time; the original data card gets a short closing beat."""
    if len(items) == 1:
        return [{**items[0], "dur": duration}]
    cards = [it for it in items if it.get("role") == "data_card"]
    others = [it for it in items if it.get("role") != "data_card"]
    card_t = 0.0
    if cards and others:
        card_t = min(max(duration * float(s.get("visuals.data_card_share", 0.25)), 1.6), 3.5)
    rest = duration - card_t * len(cards)
    out = [{**it, "dur": rest / len(others)} for it in others] if others else []
    out += [{**c, "dur": card_t if others else duration / len(cards)} for c in cards]
    return out


def render_video(s: Settings, ranking: dict, script: dict, voices: dict, visuals: dict, work: Path,
                 music: dict | None) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    sections, srt_sections, offset = [], [], 0.0
    by_rank = {e["rank"]: e for e in ranking["entries"]}

    def add(key: str, shots_src: list[dict], **ass_kw):
        nonlocal offset
        v = voices[key]
        dur = VOICE_LEAD + v["duration"] + TAIL
        shots = _split(s, dur, shots_src)
        ass = section_ass(work / f"{key}.ass", dur, v["words"], voice_offset=VOICE_LEAD, **ass_kw)
        out = render_section(s, shots, v, ass, work / f"{key}.mp4", dur)
        sections.append(out)
        srt_sections.append({"offset": offset + VOICE_LEAD, "words": v["words"]})
        offset += dur

    label = "EDITORIAL RANKING" if ranking["ranking_type"] == "editorial" else ""
    add("intro", visuals["intro"], brand=bool(visuals.get("intro_has_footage")), label=label,
        title=ranking["title"] if visuals.get("intro_has_footage") else "")
    for seg in script["segments"]:
        e = by_rank[seg["rank"]]
        items = visuals["segments"][e["rank"]]
        has_ext = items[0]["source"] != "original_graphics"
        val = fmt_value(e.get("value"), e.get("unit")) if isinstance(e.get("value"), (int, float)) else ""
        dur_est = VOICE_LEAD + voices[f"seg{e['rank']}"]["duration"] + TAIL
        card_t = sum(x["dur"] for x in _split(s, dur_est, items) if x.get("role") == "data_card")
        add(f"seg{e['rank']}", items, rank=e["rank"], name=e["name"],
            value_label=val if has_ext else "", value_until=(dur_est - card_t) if has_ext else None)
    add("outro", visuals["outro"], outro_brand=bool(visuals.get("outro_has_footage")))

    lst = work / "sections.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in sections))
    joined = work / "joined.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])

    final = work / "final.mp4"
    lufs = s.get("video.loudness_lufs", -14)
    if music:
        mv = s.get("video.music_volume", 0.18)
        fc = (f"[0:a]asplit=2[vo][key];[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={mv}[m];"
              f"[m][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[md];"
              f"[vo][md]amix=inputs=2:duration=first:normalize=0,loudnorm=I={lufs}:TP=-1.5:LRA=11[a]")
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", joined, "-stream_loop", "-1", "-i", music["path"],
             "-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-ar", "48000", "-shortest", "-movflags", "+faststart", final])
    else:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", joined, "-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", final])
    srt = write_srt(work / "captions.srt", srt_sections)
    return {"video": final, "srt": srt, "duration": offset, "sections": [str(p) for p in sections]}
