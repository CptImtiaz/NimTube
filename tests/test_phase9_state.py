import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import state  # noqa: E402


def test_encrypt_roundtrip_and_tamper_detection():
    blob = state.encrypt(b"hello db", "correct horse battery staple")
    assert state.decrypt(blob, "correct horse battery staple") == b"hello db"
    with pytest.raises(Exception):
        state.decrypt(blob, "wrong passphrase!!!!")
    bad = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(Exception):
        state.decrypt(bad, "correct horse battery staple")


def test_push_pull_via_git_branch(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True)
    (repo / "x.txt").write_text("code")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], check=True)
    db = tmp_path / "data" / "global_top10.db"
    db.parent.mkdir()
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t (v)")
    c.execute("INSERT INTO t VALUES (42)")
    c.commit()
    c.close()
    monkeypatch.setattr(state, "ROOT", repo)
    monkeypatch.setattr(state, "DB", db)
    monkeypatch.setenv("STATE_PASSPHRASE", "a-long-test-passphrase-123")
    state.push()
    db.unlink()
    state.pull()
    assert sqlite3.connect(db).execute("SELECT v FROM t").fetchone()[0] == 42
    files = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "origin/bot-state"],
                           capture_output=True, text=True).stdout
    assert "state.db.enc" in files and "x.txt" not in files      # orphan branch: no code history mixed in
    raw = subprocess.run(["git", "-C", str(repo), "show", "origin/bot-state:state.db.enc"], capture_output=True).stdout
    assert b"CREATE TABLE" not in raw                             # encrypted at rest
