"""Orchestrator: trends -> topic -> research -> ranking -> script -> voice -> visuals -> render -> SEO -> QC ->
upload -> report. Each stage's result is persisted in the jobs table, so an interrupted run resumes where it
stopped (`--resume`). Modes: dry-run | test | private | publish (see app/config.py)."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
import traceback
import uuid
from pathlib import Path

from . import http
from .analytics.collector import learnings
from .config import Settings, env
from .copyright.provenance import attribution_lines, pick_music, register_asset, register_music, uses_synthetic_media
from .db import DB
from .fact_checking.checker import check_script
from .llm import LLM
from .log import get_logger, redact
from .quality_control.checks import run_quality_checks
from .ranking.engine import RankingRejected, build_ranking
from .research.engine import research_topic
from .script_generation.writer import write_script
from .thumbnail.seo import build_metadata
from .topic_selection.selector import select_topic
from .trend_discovery.engine import discover
from .video_editing.render import render_video
from .visual_generation.planner import plan_visuals
from .voice_generation.tts import synthesize

logger = get_logger("pipeline")
STAGES = ["trends", "topic", "research", "ranking", "script", "voice", "visuals", "render", "metadata", "qc",
          "upload"]


class PipelineStop(RuntimeError):
    """Production stopped by a safety gate (not a crash)."""


class _Lock:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.f = open(self.path, "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PipelineStop("another production run holds the lock")
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def _jsonable(obj):
    return json.loads(json.dumps(obj, default=str))


def notify(text: str):
    url = env("NOTIFY_WEBHOOK_URL")
    if not url:
        return
    try:
        http.request("POST", url, json={"text": text, "content": text[:1900]}, timeout=15, retries=1)
    except Exception as e:
        logger.warning(f"notification failed: {e}")


def run(s: Settings, resume_job: str | None = None, forced_topic: str | None = None, slot_utc: str | None = None) -> dict:
    db = DB(s.db_path)
    with _Lock(s.data_dir / ".production.lock"):
        job_id = resume_job or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        job = db.get_job(job_id)
        if resume_job and not job:
            raise PipelineStop(f"job {resume_job} not found")
        if not job:
            db.save_job(job_id, status="running", mode=s.mode, stage="start", data={"slot_utc": slot_utc})
            job = db.get_job(job_id)
        data: dict = job["data"]
        work = s.output_dir / job_id
        work.mkdir(parents=True, exist_ok=True)
        timings: dict = data.get("timings", {})
        result = {"job_id": job_id, "mode": s.mode, "work_dir": str(work)}

        def done(stage):
            return stage in data and data[stage] is not None

        def save(stage, value):
            data[stage] = _jsonable(value)
            data["timings"] = timings
            db.save_job(job_id, stage=stage, data={stage: data[stage], "timings": timings})

        def timed(stage, fn):
            t0 = time.time()
            out = fn()
            timings[stage] = round(time.time() - t0, 2)
            return out

        try:
            if s.mode in ("private", "publish") and not resume_job:
                today = db.one("SELECT COUNT(*) c FROM uploads WHERE status='done' AND "
                               "created_at > strftime('%s','now','start of day')")["c"]
                target = int(s.get("production.videos_per_day", 1))
                if today >= target:
                    raise PipelineStop(f"daily target reached ({today}/{target} uploads today) — not producing more")
            llm = LLM(s, db)
            if not done("trends"):
                trends = timed("trends", lambda: discover(s, db))
                save("trends", trends[:60])
            trends = data["trends"]

            attempts, failed_titles = 0, list(data.get("failed_topics", []))
            while not done("ranking"):
                attempts += 1
                if attempts > 3:
                    raise PipelineStop("three topics failed research; nothing publishable today")
                if not done("topic"):
                    if forced_topic and attempts == 1:
                        topic = {"title": forced_topic, "niche": "general", "origin": "manual",
                                 "ranking_type": "factual", "metric": "", "unit": "", "score": None,
                                 "trend_scope": "manual"}
                        topic = _complete_manual_topic(s, llm, topic)
                    else:
                        topic = timed("topic", lambda: select_topic(s, db, llm, trends, job_id, learnings(db),
                                                                    exclude_titles=failed_titles))
                    save("topic", topic)
                    db.save_job(job_id, topic=topic["title"])
                topic = data["topic"]
                if not done("research"):
                    save("research", timed("research", lambda: research_topic(s, db, llm, topic, job_id)))
                try:
                    save("ranking", timed("ranking", lambda: build_ranking(s, topic, data["research"])))
                except RankingRejected as e:
                    logger.warning(f"topic rejected after research: {topic['title']}: {e}")
                    db.execute("UPDATE topics SET rejected_reason=?, selected=0 WHERE job_id=? AND title=?",
                               (f"research: {e}", job_id, topic["title"]))
                    failed_titles.append(topic["title"])
                    data["failed_topics"] = failed_titles
                    data["topic"] = data["research"] = None
                    db.save_job(job_id, data={"topic": None, "research": None, "failed_topics": failed_titles})
                    forced_topic = None
            ranking = data["ranking"]

            if not done("script"):
                feedback, script, problems = "", None, []
                for attempt in range(3):
                    script = write_script(s, llm, ranking, feedback)
                    problems = check_script(s, script, ranking)
                    if not problems:
                        break
                    feedback = "\n".join(problems)
                    logger.warning(f"script review failed (attempt {attempt + 1}): {problems[:5]}")
                if problems:
                    raise PipelineStop("script failed fact-check 3 times: " + "; ".join(problems[:8]))
                save("script", script)
            script = data["script"]

            if s.mode == "dry-run":
                db.save_job(job_id, status="dry-run-complete", stage="script")
                result.update(status="dry-run-complete", topic=data["topic"]["title"])
                return _finish(s, db, job_id, data, result, work)

            if not done("voice"):
                def voice_all():
                    texts = {"intro": f"{script['hook']} {script['intro']}"}
                    for seg in script["segments"]:
                        texts[f"seg{seg['rank']}"] = seg["narration"]
                    texts["outro"] = f"{script.get('ending', '')} {script.get('subscribe', '')}".strip()
                    voices = {}
                    for k, t in texts.items():
                        v = synthesize(s, t, work / f"voice_{k}.wav")
                        register_asset(s, db, job_id, kind="voice", path=v["path"], source=v["provider"],
                                       creator=v["provider"], proof={"provider": v["provider"], "text_sha256": hashlib.sha256(t.encode()).hexdigest()})
                        voices[k] = v
                    return voices
                save("voice", timed("voice", voice_all))
            voices = data["voice"]

            if not done("visuals"):
                save("visuals", timed("visuals", lambda: plan_visuals(s, db, job_id, ranking, script, work / "visuals")))
            visuals = data["visuals"]
            visuals["segments"] = {int(k): v for k, v in visuals["segments"].items()}

            if not done("render"):
                niche = s.niches.get(data["topic"].get("niche"), {})
                track = pick_music(s, niche.get("music_mood", "cinematic"))
                if track:
                    register_music(s, db, job_id, track)
                save("render", timed("render", lambda: render_video(s, ranking, script, voices, visuals,
                                                                     work / "render", track)))
                data["render"]["music"] = track["file"] if track else None
            render = data["render"]
            video = Path(render["video"])

            if not done("metadata"):
                from .youtube_upload.publisher import idempotency_key
                meta = build_metadata(ranking, script, data["topic"].get("niche", "general"),
                                      attribution_lines(db, job_id), uses_synthetic_media(db, job_id),
                                      idempotency_key(job_id, video), work)
                save("metadata", meta)
            meta = data["metadata"]

            qc_mode = s.mode
            if s.mode == "queue":
                qc_mode = "publish" if s.get("publishing.auto_publish", False) else "private"
            qc = timed("qc", lambda: run_quality_checks(s, db, job_id, video=video, ranking=ranking, script=script,
                                                        voices=voices, visuals=visuals, metadata=meta, mode=qc_mode))
            save("qc", qc)
            if not qc["passed"]:
                (work / "ERROR_REPORT.md").write_text("# Quality gate failed\n\n" + "\n".join(
                    f"- {f}" for f in qc["failures"]), encoding="utf-8")
                raise PipelineStop("quality/copyright gate failed: " + "; ".join(qc["failures"][:10]))

            if s.mode == "queue":
                if not done("upload"):
                    from .youtube_upload.queue import enqueue
                    slot = data.get("slot_utc") or slot_utc
                    if not slot:
                        raise PipelineStop("queue mode needs a publish slot")
                    save("upload", timed("upload", lambda: enqueue(s, db, job_id, video, meta, slot)))
                result["upload"] = data["upload"]
                status = f"queued for {data['upload'].get('slot_utc')}"
            elif s.mode in ("private", "publish"):
                if not done("upload"):
                    backend = env("UPLOAD_BACKEND") or s.get("publishing.backend", "zapier")
                    if backend == "zapier":
                        from .youtube_upload.zapier import upload_via_zapier
                        save("upload", timed("upload", lambda: upload_via_zapier(s, db, job_id, s.mode, video, meta)))
                    else:
                        from .youtube_upload.publisher import upload
                        save("upload", timed("upload", lambda: upload(s, db, job_id, s.mode, video, meta,
                                                                      Path(render["srt"]))))
                result["upload"] = data["upload"]
                status = ("sent to Zapier" if data["upload"].get("status") in ("sent_to_zapier", "already_sent")
                          else "published" if data["upload"].get("privacy") == "public" else "uploaded")
            else:
                status = "rendered (test mode, not uploaded)"
            db.save_job(job_id, status=status, stage="done")
            result.update(status=status, topic=data["topic"]["title"], video=str(video))
            return _finish(s, db, job_id, data, result, work)

        except PipelineStop as e:
            db.save_job(job_id, status="stopped", error=redact(str(e)), data={"timings": timings})
            result.update(status="stopped", error=redact(str(e)))
            notify(f"GLOBAL TOP 10 AI stopped ({job_id}): {redact(str(e))[:500]}")
            _finish(s, db, job_id, data, result, work)
            raise
        except Exception as e:
            db.save_job(job_id, status="failed", error=redact(traceback.format_exc()[-3000:]), data={"timings": timings})
            result.update(status="failed", error=redact(str(e)))
            notify(f"GLOBAL TOP 10 AI failed ({job_id}): {redact(str(e))[:500]}")
            _finish(s, db, job_id, data, result, work)
            raise


def _complete_manual_topic(s: Settings, llm: LLM, topic: dict) -> dict:
    resp = llm.complete_json("manual_topic", "You define ranking methodologies for an evidence-based channel.",
                             f'Topic: "{topic["title"]}". Return JSON {{"ranking_type": "factual|editorial", '
                             f'"metric": "...", "unit": "...", "authoritative_sources": "...", "niche": "one of '
                             f'{", ".join(s.niches)}"}}')
    topic.update({k: resp.get(k, topic.get(k)) for k in ("ranking_type", "metric", "unit", "authoritative_sources",
                                                          "niche")})
    return topic


def _finish(s: Settings, db: DB, job_id: str, data: dict, result: dict, work: Path) -> dict:
    day = time.strftime("%Y-%m-%d", time.gmtime())
    usage = db.query("SELECT provider, units, cost_usd FROM api_usage WHERE day=?", (day,))
    result["cost_usd_today"] = round(sum(u["cost_usd"] for u in usage), 4)
    result["api_usage_today"] = usage
    topic = data.get("topic") or {}
    ranking = data.get("ranking") or {}
    qc = data.get("qc") or {}
    lines = [f"# GLOBAL TOP 10 AI — run report `{job_id}`", "",
             f"- **Mode:** {s.mode}{' (MOCK DATA)' if s.mock else ''}", f"- **Status:** {result.get('status')}",
             f"- **Topic:** {topic.get('title', '-')} (score {topic.get('score')}, {topic.get('trend_scope', '-')})"]
    if result.get("error"):
        lines.append(f"- **Stopped/failed because:** {result['error'][:800]}")
    if ranking:
        lines += [f"- **Ranking:** {ranking.get('label')} — {ranking.get('methodology', '')}",
                  f"- **Independent source domains:** {', '.join(ranking.get('domains', []))}"]
        lines += ["", "| # | Entry | Value | Evidence |", "|---|---|---|---|"]
        for e in sorted(ranking.get("entries", []), key=lambda x: x["rank"]):
            lines.append(f"| {e['rank']} | {e['name']} | {e.get('value')} {e.get('unit') or ''} | "
                         f"{len(e['evidence'])} verified ({', '.join(e['evidence_domains'])}) |")
    if data.get("render"):
        lines += ["", f"- **Video:** {data['render']['video']} ({data['render']['duration']:.1f}s, music: "
                      f"{data['render'].get('music') or 'none'})"]
    if qc:
        lines += [f"- **Quality gate:** {'PASSED' if qc.get('passed') else 'FAILED'}"]
        lines += [f"  - {f}" for f in qc.get("failures", [])]
    if data.get("upload"):
        u = data["upload"]
        lines += [f"- **YouTube:** {u.get('url', u.get('youtube_id'))} — {u.get('privacy')} {u.get('publish_at') or ''} "
                  f"({u.get('status')})"]
    sc = data.get("script") or {}
    if sc:
        lines += ["", "### Script", "", f"> {sc.get('hook', '')} {sc.get('intro', '')}"]
        lines += [f"> **#{seg.get('rank')}** {seg.get('narration', '')}" for seg in sc.get("segments", [])]
        lines += [f"> {sc.get('ending', '')} {sc.get('subscribe', '')}", ""]
    lines += [f"- **Stage timings (s):** {json.dumps(data.get('timings', {}))}",
              f"- **Estimated API cost today:** ${result['cost_usd_today']}"]
    report = "\n".join(lines) + "\n"
    (work / "report.md").write_text(report, encoding="utf-8")
    (work / "report.json").write_text(json.dumps(_jsonable(result), indent=2), encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(report)
    return result
