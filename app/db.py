"""SQLite persistence (WAL). Schema is plain SQL with numbered migrations, so moving to PostgreSQL
means re-running the same DDL (types are portable: TEXT, INTEGER, REAL)."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS: list[str] = [
    # 1 — core
    """
    CREATE TABLE IF NOT EXISTS trend_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collected_at REAL NOT NULL, source TEXT NOT NULL, country TEXT, title TEXT NOT NULL,
        channel TEXT, url TEXT, views INTEGER, likes INTEGER, published_at TEXT, category TEXT,
        keywords TEXT, traffic INTEGER, raw TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_signals_time ON trend_signals(collected_at);

    CREATE TABLE IF NOT EXISTS trend_keywords (
        day TEXT NOT NULL, keyword TEXT NOT NULL, countries INTEGER, mentions INTEGER, score REAL,
        PRIMARY KEY (day, keyword)
    );

    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, created_at REAL, updated_at REAL, mode TEXT, status TEXT,
        stage TEXT, topic TEXT, data TEXT, error TEXT
    );

    CREATE TABLE IF NOT EXISTS topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, created_at REAL, title TEXT, niche TEXT,
        source TEXT, score REAL, selected INTEGER, reasons TEXT, rejected_reason TEXT, fingerprint TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_topics_fp ON topics(fingerprint);

    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, url TEXT, domain TEXT, title TEXT,
        published TEXT, fetched_at REAL, status TEXT, content_sha256 TEXT
    );

    CREATE TABLE IF NOT EXISTS facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, entry_rank INTEGER, entry_name TEXT,
        claim TEXT, value REAL, unit TEXT, quote TEXT, source_url TEXT, verified INTEGER, note TEXT
    );

    CREATE TABLE IF NOT EXISTS assets (
        asset_id TEXT PRIMARY KEY, job_id TEXT, kind TEXT, path TEXT, sha256 TEXT, source TEXT,
        creator TEXT, licence_name TEXT, licence_url TEXT, downloaded_at REAL, commercial_use INTEGER,
        attribution TEXT, modifications_allowed INTEGER, proof TEXT, restrictions TEXT,
        synthetic INTEGER, status TEXT, reject_reason TEXT
    );

    CREATE TABLE IF NOT EXISTS uploads (
        idempotency_key TEXT PRIMARY KEY, job_id TEXT, video_sha256 TEXT, status TEXT,
        youtube_id TEXT, privacy TEXT, publish_at TEXT, created_at REAL, updated_at REAL, error TEXT
    );

    CREATE TABLE IF NOT EXISTS analytics (
        youtube_id TEXT, snapshot TEXT, collected_at REAL, metrics TEXT,
        PRIMARY KEY (youtube_id, snapshot)
    );

    CREATE TABLE IF NOT EXISTS api_usage (
        day TEXT, provider TEXT, units REAL, cost_usd REAL, PRIMARY KEY (day, provider)
    );
    """,
]


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self):
        self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (v INTEGER)")
        row = self.conn.execute("SELECT MAX(v) FROM schema_version").fetchone()
        current = row[0] or 0
        for i, sql in enumerate(MIGRATIONS, start=1):
            if i > current:
                with self.tx():
                    for stmt in _split(sql):
                        self.conn.execute(stmt)
                    self.conn.execute("INSERT INTO schema_version VALUES (?)", (i,))

    @contextmanager
    def tx(self):
        self.conn.execute("BEGIN")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def execute(self, sql: str, params: tuple = ()):
        return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        r = self.conn.execute(sql, params).fetchone()
        return dict(r) if r else None

    # ---- jobs ----
    def save_job(self, job_id: str, *, status: str | None = None, stage: str | None = None, mode: str | None = None,
                 topic: str | None = None, data: dict | None = None, error: str | None = None):
        now = time.time()
        cur = self.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not cur:
            self.execute("INSERT INTO jobs (id, created_at, updated_at, mode, status, stage, topic, data, error) "
                         "VALUES (?,?,?,?,?,?,?,?,?)",
                         (job_id, now, now, mode, status or "created", stage, topic, json.dumps(data or {}), error))
            return
        merged = json.loads(cur["data"] or "{}")
        if data:
            merged.update(data)
        self.execute("UPDATE jobs SET updated_at=?, mode=COALESCE(?,mode), status=COALESCE(?,status), "
                     "stage=COALESCE(?,stage), topic=COALESCE(?,topic), data=?, error=? WHERE id=?",
                     (now, mode, status, stage, topic, json.dumps(merged, default=str), error, job_id))

    def get_job(self, job_id: str) -> dict | None:
        j = self.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if j:
            j["data"] = json.loads(j["data"] or "{}")
        return j

    def add_usage(self, provider: str, units: float = 0, cost: float = 0):
        day = time.strftime("%Y-%m-%d", time.gmtime())
        self.execute("INSERT INTO api_usage VALUES (?,?,?,?) ON CONFLICT(day, provider) DO UPDATE SET "
                     "units=units+excluded.units, cost_usd=cost_usd+excluded.cost_usd", (day, provider, units, cost))

    def backup(self, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        b = sqlite3.connect(dest)
        with b:
            self.conn.backup(b)
        b.close()


def _split(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]
