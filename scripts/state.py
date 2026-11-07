"""Persist the SQLite database between GitHub Actions runs — encrypted.

The database is encrypted with AES-256-GCM (key derived from STATE_PASSPHRASE with scrypt) and committed to an
orphan branch `bot-state` (never mixed with code history). Daily backups are kept for 14 days.

  python scripts/state.py pull   # restore database/global_top10.db (no-op on first run)
  python scripts/state.py push   # encrypt + commit + push the current database
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("DATA_DIR", ROOT / "database")) / "global_top10.db"
BRANCH = os.environ.get("STATE_BRANCH", "bot-state")
MAGIC = b"GT10v1"
KEEP_BACKUPS = 14


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode())


def encrypt(data: bytes, passphrase: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_key(passphrase, salt)).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes, passphrase: str) -> bytes:
    if not blob.startswith(MAGIC):
        raise ValueError("not a GT10 state file")
    salt, nonce, ct = blob[6:22], blob[22:34], blob[34:]
    return AESGCM(_key(passphrase, salt)).decrypt(nonce, ct, MAGIC)


def git(*args, cwd=None, check=True) -> subprocess.CompletedProcess:
    p = subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} failed: {p.stderr.strip()[:300]}")
    return p


def passphrase() -> str:
    p = os.environ.get("STATE_PASSPHRASE", "")
    if len(p) < 16:
        sys.exit("STATE_PASSPHRASE secret missing or shorter than 16 characters")
    return p


def pull():
    pw = passphrase()
    if git("fetch", "origin", f"{BRANCH}:refs/remotes/origin/{BRANCH}", check=False).returncode != 0:
        print(f"no '{BRANCH}' branch yet — starting with an empty database")
        return
    blob = subprocess.run(["git", "show", f"origin/{BRANCH}:state.db.enc"], cwd=ROOT, capture_output=True).stdout
    if not blob:
        print("state branch has no database yet")
        return
    DB.parent.mkdir(parents=True, exist_ok=True)
    DB.write_bytes(decrypt(blob, pw))
    sqlite3.connect(DB).execute("PRAGMA integrity_check").fetchone()
    print(f"restored database ({DB.stat().st_size // 1024} KB)")


def push():
    pw = passphrase()
    if not DB.exists():
        print("no database to save")
        return
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "snap.db"
        src = sqlite3.connect(DB)
        dst = sqlite3.connect(snap)
        src.backup(dst)
        dst.close()
        src.close()
        blob = encrypt(snap.read_bytes(), pw)

        wt = Path(tmp) / "wt"
        exists = git("ls-remote", "--exit-code", "--heads", "origin", BRANCH, check=False).returncode == 0
        if exists:
            git("fetch", "origin", f"{BRANCH}:refs/remotes/origin/{BRANCH}")
            git("worktree", "add", "--detach", str(wt), f"origin/{BRANCH}")
        else:
            git("worktree", "add", "--detach", str(wt))
            git("checkout", "--orphan", BRANCH, cwd=wt)
            git("rm", "-rf", "--quiet", ".", cwd=wt, check=False)
            for p in wt.iterdir():
                if p.name != ".git":
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
        (wt / "state.db.enc").write_bytes(blob)
        backups = wt / "backups"
        backups.mkdir(exist_ok=True)
        (backups / f"{time.strftime('%Y-%m-%d')}.db.enc").write_bytes(blob)
        for old in sorted(backups.glob("*.db.enc"))[:-KEEP_BACKUPS]:
            old.unlink()
        (wt / "README.md").write_text("Encrypted runtime state for GLOBAL TOP 10 AI. Do not edit by hand.\n")
        git("add", "-A", cwd=wt)
        git("-c", "user.name=gt10-bot", "-c", "user.email=gt10-bot@users.noreply.github.com",
            "commit", "-m", f"state {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}", cwd=wt, check=False)
        git("push", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=wt)
        git("worktree", "remove", "--force", str(wt), check=False)
    print("state saved")


if __name__ == "__main__":
    {"pull": pull, "push": push}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
