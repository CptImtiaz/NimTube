# GLOBAL TOP 10 AI

An autonomous, evidence-based **Top 10 documentary Shorts** bot for YouTube. Every day it discovers what the
world is paying attention to, turns it into a rankable topic, researches it with verified sources, writes an
original documentary script, produces narration and original visuals, renders a 1080×1920 video, checks rights
and quality, and publishes 3 videos a day at global prime times — on GitHub Actions + Zapier.

```
 Trends (YouTube mostPopular ×12 countries · Google Trends RSS · Google News RSS · publisher RSS · Reddit)
   └─► Topic selection (0–100 score, rejection rules, no repeats, trend scope: global/regional/evergreen)
        └─► Research (LLM + live web search)  ──►  Verifier fetches every source & checks the quote + number
             └─► Ranking engine (#10→#1, one metric, ≥2 independent domains, factual vs editorial)
                  └─► Script writer ──► Fact-checker (every number traced to a verified fact; rewrite ×3)
                       └─► Voice (ElevenLabs / OpenAI / Edge)  +  REAL footage (Pexels / Pixabay) + original data cards
                            └─► FFmpeg (camera moves, animated ranks, word-level captions, ducked music, −14 LUFS)
                                 └─► Copyright + quality + policy gate ──► YouTube upload (idempotent) ──► Analytics → learning
```

## Quick start (local, no API keys needed)

```bash
sudo apt-get install -y ffmpeg fonts-dejavu-core espeak-ng     # macOS: brew install ffmpeg espeak-ng
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q                                            # 51 tests, mock APIs + real FFmpeg
python scripts/make_original_music.py                          # optional: original music bed
python -m app run --mode test --mock                           # renders a sample video from offline fixtures
```

With real keys (see `.env.example` and **[docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md)**):

```bash
python -m app check-config            # shows which keys are set (never their values)
python -m app discover                # today's worldwide trend scores
python -m app run --mode dry-run      # topic + research + ranking + script only
python -m app run --mode test         # full video, no upload
python -m app run --mode private      # full video, uploaded PRIVATE
python -m app run --mode publish      # uses config/settings.yaml -> publishing (auto_publish must be true)
python -m app run --mode test --topic "Top 10 Largest Lakes in the World"
```

## Modes & safety

| Mode | Media | Upload | Use |
|---|---|---|---|
| `dry-run` | none | no | check topic/research/script quality and cost |
| `test` | full video | no | review videos locally / as artifacts |
| `private` | full video | **private** | default for the scheduled workflow until you are happy |
| `publish` | full video | public / unlisted / scheduled — only if `publishing.auto_publish: true` | production |

Production **stops** (and writes `ERROR_REPORT.md`) if: fewer than 10 entries have verified evidence, evidence comes
from <2 independent domains, the script contains a number not in the verified facts, any asset has unknown or
non-commercial rights (publish mode), the video fails technical checks, or the topic repeats a recent one.

## Configuration

* `config/settings.yaml` — schedule target, duration, regions, niches, voice, visuals, publishing.
* `config/providers.yaml` — approved media/voice/music sources and their licence facts (review before monetising).
* `config/ranking_rules.yaml` — methodology rules (single metric, monotonic values, staleness).
* `niches/*.yaml` — evergreen topic seeds and mood per niche.
* `assets/music/manifest.yaml` — the only music the bot may use.

## Project layout

```
app/  trend_discovery · topic_selection · research · ranking · script_generation · fact_checking ·
      visual_generation · voice_generation · video_editing · thumbnail · copyright · quality_control ·
      youtube_upload · analytics · pipeline.py · cli.py · db.py · llm.py · http.py · log.py
config/ niches/ assets/ scripts/ tests/ research/ docs/ .github/workflows/
```

## Documentation

* [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) — installation, API setup, GitHub Secrets, deployment, troubleshooting, costs
* [BUILD_REPORT.md](BUILD_REPORT.md) — what is implemented, tested, and what still needs your credentials
* [research/REPOSITORY_COMPARISON.md](research/REPOSITORY_COMPARISON.md) — the five-repository research

## Acknowledgements

Designed after studying (no code copied): Verticals v3 (rushindrasinha/youtube-shorts-pipeline, MIT),
YouTube Automation Agent (darkzOGx/youtube-automation-agent, MIT), YT Agent (nathanphan/yt-agent, MIT),
YouTube Trending Analytics (sheb1lmsp/youtube-trending-analytics, MIT). AutoTube (Nabin68) has no licence
and was only reviewed.

## Licence

Your project — add the licence of your choice. Third-party dependency licences are listed in BUILD_REPORT.md.
