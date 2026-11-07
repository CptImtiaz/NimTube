# BUILD REPORT — GLOBAL TOP 10 AI (MVP)

Build date: 2026-11-07 · Python 3.12/3.13 · ≈3,400 lines of application code + ≈500 lines of tests.
This report states plainly what works, what was tested, and what was **not** possible to test in the build
environment.

## Update — Gemini free tier as main AI (2026-11-07)

* Provider order is now Gemini → Claude → OpenAI. The Gemini model is auto-selected from the key's ListModels result
  (newest stable Flash), because older IDs such as gemini-2.5 were retired; `GEMINI_MODEL` forces an exact ID.
  Calls are paced (6 s apart) for the free tier's low per-minute limit; thinking parts are ignored.
* `videos_per_day: 1` for the free tier; slots are in priority order, so 1 video goes to the Americas slot.
* Tests: **54 passed**. Not live-tested with a real Gemini key.

## Update — GitHub Actions nightly batch, 3 videos/day (2026-11-07)

* Railway removed. **`nightly.yml`** (14:00 UTC = 22:00 MYT) produces one video per open slot (3), each with a
  different niche where possible; a failed video doesn't stop the others; interrupted jobs resume next night.
* **`publish.yml`** runs at 17:30 / 23:30 / 11:30 UTC (Europe / Americas / Asia evening prime time) and sends the
  due video to Zapier for an immediate upload (works without YouTube Partner status). Sent at most once.
* Videos are hosted as release assets of the public repo (temporary `queue-*` pre-releases, deleted after 3 days).
* Tests: **51 passed**.

## Update — Claude API + Zapier (2026-11-07)

* **Claude is now the primary LLM** (`claude-sonnet-5-5` via the Messages API). Research uses Claude's server-side
  web search tool (`web_search_20250305`) and follows `pause_turn`; citations and search results are collected and
  every quote is still independently verified by fetching the page. OpenAI/Gemini remain optional fallbacks.
* **Uploading goes through Zapier** by default: video → private Railway bucket → 24 h presigned link → Zapier
  Catch Hook → *YouTube: Upload Video*. Sent at most once; unknown send states never auto-resend. No Google OAuth
  or API audit needed for uploads. Direct YouTube API upload is still available (`UPLOAD_BACKEND=youtube_api`).
* Voice without an OpenAI key: ElevenLabs (exact timings) → Edge (exact timings, private/test only).
* Tests: **45 passed**. Not live-tested: Claude, Zapier, bucket upload (need your keys).

## Update — footage-first visuals, better voice, Railway (2026-11-07)

* **Real footage is now the main visual.** The script writer gives 3 filmable search terms per entry; the planner
  downloads **2 unique licensed clips per entry** (Pexels → Pixabay; landscape clips centre-cropped), plus intro and
  outro clips. Clips are never reused in a video, a consistent cinematic grade is applied, and the original data
  card only appears as a short closing beat (~25%) per entry. AI stills are used only when no footage exists.
  Private/publish runs **stop** if fewer than 70% of entries have real footage.
* **Voice:** provider order ElevenLabs → OpenAI `gpt-4o-mini-tts` (documentary direction prompt) → Edge; the robotic
  espeak engine is now **test-only**. All narration gets a broadcast polish chain (high-pass, compression, presence
  EQ). Captions use **exact word timings** (ElevenLabs/Edge natively; OpenAI voices aligned with Whisper).
* **Railway:** cron service (08:00 MYT) with a `/data` volume; `scripts/railway_run.sh` entrypoint.
  GitHub Actions production/analytics schedules are now manual-only to avoid double runs.
* Tests: **39 passed** (added footage planner, timing split, Whisper alignment, voice-order tests).
* Not live-tested: Pexels/Pixabay/OpenAI/ElevenLabs calls (no keys in the build environment).

## 1. What was implemented

