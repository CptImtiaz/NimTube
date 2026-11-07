"""Synthesises an ORIGINAL ambient cinematic pad with FFmpeg (no samples, no third-party audio) and registers
it in assets/music/manifest.yaml with licence 'Original'. Usage: python scripts/make_original_music.py"""
import datetime
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MUSIC = ROOT / "assets" / "music"
OUT = MUSIC / "original_ambient_pad.mp3"

# A-minor pad (A2 drone + A3/C4/E4 chord + slow-moving fifth), gentle tremolo, echo for space.
voices = [(110.0, 0.30), (220.0, 0.16), (261.63, 0.12), (329.63, 0.12), (164.81, 0.10), (440.0, 0.04)]
inputs, labels = [], []
for i, (f, amp) in enumerate(voices):
    inputs += ["-f", "lavfi", "-i", f"sine=frequency={f}:sample_rate=48000:duration=64"]
    labels.append(f"[{i}:a]volume={amp},tremolo=f={0.12 + i * 0.05:.2f}:d=0.35[v{i}]")
mix = "".join(f"[v{i}]" for i in range(len(voices)))
fc = ";".join(labels) + f";{mix}amix=inputs={len(voices)}:normalize=0,lowpass=f=1800," \
     "aecho=0.8:0.7:420|780:0.35|0.25,afade=t=in:d=3,afade=t=out:st=60:d=4,aformat=channel_layouts=stereo[a]"
MUSIC.mkdir(parents=True, exist_ok=True)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", fc, "-map", "[a]",
                "-c:a", "libmp3lame", "-b:a", "160k", str(OUT)], check=True)

manifest_path = MUSIC / "manifest.yaml"
data = yaml.safe_load(manifest_path.read_text()) if manifest_path.exists() else {}
data = data or {}
tracks = [t for t in (data.get("tracks") or []) if t.get("file") != OUT.name]
tracks.append({"file": OUT.name, "title": "Original Ambient Pad", "creator": "GLOBAL TOP 10 AI (synthesised)",
               "licence": "Original", "licence_url": "", "source_url": "scripts/make_original_music.py",
               "proof": f"Generated locally from sine oscillators by scripts/make_original_music.py on "
                        f"{datetime.date.today().isoformat()}; contains no third-party audio.",
               "attribution": "", "commercial_use": True, "moods": ["cinematic", "ambient"]})
data["tracks"] = tracks
header = "\n".join(l for l in manifest_path.read_text().splitlines() if l.startswith("#")) if manifest_path.exists() else ""
manifest_path.write_text(header + "\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
print(f"wrote {OUT} and registered it in {manifest_path}")
