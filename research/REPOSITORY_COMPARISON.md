# Repository Research & Comparison

Research date: 2026-11-07. All five repositories were cloned with `git clone` and inspected locally
(source, dependency files, workflows, tests, commit history). Claims below were checked against
source code, not README text. No downloaded repository code was executed; dependency audits used
`npm audit --package-lock-only` (reads the lockfile only).

> Verification limits: the GitHub REST API was not available in the research environment, so stars,
> fork flags and redirect status could not be queried. All five clone URLs resolved to the same
> owner/name, so no rename/ownership change was observed at clone time.

## Summary table

| # | Repository | Licence | Language | Last commit | Verdict |
|---|---|---|---|---|---|
| 1 | [rushindrasinha/youtube-shorts-pipeline](https://github.com/rushindrasinha/youtube-shorts-pipeline) ("Verticals v3") | MIT © 2026 Rushindra Sinha | Python (≈3.5k LOC) | 2026-06-10 (31 commits) | Best architectural reference for a Python Shorts pipeline. Reimplement ideas; avoid its silent fallbacks. |
| 2 | [darkzOGx/youtube-automation-agent](https://github.com/darkzOGx/youtube-automation-agent) | MIT © 2025 "YouTube Automation Agent Contributors" | Node.js (≈17k LOC) | 2026-10-26 (82 commits) | Feature-rich Node app; good ideas for provenance/claims and publishing. Too heavy, 36 known vulnerable deps. Ideas only. |
| 3 | [Nabin68/AutoTube](https://github.com/Nabin68/AutoTube) | **No licence file** | Python (≈2.1k LOC) | 2025-12-15 (17 commits) | **Do not reuse any code** (all rights reserved). Uploads by Selenium browser automation, not the API. Avoid. |
| 4 | [nathanphan/yt-agent](https://github.com/nathanphan/yt-agent) | MIT (same text as #2) | Node.js | 2026-08-25 (67 commits) | **Same codebase as #2.** Its HEAD (`260d7a9`) is an ancestor of #2; #2 is 15 commits ahead. Treat as a stale mirror of #2. |
| 5 | [sheb1lmsp/youtube-trending-analytics](https://github.com/sheb1lmsp/youtube-trending-analytics) | MIT © 2025 Mohammed Shebil | Python + Jupyter + Streamlit | 2026-11-07 (399 commits, daily bot) | Best reference for multi-country `mostPopular` collection. Proves the chart still returns 50 videos × 110 regions daily. |

---

## 1. Verticals v3 — `rushindrasinha/youtube-shorts-pipeline`

**Architecture.** CLI package `verticals/` with stages `research → draft → broll → voiceover → whisper →
captions → music → assemble → thumbnail → upload`, a JSON state machine (`state.py`) for resumable stages,
YAML "niche profiles" (15 niches) that shape tone/visuals/music, pluggable topic sources (`topics/`).

**Verified working (in source):**
- Multi-provider LLM layer (`llm.py`: Anthropic, Gemini, OpenAI, Ollama, MiniMax, LiteLLM, Claude CLI).
- Edge TTS / ElevenLabs / MiniMax / 60db / macOS `say` voiceover (`tts.py`, 454 LOC).
- Whisper word-level captions → ASS burn-in + SRT (`captions.py`), libass detection.
- FFmpeg assembly with Ken Burns motion, music with a volume-expression ducking filter (`music.py`).
- Private-by-default upload via the official YouTube Data API, SRT caption + thumbnail upload (`upload.py`).
- Retry decorator with exponential backoff (`retry.py`). 105 pytest test functions.

**Incomplete / problematic:**
- `topics/google_trends.py` depends on **pytrends**, which was archived in April 2025 and is not in
  `requirements.txt`; `trending_searches()` is no longer a reliable endpoint. Single-geo only.
- Research = scraping `html.duckduckgo.com` (fragile; no source verification, only snippet truncation
  as prompt-injection mitigation).
- `broll.py` **silently substitutes solid-colour frames** when image generation fails or the key is missing
  — exactly the "unlabelled placeholder" behaviour our spec forbids.
- Music = any `*.mp3` in a folder, no licence tracking.
- `upload_to_youtube` is wrapped in `@with_retry(max_retries=2)` with **no idempotency**: a network error
  after YouTube accepted the upload can publish duplicates.
- README itself lists Pexels, Docker, Gradio etc. as roadmap, not implemented.

**Security:** OAuth token stored as a file with restricted permissions (`write_secret_file`) — good.
No dependency pinning (ranges only). No CI workflow in the repo.

**Reuse decision:** *Independently reimplement* the stage/resume pattern, niche YAML profiles, ASS
caption approach and ducking. Do not copy code (MIT would allow it with attribution, but our
architecture differs and we need stricter failure semantics). **Avoid** pytrends, DDG scraping,
silent fallback frames, non-idempotent retries.

## 2. YouTube Automation Agent — `darkzOGx/youtube-automation-agent`

**Architecture.** Express server + static dashboard, seven "agents" (strategy, script, thumbnail, SEO,
production, publishing, analytics), SQLite via `database/db.js` (2.9k LOC), `node-cron` schedules,
many services in `utils/` (provenance, scene repair, retention engine, telemetry).

**Verified working (in source):**
- Upload via `googleapis` `videos.insert`, default `privacyStatus: private`, `publishAt` scheduling,
  `containsSyntheticMedia` flag passed through (`agents/publishing-scheduling-agent.js`).
- `ProvenanceService`: sources + claims with statuses (`pending/verified/rejected`, `supported/unsupported`)
  and claim risk levels — a good model for evidence tracking. It tracks **claims**, not **asset licences**.
- Server binds `127.0.0.1` by default; warns if exposed without `API_KEY`.
- Credentials/tokens written with mode `0600`. Telemetry is opt-in and HTTPS-only.

**Incomplete / problematic:**
- Trend discovery = `videos.list chart=mostPopular` for **one region** (`YOUTUBE_REGION`, default US).
- `requireAPIKey()` lets every request through when `API_KEY` is unset and uses a non-constant-time compare.
- `npm audit` on the lockfile: **36 vulnerabilities (2 critical, 19 high)** incl. axios, express, body-parser,
  form-data, follow-redirects.
- CI only runs lint; `test.js` is a smoke script (≈35 assertions), not a test suite.
- Heavy dependency surface (Playwright, Replicate, Azure Speech SDK, sharp) for a single bot.

**Reuse decision:** *Ideas only* — claim/source provenance model, private-by-default publishing with
`publishAt`, synthetic-media flag. Node.js gives no advantage for our Python video pipeline.

## 3. AutoTube — `Nabin68/AutoTube`

**No LICENSE file → all rights reserved. No code may be reused.**

- README promises "Upload to YouTube via YouTube Data API", Whisper captions and a scheduler.
  **Source reality:** `uploader/youtube_upload.py` (624 LOC) drives YouTube Studio with **Selenium** using a
  hard-coded Windows Chrome profile path (`C:\Users\KIIT\...`) and kills Chrome processes.
  `captions/captions.py` and `scheduler/job_scheduler.py` are **empty files** (0 lines).
- Trends = NewsAPI "everything" with a tech keyword filter (`fetch_trends/trends.py`), MD5 title-hash history.
- Images = Hugging Face Inference API (SDXL). Video = MoviePy.
- Browser automation of YouTube Studio is brittle and conflicts with platform terms — **avoid**.

## 4. YT Agent — `nathanphan/yt-agent`

Same MIT licence text and identical initial commits as #2. `git merge-base` shows yt-agent's HEAD
(`260d7a9`, 2026-08-25) **is contained in** darkzOGx's history; darkzOGx has 15 further commits.
Lockfile audit is identical (36 vulnerabilities). **Conclusion:** a stale copy of #2; nothing additional
to reuse. Same assessment as #2.

## 5. YouTube Trending Analytics — `sheb1lmsp/youtube-trending-analytics`

**Architecture.** `scripts/fetch_youtube_trend.py` loops over **110 region codes** (`countries.json`),
calls `videos.list(part=snippet,statistics,contentDetails, chart=mostPopular, regionCode=X, maxResults=50)`,
writes partitioned CSVs (`data/videos/country=XX/year=/month=/`), plus a Streamlit dashboard and a GitHub
Actions workflow (`daily.yml`, `cron: "30 0 * * *"`) that commits data back to the repo and pushes to Kaggle.

**Verified working:** the 2026-11-07 commit added 51-line CSVs (header + 50 videos) for every region →
`chart=mostPopular` with `regionCode` **still works** as of today. Category mapping via `categories.json`.

**Issues:** reads `status.embeddable` / `status.madeForKids` but never requests `part=status` (always False);
no tests; 1.2 GB of data committed to git (repo bloat); Kaggle token echoed into files in CI (masked, but
written to disk); actions pinned to old majors (`checkout@v3`, `setup-python@v4`).

**Reuse decision:** *Independently reimplement* multi-region mostPopular collection with historical
storage — in SQLite, persisted outside git history. Avoid committing raw data into the main branch.

---

## Cross-cutting findings that shaped GLOBAL TOP 10 AI

| Need | Best reference | Our approach |
|---|---|---|
| Worldwide YouTube signal | #5 (110 regions) | 12 configurable regions, `mostPopular` (1 quota unit each), keyword clustering across countries; a topic needs ≥ 3 countries to be called "international". |
| Google Trends | none worked (pytrends archived) | Public **Trending RSS** `trends.google.com/trending/rss?geo=XX` (stable, per-country). Official Trends API is gated alpha → adapter documented, not implemented. |
| News | #3 (NewsAPI, paid) | **Google News RSS** per country + configurable publisher RSS (BBC, Reuters-style feeds) — free, no key. |
| Research | #1 (DDG scraping) | LLM with grounded web search (OpenAI web search / Gemini Google Search grounding) returns candidate facts + URLs, **then our verifier fetches each URL and checks the quoted evidence actually appears on the page**. ≥ 2 independent domains required. Retrieved text is treated as data only. |
| Evidence/provenance | #2 (claims) | Claims → evidence table **plus** a separate **asset licence database** (none of the five had one). |
| Visual failures | #1 (silent solid frames) | Provider chain; if all fail → **stop production**. Original locally-rendered infographics are first-class, recorded as original assets. |
| Upload | #1, #2 | Official API, private by default, resumable, **idempotency key** in DB + description marker + pre-upload lookup of own uploads playlist. |
| Scheduling/hosting | #5 (Actions + commit data) | GitHub Actions; state DB **encrypted** and stored on an orphan `bot-state` branch (not mixed into code history). |
| Dependencies | — | Pinned versions, `pip-audit` in CI. |

## Licence & attribution summary

- **No source code from any of the five repositories was copied** into GLOBAL TOP 10 AI. The system is an
  independent implementation; MIT repos (#1, #2, #4, #5) were used as design references only, so no
  attribution notice is legally required, but they are credited in `README.md` → *Acknowledgements*.
- AutoTube (#3) has no licence and was not used beyond reading for comparison.
- Runtime dependencies are listed with licences in `BUILD_REPORT.md`.
- **Edge TTS caveat:** `edge-tts` (LGPLv3 library) calls Microsoft's consumer Read Aloud service, which
  has no published commercial-use licence. It is supported for testing, but the build report recommends a
  commercial TTS (OpenAI, ElevenLabs, Google) for a monetised channel.