| Phase | Module(s) | Status |
|---|---|---|
| 1 Research | `research/REPOSITORY_COMPARISON.md` — 5 repos cloned & inspected, licences, audits | Done |
| 2 Trend discovery + history | `app/trend_discovery/` — YouTube `mostPopular` (12 regions), Google Trends RSS, Google News RSS, publisher RSS, Reddit (OAuth, optional); keyword clustering across countries; growth vs. previous 7 days; `trend_signals` + `trend_keywords` tables | Done |
| 3 Topic selection + ranking | `app/topic_selection/selector.py` (0–100 rubric, rejection rules, repeat block, trend-scope honesty), `app/ranking/engine.py` (#10→#1, single metric/unit, monotonic, ≥2 independent domains, factual vs editorial label, stale-figure flag) | Done |
| 4 Research, fact-check, script | `app/research/` (LLM + live web search → **every quote fetched and verified on the source page**, page text never fed back to the LLM), `app/script_generation/writer.py`, `app/fact_checking/checker.py` (every number traced to a verified fact, 8-gram copy check, banned AI phrases, duration, editorial labelling; 3 rewrite attempts) | Done |
| 5 Narration + visuals | `app/voice_generation/tts.py` (OpenAI, ElevenLabs w/ timestamps, Edge, espeak offline; pronunciation map, speed, pitch), `app/visual_generation/` (OpenAI images, Gemini Imagen, Pexels video, **original infographics/brand identity** drawn locally) | Done |
| 6 FFmpeg production | `app/video_editing/` — camera moves, animated rank numbers, word-level karaoke captions, dip-to-black transitions, music with sidechain ducking, loudnorm −14 LUFS, H.264/AAC 1080×1920@30, SRT export | Done |
| 7 Copyright + QC | `app/copyright/provenance.py` (asset DB: id, source, creator, licence, URL, date, commercial use, attribution, modifications, proof, restrictions, synthetic; unknown rights rejected; music manifest; CC licences not interchangeable — only listed licences allowed), `app/quality_control/checks.py` (file validity, resolution, codec, fps, audio level, black-frame/missing-scene detection, caption sync, evidence, script, rights, metadata accuracy, policy terms, synthetic disclosure, repetition) | Done |
| 8 YouTube publishing | `app/youtube_upload/publisher.py` — OAuth refresh token, resumable upload with retries, title/description/tags/category, `containsSyntheticMedia`, thumbnail, optional captions, privacy + `publishAt`, status tracking, **3-layer idempotency**, daily-target guard | Done (see §4) |
| 9 GitHub Actions | `daily-production.yml` (00:00 UTC = 08:00 MYT, dispatch inputs, concurrency, timeout, one auto-resume, artifacts 14 days, report in job summary), `analytics.yml`, `tests.yml` (pytest + pip-audit); `scripts/state.py` encrypted DB on orphan branch with 14 backups | Done |
| 10 Analytics + dashboard | `app/analytics/collector.py` — 24 h / 7 d snapshots (views, watch time, AVD, AV%, likes, comments, subs, shares, traffic sources, retention curve) and a learning step that adjusts niche scores (±5) and feeds best/worst hooks to topic selection | Analytics done · **dashboard not built** |

Also: dry-run / test / private / publish modes, resumable job state, file lock, structured JSON logs with
secret redaction, SSRF-safe fetching (every redirect hop validated), safe YAML, defusedxml for feeds,
pinned dependencies, Dockerfile, failure webhook.

## 2. What was tested

`python -m pytest -q` → **35 passed** (≈3 min; includes real FFmpeg renders).

* Trend parsing & scoring; single-country signals are never labelled global.
* Topic selection: unsafe topic rejected, fake "trend" rejected, repeats blocked, reasons stored.
* Research verifier: fabricated quote (Iapetus fixture) rejected; numbers & near-exact quotes matched.
* Ranking: order, mixed units rejected, <10 verified entries rejected, single-domain evidence rejected.
* Script review: invented number, unsupported hook number, copied source sentence, banned phrase, missing entry.
* Voice timings, offline TTS, caption events, SRT.
* Copyright: unknown source rejected; non-commercial voice allowed in test but blocked in publish;
  music needs allowed licence + proof; path traversal in manifest refused.
* Publisher (fake YouTube client): private by default, never uploads twice, interrupted upload recovered via
  description marker, public/scheduled only with `auto_publish`, refuses upload without reference.
* Analytics learning: no bonus with <3 videos; bounded bonus with data.
* Security: secret redaction, private/metadata IP blocking, unsafe YAML rejected.
* State: AES-GCM round trip, tamper detection, push/pull through a real git remote (orphan branch, encrypted).
* Integration: dry-run stops before media; full test-mode render validated by ffprobe; resume reuses
  finished stages; tampered asset rights stop a private upload; daily target blocks a second upload.
* `pip-audit -r requirements.txt` → **no known vulnerabilities** (Pillow pinned to 13.0.0 after 12.3.0 showed 39 CVEs).

## 3. Sample video

`sample/GLOBAL_TOP10_sample_largest_moons.mp4` — produced by `python -m app run --mode test --mock`.
110 s, 1080×1920, H.264/AAC 48 kHz, −14.8 LUFS, quality gate PASSED.

**Be aware:** the sample uses *offline fixtures*: the research response and source pages are test fixtures
written to mirror NASA/Britannica statements (diameters match published NASA values), the voice is the robotic
offline `espeak-ng` engine, and all visuals are the bot's original infographics with the bot's own synthesised
music. It demonstrates the editing engine and gates, not the final voice/visual quality you get with real APIs.

## 4. What requires your credentials (built, not live-tested here)

The build environment had no API keys and no general internet access, so these integrations are implemented
against the providers' documented REST APIs but have **not been called live**:

| Integration | Needs | Notes |
|---|---|---|
| OpenAI (Responses API + web search, TTS, gpt-image-1) | `OPENAI_API_KEY` | |
| Gemini (generateContent + Google Search grounding, Imagen 4) | `GEMINI_API_KEY` | grounding URLs are redirect links — fetcher follows them safely |
| YouTube Data API (trends, upload, thumbnails) | `YOUTUBE_API_KEY`, `YT_CLIENT_ID/SECRET/REFRESH_TOKEN` | uploads stay **private until Google audits your API project** |
| YouTube Analytics API | same OAuth token | |
| Pexels, ElevenLabs, Reddit | optional keys | |
| Google Trends RSS / Google News RSS / BBC RSS | none | public feeds; formats can change |
| GitHub Actions workflows | repo + secrets | YAML validated; first run will confirm runner setup |

**First real run checklist:** `check-config` → `run --mode dry-run` (read `report.md`: are 10 entries verified?) →
`run --mode test` (watch the video) → `run --mode private` → GitHub dispatch.

## 5. Known limitations / incomplete

* **Dashboard (Part 7) not built.** All data it needs is in SQLite (`jobs`, `topics`, `trend_keywords`,
  `facts`, `assets`, `uploads`, `analytics`, `api_usage`); settings live in YAML. A read-only dashboard can be
  added without changing the pipeline.
* **AI video generation (e.g. Veo/Runway) not implemented** — visuals are AI images with camera moves, licensed
  stock video, and original infographics. Historical maps are not generated.
* **Official Google Trends API** is a gated alpha; the public Trending RSS feed is used instead.
* Research depends on pages that serve readable HTML; PDF and JavaScript-only sources count as unverified, so
  some valid topics will be rejected (by design: no unverified claims).
* Word timings are exact for Edge/ElevenLabs and *estimated* for OpenAI/espeak voices.
* Policy check is keyword + topic-rule based, not a full Community Guidelines classifier.
* Editorial rankings rely on the model's ordering; evidence verifies the supporting facts, not the opinion.
* Actions are pinned to major versions (`@v4/@v5`), not commit SHAs.
* Copyright checks record licences and block unknown sources; **they cannot guarantee you will never get a
  claim or strike.** Review `config/providers.yaml` terms yourself before monetising.

## 6. Expected operating cost

≈ **$0.10–0.20 per video** without AI images, ≈ **$0.50–0.85** with 10 AI images → about **$3–26/month**
at one video per day. GitHub Actions is free on public repos (private: ~25 min/day of 2,000 free min/month).
Details in `docs/SETUP_GUIDE.md` §6. Measured LLM cost is written to each run report.

## 7. How to run

```bash
# install
sudo apt-get install -y ffmpeg fonts-dejavu-core espeak-ng
python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
# test
python -m pytest -q
python -m app run --mode test --mock
# configure (fill values, never commit .env)
cp .env.example .env && nano .env && set -a && source .env && set +a
python scripts/get_youtube_token.py
python -m app check-config
# real runs
python -m app run --mode dry-run
python -m app run --mode test
python -m app run --mode private
# launch on GitHub: push repo, add Secrets, set Actions permissions to read/write,
# Actions -> Daily production -> Run workflow (mode: private)
```

## 8. How to enable automatic publishing

1. Get your Google Cloud project through the YouTube API audit.
2. Use a commercial voice (`voice.provider_order: [openai, elevenlabs]`).
3. In `config/settings.yaml`: `publishing.auto_publish: true`, `visibility: public` (optional
   `schedule_publish_at: "18:00"`).
4. Set repository variable `PRODUCTION_MODE=publish`.

## 9. Dependency licences

requests (Apache-2.0) · PyYAML (MIT) · defusedxml (PSF) · Pillow (MIT-CMU) · edge-tts (LGPLv3) ·
google-api-python-client, google-auth, google-auth-oauthlib (Apache-2.0) · cryptography (Apache-2.0/BSD) ·
pytest (MIT) · pip-audit (Apache-2.0) · system: FFmpeg (LGPL/GPL build-dependent), espeak-ng (GPLv3, optional),
DejaVu fonts (free licence). No code from the researched repositories is included.

## Update — more media sources, free voice, hooks (Nov 2026)

**New media sources (all checked for YouTube-safe licences):**
- **NASA Image & Video Library**: no key needed. Used only for space and astronomy queries. Items credited to other rights holders (Getty, AP, etc.) are skipped. The NASA logo is never used.
- **Openverse**: Creative Commons photos, no key needed. Only CC0, Public Domain Mark and CC BY are accepted. BY-SA, NC and ND are always excluded. Each photo's licence is recorded with that asset, and CC BY credits go into the description automatically.
- **Search images** are now tried before AI images. The fallback order is: footage, then real photos, then AI still, then original cards.

**Kokoro-82M voice:**
- Apache-2.0 licence, runs locally, free. Word timings come from the model itself.
- It is installed in the workflows with CPU torch. If the install fails, the next voice in the list is used.
- Voice order: ElevenLabs → Kokoro → OpenAI → Edge.

**Original sound effects:** whoosh, impact and riser, synthesised with FFmpeg (no samples, so no licence risk). Each entry opens on a whoosh, the top-3 re-hook gets a riser, and #1 gets an impact.

**Hook system:**
- `hook`: a spoken curiosity gap (≤14 words). Weak openers like "Did you know" and "Welcome" are rejected.
- `hook_text`: an on-screen hook of ≤6 words. Numbers are fact-checked, and it must not reveal #1.
- **Teaser:** the video opens on a 1.2–2.4 s flash of the #1 footage with the hook slammed on screen and an impact sound. The animated TOP 10 title appears after it.
- `rehook`: spoken right before #3, with a "THE TOP 3" banner.
- Settings: `video.hook_teaser`, `video.sfx`, `video.sfx_volume`, `voice.kokoro_voice`, `visuals.search_image_providers`.
