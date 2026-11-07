"""Original sound effects, synthesised with FFmpeg (no third-party samples, so no licence risk).
whoosh : band-passed noise sweep with a fast swell — used on every entry transition
impact : low sine thump + short noise click — used under the hook teaser and the #1 reveal
riser  : rising tone — used under the top-3 re-hook"""
from __future__ import annotations

import subprocess
from pathlib import Path

SFX = {
    "whoosh": ("anoisesrc=d=0.6:c=pink:a=0.9", "highpass=f=300,lowpass=f=5000,"
               "volume='if(lt(t,0.3),t/0.3,max(0,(0.6-t)/0.3))':eval=frame,afade=t=out:st=0.45:d=0.15,volume=7dB"),
    "impact": ("sine=f=55:d=0.9", "volume='exp(-6*t)':eval=frame,aecho=0.6:0.4:40:0.25,"
               "lowpass=f=900,volume=20dB,alimiter=limit=0.7"),
    "riser": ("sine=f=220:d=1.2", "vibrato=f=7:d=0.3,volume='t/1.2':eval=frame,highpass=f=150,"
              "afade=t=out:st=1.05:d=0.15,volume=16dB"),
}


def make_sfx(work: Path) -> dict[str, Path]:
    work.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, (src, af) in SFX.items():
        p = work / f"sfx_{name}.wav"
        if not p.exists():
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src, "-af", af,
                            "-ar", "48000", "-ac", "2", str(p)], check=True, capture_output=True)
        out[name] = p
    return out
