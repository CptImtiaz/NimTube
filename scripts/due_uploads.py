"""Helper for the scheduled Claude upload task (publishing.backend: claude_zapier).

  python scripts/due_uploads.py list                 -> JSON list of videos due now that were not uploaded yet
  python scripts/due_uploads.py mark KEY VIDEO_ID    -> record an upload (prevents duplicates)
  python scripts/due_uploads.py unknown KEY NOTE     -> record an upload whose result is unknown (never retried)

Videos + manifests live in the public `queue-YYYYMMDD` GitHub releases made by the nightly workflow.
The upload log is `uploaded.json` on the `upload-log` branch (updated with git push). Uses `gh` + `git`.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = "CptImtiaz/NimTube"
BRANCH = "upload-log"
LOG = "uploaded.json"
EARLY_MIN = 40          # a video may be uploaded up to this many minutes before its slot
LATE_HOURS = 20         # ...and up to this many hours after (a missed slot is caught by the next run)


def gh(*args: str, input_: str | None = None) -> str:
    p = subprocess.run(["gh", "api", *args], capture_output=True, text=True, input=input_)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:300])
    return p.stdout


WORK = Path(tempfile.gettempdir()) / "nimtube-upload-log"
REMOTE = f"https://github.com/{REPO}.git"


def git(*args: str, cwd: Path | None = None) -> str:
    p = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd or WORK)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:300])
    return p.stdout


def _checkout() -> Path:
    """Fresh checkout of the upload-log branch (created as an empty orphan branch if missing)."""
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    git("init", "-q")
    git("remote", "add", "origin", REMOTE)
    try:
        git("fetch", "-q", "--depth", "1", "origin", BRANCH)
        git("checkout", "-q", "-b", BRANCH, "FETCH_HEAD")
    except RuntimeError:
        git("checkout", "-q", "--orphan", BRANCH)
    return WORK


def read_log() -> dict:
    f = _checkout() / LOG
    return json.loads(f.read_text()) if f.exists() else {}


def write_log(log: dict, msg: str):
    (WORK / LOG).write_text(json.dumps(log, indent=2))
    git("add", LOG)
    git("-c", "user.name=nimtube-upload-bot", "-c", "user.email=bot@users.noreply.github.com", "commit", "-q", "-m", msg)
    git("push", "-q", "origin", f"HEAD:{BRANCH}")


def due(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    log = read_log()
    out = []
    for rel in json.loads(gh(f"repos/{REPO}/releases?per_page=30")):
        if not rel.get("tag_name", "").startswith("queue-"):
            continue
        assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
        for name, url in assets.items():
            if not name.endswith(".json"):
                continue
            with urllib.request.urlopen(url, timeout=30) as r:
                m = json.loads(r.read().decode())
            if m.get("idempotency_key") in log:
                continue
            slot = datetime.fromisoformat(m["slot_utc"].replace("Z", "+00:00"))
            if slot - timedelta(minutes=EARLY_MIN) <= now <= slot + timedelta(hours=LATE_HOURS):
                m["thumbnail_url"] = m.get("thumbnail_url") or assets.get(name.replace(".json", "-thumbnail.jpg"), "")
                out.append(m)
    return sorted(out, key=lambda m: m["slot_utc"])


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "list":
        items = due()
        print(json.dumps([{k: m.get(k) for k in ("idempotency_key", "slot_utc", "title", "description", "tags",
                                                  "privacy", "category_id", "made_for_kids", "video_url",
                                                  "thumbnail_url", "contains_synthetic_media")} for m in items],
                         indent=2, ensure_ascii=False))
        return 0
    if cmd in ("mark", "unknown") and len(argv) >= 4:
        log = read_log()
        if argv[2] in log:
            print(f"already recorded: {log[argv[2]]}")
            return 0
        log[argv[2]] = {"status": "uploaded" if cmd == "mark" else "unknown", "result": argv[3],
                        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        write_log(log, f"{cmd} {argv[2]}")
        print("recorded")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
