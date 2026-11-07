"""Original motion-graphics stills for GLOBAL TOP 10 AI (drawn with Pillow — fully original assets).
Cards are rendered 10% larger than 1080x1920 so the editor can add slow camera moves."""
from __future__ import annotations

import math
import random
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import ROOT

W, H = 1188, 2112             # 1.1 x 1080x1920
GOLD = (245, 183, 0)
CYAN = (64, 200, 255)
WHITE = (245, 245, 245)
DIM = (150, 160, 180)
FONT_CANDIDATES = [
    ROOT / "assets" / "fonts" / "Brand-Bold.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    Path("/Library/Fonts/Arial Bold.ttf"), Path("C:/Windows/Fonts/arialbd.ttf"),
]
FONT_REG = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/dejavu/DejaVuSans.ttf")]


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for p in (FONT_CANDIDATES if bold else FONT_REG + FONT_CANDIDATES):
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


def _background(seed: int, hue_shift: int = 0) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", (W, H))
    top = (8, 14 + hue_shift // 4, 34 + hue_shift // 2)
    bottom = (2, 3, 8)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    # star field + soft light leak
    for _ in range(420):
        x, y = rnd.randrange(W), rnd.randrange(H)
        b = rnd.randint(70, 230)
        r = rnd.choice([1, 1, 1, 2])
        d.ellipse([x - r, y - r, x + r, y + r], fill=(b, b, min(255, b + 20)))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy = rnd.randint(200, W - 200), rnd.randint(300, 900)
    for rad in range(700, 0, -20):
        a = int(40 * (1 - rad / 700))
        gd.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=(a // 2, int(a / 1.5), a))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(60)), 0.55)
    return _vignette(img)


def _vignette(img: Image.Image) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([-W * 0.25, -H * 0.1, W * 1.25, H * 1.1], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(220))
    black = Image.new("RGB", img.size, (0, 0, 0))
    return Image.composite(img, black, mask)


def brand_mark(d: ImageDraw.ImageDraw, x: int, y: int, size: int = 90):
    """Globe ring + meridians + '10' — the GLOBAL TOP 10 AI emblem."""
    r = size // 2
    d.ellipse([x, y, x + size, y + size], outline=GOLD, width=max(3, size // 18))
    for k in (0.45, 0.8):
        d.ellipse([x + r - r * k, y, x + r + r * k, y + size], outline=GOLD, width=2)
    d.line([x, y + r, x + size, y + r], fill=GOLD, width=2)
    f = font(int(size * 0.42))
    tw = d.textlength("10", font=f)
    d.text((x + r - tw / 2, y + r - size * 0.25), "10", font=f, fill=WHITE, stroke_width=3, stroke_fill=(0, 0, 0))


def _centered(d, text, y, f, fill=WHITE, stroke=4):
    tw = d.textlength(text, font=f)
    d.text(((W - tw) / 2, y), text, font=f, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))


def _wrap(d, text, f, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if d.textlength(test, font=f) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fmt_value(v, unit: str | None) -> str:
    if v is None:
        return ""
    if abs(v) >= 1e12:
        s = f"{v / 1e12:.2f} trillion"
    elif abs(v) >= 1e9:
        s = f"{v / 1e9:.2f} billion"
    elif abs(v) >= 1e6:
        s = f"{v / 1e6:.2f} million"
    elif float(v).is_integer():
        s = f"{int(v):,}"
    else:
        s = f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{s} {unit or ''}".strip()


def title_card(title: str, subtitle: str, out: Path, seed: int = 1) -> Path:
    img = _background(seed)
    d = ImageDraw.Draw(img)
    brand_mark(d, W // 2 - 70, 330, 140)
    _centered(d, "GLOBAL TOP 10 AI", 500, font(46), GOLD, 3)
    clean = title.replace("Top 10", "").strip()
    _centered(d, "TOP 10", 720, font(190), WHITE, 6)
    f = font(84)
    for i, line in enumerate(_wrap(d, clean.upper(), f, W - 200)[:4]):
        _centered(d, line, 960 + i * 104, f, GOLD if i == 0 else WHITE, 5)
    if subtitle:
        fs = font(40, bold=False)
        for i, line in enumerate(_wrap(d, subtitle, fs, W - 260)[:3]):
            _centered(d, line, 1250 + i * 52, fs, DIM, 2)
    img.save(out, quality=95)
    return out


def entry_card(entry: dict, ranking: dict, out: Path, variant: str = "bars") -> Path:
    """Data visualisation for one entry. variant: 'bars' (all ten, this one highlighted) | 'circles' (size vs #1)."""
    rank = entry["rank"]
    img = _background(100 + rank, hue_shift=rank * 6)
    d = ImageDraw.Draw(img)
    entries = sorted(ranking["entries"], key=lambda e: e["rank"])
    numeric = ranking["ranking_type"] == "factual" and all(isinstance(e.get("value"), (int, float)) for e in entries)
    unit = ranking.get("unit") or ""
    _centered(d, (ranking.get("metric") or "").upper(), 660, font(40), DIM, 2)
    if numeric:
        _centered(d, fmt_value(entry["value"], unit), 715, font(110), GOLD, 6)
    if numeric and variant == "bars":
        vmax = max(e["value"] for e in entries)
        top_y, bar_h, gap = 900, 36, 14
        fl = font(34)
        for i, e in enumerate(entries):
            y = top_y + i * (bar_h + gap)
            frac = max(e["value"] / vmax, 0.02)
            x0, x1 = 330, 330 + int((W - 330 - 110) * frac)
            col = GOLD if e["rank"] == rank else (60, 74, 100)
            d.rounded_rectangle([x0, y, x1, y + bar_h], radius=12, fill=col)
            label = f"#{e['rank']} {e['name']}"
            if d.textlength(label, font=fl) > 290:
                label = label[:14] + "…"
            d.text((x0 - 20 - d.textlength(label, font=fl), y - 1), label, font=fl,
                   fill=WHITE if e["rank"] == rank else DIM)
    elif numeric and variant == "circles":
        best = entries[0]
        cx, cy, rmax = W // 2, 1110, 260
        rb = rmax
        re_ = max(int(rmax * math.sqrt(entry["value"] / best["value"])), 12)
        d.ellipse([cx - rb, cy - rb, cx + rb, cy + rb], outline=(80, 96, 130), width=6)
        d.ellipse([cx - re_, cy + rb - 2 * re_, cx + re_, cy + rb], fill=GOLD, outline=WHITE, width=4)
        fs = font(36)
        _centered(d, f"vs #1 {best['name']}: {fmt_value(best['value'], unit)}", cy + rb + 40, fs, DIM, 2)
        pct = entry["value"] / best["value"] * 100
        _centered(d, f"{pct:.0f}% of the leader" if entry["rank"] != 1 else "THE LEADER", cy + rb + 100, font(48), WHITE, 3)
    else:
        f = font(54)
        lines = _wrap(d, entry.get("criteria") or ranking.get("methodology", "Editorial assessment"), f, W - 220)
        for i, line in enumerate(lines[:6]):
            _centered(d, line, 900 + i * 70, f, WHITE, 3)
        _centered(d, "EDITORIAL RANKING", 1320, font(40), GOLD, 2)
    img.save(out, quality=95)
    return out


def outro_card(text: str, out: Path) -> Path:
    img = _background(999)
    d = ImageDraw.Draw(img)
    brand_mark(d, W // 2 - 90, 560, 180)
    _centered(d, "GLOBAL TOP 10 AI", 780, font(64), GOLD, 4)
    f = font(56, bold=False)
    for i, line in enumerate(_wrap(d, text, f, W - 240)[:4]):
        _centered(d, line, 920 + i * 72, f, WHITE, 3)
    _centered(d, "A new evidence-based ranking every day", 1300, font(38, bold=False), DIM, 2)
    img.save(out, quality=95)
    return out


def thumbnail(title: str, out: Path, size=(1280, 720), seed: int = 7) -> Path:
    bg = _background(seed).resize((size[0], int(size[0] * H / W))).crop((0, 300, size[0], 300 + size[1]))
    d = ImageDraw.Draw(bg)
    brand_mark(d, 40, 40, 110)
    clean = title.replace("Top 10", "").strip().upper()
    d.text((60, 190), "TOP 10", font=font(150), fill=GOLD, stroke_width=6, stroke_fill=(0, 0, 0))
    f = font(76)
    for i, line in enumerate(_wrap(d, clean, f, size[0] - 120)[:3]):
        d.text((60, 370 + i * 90), line, font=f, fill=WHITE, stroke_width=5, stroke_fill=(0, 0, 0))
    bg.save(out, quality=92)
    return out
