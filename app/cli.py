"""Command line: python -m app <command>

  run       --mode dry-run|test|private|publish [--resume JOB] [--topic "Top 10 ..."] [--mock]
  discover  [--mock]                      collect + score worldwide trends only
  analytics                               collect YouTube Analytics snapshots and print learnings
  jobs                                    list recent jobs
  check-config                            show which providers/credentials are configured (never prints values)
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import load_settings, env


def main(argv=None):
    p = argparse.ArgumentParser(prog="global-top10-ai")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--mode", default="test", choices=["dry-run", "test", "private", "publish"])
    r.add_argument("--resume", default=None, help="job id to resume")
    r.add_argument("--resume-latest", action="store_true", help="resume the latest unfinished job from today")
    r.add_argument("--topic", default=None, help="force a topic, e.g. 'Top 10 Largest Lakes in the World'")
    r.add_argument("--mock", action="store_true", help="offline fixtures (tests/demo only)")
    d = sub.add_parser("discover")
    d.add_argument("--mock", action="store_true")
    sub.add_parser("analytics")
    sub.add_parser("jobs")
    sub.add_parser("check-config")
    a = p.parse_args(argv)

    if a.cmd == "check-config":
        keys = ["ANTHROPIC_API_KEY", "ZAPIER_UPLOAD_HOOK", "S3_BUCKET", "S3_ENDPOINT", "OPENAI_API_KEY", "GEMINI_API_KEY", "YOUTUBE_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "PEXELS_API_KEY", "PIXABAY_API_KEY",
                "YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN", "REDDIT_CLIENT_ID", "STATE_PASSPHRASE",
                "NOTIFY_WEBHOOK_URL"]
        for k in keys:
            print(f"{k:22s} {'set' if env(k) else '-'}")
        s = load_settings("test")
        print("auto_publish:", s.get("publishing.auto_publish"), "| visibility:", s.get("publishing.visibility"))
        print("enabled niches:", ", ".join(s.niches))
        return 0

    from .db import DB
    if a.cmd == "run":
        from .pipeline import run, PipelineStop
        s = load_settings(a.mode, mock=a.mock or None)
        resume = a.resume
        if a.resume_latest and not resume:
            row = DB(s.db_path).one("SELECT id FROM jobs WHERE status IN ('running','failed') AND "
                                    "created_at > strftime('%s','now','-20 hours') ORDER BY created_at DESC LIMIT 1")
            resume = row["id"] if row else None
        try:
            res = run(s, resume_job=resume, forced_topic=a.topic)
            print(json.dumps({k: res.get(k) for k in ("job_id", "status", "topic", "video", "upload", "cost_usd_today")},
                             indent=2, default=str))
            return 0
        except PipelineStop as e:
            print(f"STOPPED: {e}", file=sys.stderr)
            return 3
    if a.cmd == "discover":
        from .trend_discovery.engine import discover
        s = load_settings("dry-run", mock=a.mock or None)
        for t in discover(s, DB(s.db_path))[:30]:
            flag = "GLOBAL " if t["is_global"] else "regional"
            print(f"{t['score']:8.1f}  {flag}  {t['n_countries']:2d} countries  {t['keyword']}")
        return 0
    if a.cmd == "analytics":
        from .analytics.collector import collect, learnings
        s = load_settings("test")
        db = DB(s.db_path)
        print("snapshots stored:", collect(s, db))
        print(json.dumps(learnings(db), indent=2))
        return 0
    if a.cmd == "jobs":
        s = load_settings("test")
        for j in DB(s.db_path).query("SELECT id, status, stage, topic FROM jobs ORDER BY created_at DESC LIMIT 20"):
            print(f"{j['id']}  {j['status']:<30} {j['stage'] or '':<10} {j['topic'] or ''}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
