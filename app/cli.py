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
    n = sub.add_parser("nightly", help="produce one video per open publish slot (default 3) and queue them")
    n.add_argument("--mock", action="store_true")
    sub.add_parser("publish", help="send due queued videos to Zapier and clean up old hosted files")
    sub.add_parser("queue", help="show the publish queue")
    sub.add_parser("analytics")
    sub.add_parser("jobs")
    sub.add_parser("check-config")
    sub.add_parser("check-apis", help="make one tiny live request to every configured API")
    a = p.parse_args(argv)

    if a.cmd == "check-apis":
        from .healthcheck import report, run_checks
        rows, ok = run_checks(load_settings("test"))
        print(report(rows))
        import os
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write("## API health check\n\n" + report(rows) + "\n")
        return 0 if ok else 1

    if a.cmd == "check-config":
        keys = ["ANTHROPIC_API_KEY", "ZAPIER_UPLOAD_HOOK", "S3_BUCKET", "S3_ENDPOINT", "OPENAI_API_KEY", "GEMINI_API_KEY", "YOUTUBE_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "PEXELS_API_KEY", "PIXABAY_API_KEY", "COVERR_API_KEY", "HF_TOKEN", "OMNIROUTE_BASE_URL", "OMNIROUTE_API_KEY", "TAVILY_API_KEY", "NVIDIA_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
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
    if a.cmd == "nightly":
        from .pipeline import run, PipelineStop
        from .youtube_upload.queue import open_slots
        s = load_settings("queue", mock=a.mock or None)
        db = DB(s.db_path)
        made, failed = [], []
        # resume an interrupted job first (it keeps its slot)
        row = db.one("SELECT id, data FROM jobs WHERE mode='queue' AND status IN ('running','failed') AND "
                     "created_at > strftime('%s','now','-20 hours') ORDER BY created_at DESC LIMIT 1")
        todo = []
        if row:
            todo.append((row["id"], json.loads(row["data"] or "{}").get("slot_utc")))
        taken = {slot for _, slot in todo}
        todo += [(None, slot) for slot in open_slots(s, db) if slot not in taken]
        for resume, slot in todo:
            try:
                res = run(s, resume_job=resume, slot_utc=slot)
                made.append({"slot": slot, "topic": res.get("topic"), "status": res.get("status")})
            except PipelineStop as e:
                failed.append({"slot": slot, "error": str(e)[:300]})
            except Exception as e:                       # one bad video must not stop the other slots
                failed.append({"slot": slot, "error": f"crash: {str(e)[:300]}"})
        print(json.dumps({"made": made, "failed": failed}, indent=2))
        return 0 if made or not todo else 4
    if a.cmd == "publish":
        from .youtube_upload.queue import publish_due, cleanup_hosting
        s = load_settings("queue")
        db = DB(s.db_path)
        sent = publish_due(s, db)
        print(json.dumps({"sent": sent, "hosting_cleaned": cleanup_hosting(s)}, indent=2))
        return 0 if all(x["status"] == "sent" for x in sent) else 5
    if a.cmd == "queue":
        s = load_settings("queue")
        for r in DB(s.db_path).query("SELECT slot_utc, status, job_id, error FROM publish_queue ORDER BY slot_utc DESC LIMIT 15"):
            print(f"{r['slot_utc']}  {r['status']:<8} {r['job_id']}  {r['error'] or ''}")
        return 0
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
