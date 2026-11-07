# Setup Guide — install, APIs, GitHub Secrets, deployment, troubleshooting, costs

## 1. Installation (local)

Requirements: Python 3.11+, FFmpeg with libass (standard apt/brew builds), DejaVu fonts, git.
`espeak-ng` is optional (offline test voice only).

```bash
git clone https://github.com/<you>/global-top10-ai.git && cd global-top10-ai
sudo apt-get install -y ffmpeg fonts-dejavu-core espeak-ng
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
python -m app run --mode test --mock          # offline sample video in output/<job>/render/final.mp4
```

## 2. API setup

| Variable | Needed for | How to get it |
|---|---|---|
| `OPENAI_API_KEY` | LLM (topic, research with web search, script), TTS, images | platform.openai.com → API keys. Add billing. |
| `GEMINI_API_KEY` | Fallback LLM with Google Search grounding, Imagen images | aistudio.google.com → Get API key. Use a **paid** tier for commercial output. |
| `YOUTUBE_API_KEY` | Trend discovery (mostPopular per country, 1 quota unit each) | console.cloud.google.com → new project → enable **YouTube Data API v3** → Credentials → API key → restrict to YouTube Data API. |
| `YT_CLIENT_ID/SECRET/REFRESH_TOKEN` | Uploading + analytics | See §2.1 |
| `PEXELS_API_KEY` | **Real licensed footage — the main visual source** | pexels.com/api (free) |
| `PIXABAY_API_KEY` | Second licensed footage source | pixabay.com/api/docs (free, log in) |
| `ELEVENLABS_API_KEY` + voice id | Premium voice (optional) | elevenlabs.io → API keys; paid plan for commercial use |
| `REDDIT_CLIENT_ID/SECRET` | Extra trend signal (optional) | reddit.com/prefs/apps → create "script" app |
| `STATE_PASSPHRASE` | Encrypts the database on GitHub | `python -c "import secrets;print(secrets.token_urlsafe(32))"` — store a copy safely |
| `NOTIFY_WEBHOOK_URL` | Failure alerts (optional) | Slack incoming webhook or Discord webhook URL |

At least one of OpenAI or Gemini is required. Research quality is best with OpenAI (`gpt-4.1` + web search).

### 2.1 YouTube OAuth (one time, on your computer)

1. In the same Google Cloud project: enable **YouTube Data API v3** and **YouTube Analytics API**.
2. **OAuth consent screen** → External → app name → add your Google account under *Test users* → then
   **Publish app** (in "Testing" status, refresh tokens expire after 7 days).
3. **Credentials → Create credentials → OAuth client ID → Desktop app** → download the JSON as
   `scripts/client_secret.json` (git-ignored).
4. Run:
   ```bash
   pip install google-auth-oauthlib
   python scripts/get_youtube_token.py      # sign in with the Google account that owns the channel
   ```
5. Copy the printed `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.

Scopes requested (least privilege): `youtube.upload`, `youtube.readonly` (duplicate check + status),
`yt-analytics.readonly`. Add `--captions` only if you enable caption upload / pinned comments.

### 2.2 The YouTube audit (important)

Videos uploaded through the API by an **unverified** Google Cloud project created after 28 July 2020 are
**locked to private**. Test with `--mode private` first, then apply for the *YouTube API Services Audit and
Quota Extension* form in Google Cloud. Public/scheduled publishing works only after approval. The bot never
tries to bypass this.

## 3. GitHub Secrets & variables

Repository → **Settings → Secrets and variables → Actions**:

* **Secrets:** `OPENAI_API_KEY`, `GEMINI_API_KEY`, `YOUTUBE_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`,
  `YT_REFRESH_TOKEN`, `STATE_PASSPHRASE` (+ optional `PEXELS_API_KEY`, `ELEVENLABS_API_KEY`,
  `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `NOTIFY_WEBHOOK_URL`).
* **Variables:** `PRODUCTION_MODE` = `private` (default), later `publish`.

Secrets are only exposed as environment variables; logs pass through a redaction filter and
`check-config` prints names only.

## 4. Deployment — GitHub Actions + Zapier (current setup)

```
22:00 MYT  nightly.yml   Claude researches 3 topics -> 3 videos rendered -> hosted as release assets -> queued
17:30 UTC  publish.yml   video 1 -> Zapier -> YouTube   (Europe evening)
23:30 UTC  publish.yml   video 2 -> Zapier -> YouTube   (Americas evening)
11:30 UTC  publish.yml   video 3 -> Zapier -> YouTube   (Asia evening)
```

