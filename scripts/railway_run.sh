#!/bin/sh
# Railway cron entrypoint: produce today's video, resume once on a transient failure, then exit.
# Persistent data lives on the Railway volume mounted at /data (DATA_DIR / OUTPUT_DIR).
set -u
MODE="${PRODUCTION_MODE:-private}"
echo "GLOBAL TOP 10 AI — mode=$MODE — $(date -u)"
python -m app check-config
python -m app analytics || echo "analytics skipped"

python -m app run --mode "$MODE" --resume-latest
code=$?
if [ $code -ne 0 ] && [ $code -ne 3 ]; then
  echo "run failed (code $code) — resuming once in 60s"
  sleep 60
  python -m app run --mode "$MODE" --resume-latest
  code=$?
fi

# keep the volume small: delete rendered job folders older than KEEP_DAYS (default 7)
find "${OUTPUT_DIR:-/data/output}" -mindepth 1 -maxdepth 1 -type d -mtime +"${KEEP_DAYS:-7}" -exec rm -rf {} + 2>/dev/null
python -c "import sqlite3,os;from pathlib import Path;d=Path(os.environ.get('DATA_DIR','/data'));src=sqlite3.connect(d/'global_top10.db');b=sqlite3.connect(d/'backup.db');src.backup(b);print('db backup ok')" || true

[ $code -eq 3 ] && exit 0   # stopped by a safety gate: not a crash (see report.md / logs)
exit $code
