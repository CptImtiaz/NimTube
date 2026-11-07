"""ASS overlay builder (animated countdown numbers, entry titles, word-level karaoke captions) and SRT export."""
from __future__ import annotations

import re
from pathlib import Path

W, H = 1080, 1920
GOLD_ASS = "&H0000B7F5&"     # BGR of (245,183,0)
WHITE_ASS = "&H00F5F5F5&"


def t_ass(sec: float) -> str:
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def t_srt(sec: float) -> str:
    ms = int(round(max(sec, 0) * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def esc(text: str) -> str:
    return (text or "").replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Brand,DejaVu Sans,34,{GOLD_ASS},{GOLD_ASS},&H00000000,&H64000000,1,0,0,0,100,100,2,0,1,3,0,7,60,60,70,1
Style: Rank,DejaVu Sans,230,{GOLD_ASS},{GOLD_ASS},&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,9,6,8,40,40,170,1
Style: Name,DejaVu Sans,82,{WHITE_ASS},{WHITE_ASS},&H00000000,&H96000000,1,0,0,0,100,100,1,0,1,6,4,8,60,60,430,1
Style: Value,DejaVu Sans,64,{GOLD_ASS},{GOLD_ASS},&H00000000,&H96000000,1,0,0,0,100,100,1,0,1,5,3,8,60,60,540,1
Style: Label,DejaVu Sans,40,{WHITE_ASS},{WHITE_ASS},&H00000000,&H96000000,1,0,0,0,100,100,3,0,3,8,0,8,60,60,330,1
Style: Title,DejaVu Sans,96,{WHITE_ASS},{WHITE_ASS},&H00000000,&H96000000,1,0,0,0,100,100,1,0,1,7,4,8,70,70,560,1
Style: Top10,DejaVu Sans,190,{GOLD_ASS},{GOLD_ASS},&H00000000,&H96000000,1,0,0,0,100,100,2,0,1,9,5,8,40,40,330,1
Style: Hook,DejaVu Sans,112,{WHITE_ASS},{WHITE_ASS},&H00000000,&HB4000000,1,0,0,0,100,100,1,0,1,9,6,5,70,70,0,1
Style: Banner,DejaVu Sans,62,&H00101010&,&H00101010&,{GOLD_ASS},&H00000000,1,0,0,0,100,100,3,0,3,18,0,8,60,60,250,1
Style: Cap,DejaVu Sans,74,{WHITE_ASS},{WHITE_ASS},&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,7,3,2,80,80,400,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def caption_events(words: list[dict], offset: float = 0.0, max_words: int = 4, max_chars: int = 24) -> list[str]:
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or len(" ".join(x["word"] for x in cur + [w])) > max_chars):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w["word"].rstrip().endswith((".", "!", "?", ":")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    ev = []
    for ci, ch in enumerate(chunks):
        chunk_end = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else ch[-1]["end"] + 0.3
        for wi, w in enumerate(ch):
            end = ch[wi + 1]["start"] if wi + 1 < len(ch) else chunk_end
            if end <= w["start"]:
                end = w["start"] + 0.05
            parts = [(f"{{\\c{GOLD_ASS}}}{esc(x['word']).upper()}{{\\c{WHITE_ASS}}}" if j == wi
                      else esc(x["word"]).upper()) for j, x in enumerate(ch)]
            ev.append(f"Dialogue: 3,{t_ass(w['start'] + offset)},{t_ass(end + offset)},Cap,,0,0,0,,{' '.join(parts)}")
    return ev


def section_ass(path: Path, duration: float, words: list[dict], *, rank: int | None = None, name: str = "",
                value_label: str = "", value_until: float | None = None, label: str = "", brand: bool = True,
                voice_offset: float = 0.0, title: str = "", outro_brand: bool = False, hook_text: str = "",
                hook_until: float = 0.0, banner: str = ""):
    ev = []
    title_from = 0.0
    if hook_text and hook_until > 0:     # teaser of #1: big punchy hook, slam-in then hold
        lines, cur = [], ""
        for word in esc(hook_text).upper().split():
            if cur and len(cur) + 1 + len(word) > 14:
                lines.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        lines.append(cur)
        slam = r"{\fad(0,150)\fscx160\fscy160\t(0,180,\fscx100\fscy100)}"
        ev.append(f"Dialogue: 4,{t_ass(0)},{t_ass(hook_until)},Hook,,0,0,0,,{slam}" + "\\N".join(lines[:3]))
        title_from = hook_until
    if banner:                      # re-hook banner, e.g. before the top 3
        ev.append(f"Dialogue: 4,{t_ass(0)},{t_ass(min(2.2, duration))},Banner,,0,0,0,,"
                  r"{\fad(120,250)\fscx70\t(0,200,\fscx100)}" + esc(banner).upper())
    if title:                       # intro over real footage: animated "TOP 10" + topic
        topic = re.sub(r"(?i)^top\s*10\s*", "", title).strip().upper()
        ev.append(f"Dialogue: 2,{t_ass(title_from)},{t_ass(duration)},Top10,,0,0,0,,"
                  r"{\fad(200,300)\fscx40\fscy40\t(0,300,\fscx110\fscy110)\t(300,450,\fscx100\fscy100)}TOP 10")
        lines, cur = [], ""
        for word in esc(topic).split():              # wrap to ~16 characters per line (fits 1080 px at 96 pt)
            if cur and len(cur) + 1 + len(word) > 16:
                lines.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        lines.append(cur)
        ev.append(f"Dialogue: 2,{t_ass(title_from + 0.35)},{t_ass(duration)},Title,,0,0,0,,{{\\fad(350,300)}}" + "\\N".join(lines[:4]))
    if outro_brand:
        ev.append(f"Dialogue: 2,{t_ass(0.2)},{t_ass(duration)},Top10,,0,0,0,,"
                  r"{\fad(400,400)\fs84}GLOBAL TOP 10 AI")
    if brand:
        ev.append(f"Dialogue: 0,{t_ass(0)},{t_ass(duration)},Brand,,0,0,0,,GLOBAL TOP 10 AI")
    if label:
        ev.append(f"Dialogue: 1,{t_ass(0.2)},{t_ass(duration)},Label,,0,0,0,,{{\\fad(300,200)}}{esc(label)}")
    if rank is not None:
        pop = r"{\fad(120,250)\fscx30\fscy30\t(0,260,\fscx118\fscy118)\t(260,420,\fscx100\fscy100)}"
        ev.append(f"Dialogue: 2,{t_ass(0)},{t_ass(duration)},Rank,,0,0,0,,{pop}#{rank}")
        slide = r"{\fad(300,250)\move(540,470,540,430,150,550)}"
        ev.append(f"Dialogue: 2,{t_ass(0.15)},{t_ass(duration)},Name,,0,0,0,,{slide}{esc(name).upper()}")
        if value_label:
            ev.append(f"Dialogue: 2,{t_ass(0.45)},{t_ass(value_until or duration)},Value,,0,0,0,,"
                      f"{{\\fad(250,200)}}{esc(value_label)}")
    ev += caption_events(words, offset=voice_offset)
    path.write_text(HEADER + "\n".join(ev) + "\n", encoding="utf-8")
    return path


def write_srt(path: Path, sections: list[dict]):
    """sections: [{offset, words}] -> sentence-ish SRT cues for YouTube caption upload."""
    cues, idx = [], 1
    for sec in sections:
        words = sec["words"]
        buf = []
        for w in words:
            buf.append(w)
            if len(buf) >= 7 or w["word"].endswith((".", "!", "?")):
                cues.append((sec["offset"] + buf[0]["start"], sec["offset"] + buf[-1]["end"],
                             " ".join(x["word"] for x in buf)))
                buf = []
        if buf:
            cues.append((sec["offset"] + buf[0]["start"], sec["offset"] + buf[-1]["end"], " ".join(x["word"] for x in buf)))
    lines = []
    for s, e, text in cues:
        lines += [str(idx), f"{t_srt(s)} --> {t_srt(max(e, s + 0.3))}", text, ""]
        idx += 1
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