1. **Secrets** — repo → Settings → Secrets and variables → Actions → *New repository secret*:
   `ANTHROPIC_API_KEY`, `ZAPIER_UPLOAD_HOOK`, `YOUTUBE_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`,
   `STATE_PASSPHRASE` (+ optional `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `NOTIFY_WEBHOOK_URL`).
2. **Permissions** — Settings → Actions → General → *Workflow permissions* → **Read and write**.
3. **Zap** — see `docs/ZAPIER_SETUP.md`.
4. **Test** — Actions → *Single video (manual test)* → mode `test` → download the artifact and watch it.
   Then Actions → *Nightly production* → *Run workflow*; afterwards *Publish slot* runs on its own.
5. **Go public** — after testing, set `publishing.auto_publish: true` and `visibility: public` in
   `config/settings.yaml` (until then every video is uploaded **private**).

Notes: the repo is public, so GitHub Actions minutes are free. Videos are hosted as assets of a temporary
`queue-YYYYMMDD` pre-release (anyone browsing the repo's Releases could see them until they are deleted after
3 days); the database is stored encrypted on the `bot-state` branch. GitHub may start scheduled runs late —
publish runs start 20 minutes early and wait for the slot. To change the slots, edit `publishing.slots_utc`
**and** the cron lines in `.github/workflows/publish.yml`.

## 4b. Deployment on GitHub Actions — single daily video (alternative)

1. Push the repository to GitHub (private recommended).
2. Add the secrets above. Repository → Settings → Actions → General → Workflow permissions:
   **Read and write** (needed to push the encrypted `bot-state` branch).
3. Actions → **Tests** should go green on push.
4. Actions → **Daily production** → *Run workflow* → mode `dry-run`, then `test` (download the video artifact),
   then `private`.
5. The schedule `0 0 * * *` runs daily at **08:00 Malaysia time** (GitHub may start scheduled runs several
   minutes late at busy times).
6. **Analytics** runs daily at 21:30 MYT and records 24 h / 7 day snapshots that feed topic selection.

### Enabling automatic public publishing

After several good private videos **and** an approved YouTube API audit:

```yaml
# config/settings.yaml
publishing:
  auto_publish: true
  visibility: public          # or unlisted
  schedule_publish_at: "18:00"   # optional: publish at 18:00 channel time via publishAt
```
and set the repository variable `PRODUCTION_MODE=publish`. Also switch the voice to a commercial provider
(OpenAI or ElevenLabs) — Edge TTS is blocked in publish mode by the copyright gate.

### State, backups, recovery

* The SQLite DB is restored from / saved to the orphan branch `bot-state` as `state.db.enc`
  (AES-256-GCM, scrypt key from `STATE_PASSPHRASE`); 14 daily backups are kept in `backups/`.
* A failed run is resumed automatically once (`--resume-latest`); completed stages are not repeated.
* Uploads are idempotent: a retry can never publish the same video twice; a second scheduled run on the same
  day stops at the daily target.
* To restore a backup: `git show origin/bot-state:backups/2026-11-07.db.enc > state.db.enc`, then decrypt with
  `python -c "import sys;sys.path.insert(0,'scripts');import state,os;open('database/global_top10.db','wb').write(state.decrypt(open('state.db.enc','rb').read(),os.environ['STATE_PASSPHRASE']))"`.

### Docker (optional)

```bash
docker build -t gt10 . && docker run --env-file .env -v $PWD/output:/app/output gt10 python -m app run --mode test
```

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No LLM provider configured` | Set `OPENAI_API_KEY` or `GEMINI_API_KEY`. |
| `only N entries have verified evidence` | The research couldn't verify 10 entries (pages blocked/JS-only, quotes not verbatim). The bot tries up to 3 topics, then stops. Normal on some days; check `report.md`. |
| `script failed fact-check 3 times` | The writer kept adding unsupported numbers. Lower `ai.temperature` or switch model. |
| `quality/copyright gate failed` | Read `output/<job>/ERROR_REPORT.md`. Typical: non-commercial voice in publish mode, missing music proof, duration out of range. |
| Uploaded video is private although I chose public | YouTube API project not audited (see §2.2), or `auto_publish: false`. |
| `thumbnail: skipped` | Custom thumbnails need a phone-verified channel. |
| `invalid_grant` on upload | Refresh token revoked/expired (consent screen left in Testing). Re-run `get_youtube_token.py`. |
| `STATE_PASSPHRASE secret missing` | Add the secret; it must be ≥16 characters. |
| Captions missing in render | Your FFmpeg lacks libass — install the distro FFmpeg. |
| `daily target reached` | Already uploaded today; increase `production.videos_per_day` if intended. |
| Google Trends source empty | The public RSS feed changed or was rate-limited; other sources still work. |

## 6. Cost estimation (approximate — verify current prices on each provider's pricing page)

Per video at the default settings (≈90–120 s, 1 video/day):

| Item | Typical cost |
|---|---|
| Topic + script (`gpt-4.1-mini`, ~3 calls) | < $0.01 |
| Research (`gpt-4.1` + web search, 1 call with several searches) | ~$0.05–0.15 |
| Narration (`gpt-4o-mini-tts`, ~2 min) | ~$0.03 |
| Visuals — original infographics only | $0 |
| Visuals — Pexels stock | $0 |
| Visuals — 10 AI images (gpt-image-1 medium / Imagen 4) | ~$0.40–0.65 |
| YouTube Data/Analytics API | $0 (trends ≈ 12 units/day of the 10,000 default; uploads use a separate daily upload bucket) |
| GitHub Actions | $0 on public repos; private repos use ~20–30 min/day of the 2,000 free min/month |

**≈ $0.10–0.20 per video without AI images, ≈ $0.50–0.85 with AI images → roughly $3–26 per month.**
The run report shows the measured LLM cost for each day (`api_usage` table).
