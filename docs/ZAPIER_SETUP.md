# Zapier upload setup (one Zap, ~5 minutes)

The bot renders videos every night on GitHub Actions and hosts each file as a release asset of your public
repo. At each publish slot it sends the video link and metadata to Zapier. Zapier uploads it to YouTube with its own
approved YouTube app — so you do **not** need Google OAuth tokens or the YouTube API audit for uploading.

## 1. Create the Zap

1. zapier.com → **Create Zap**.
2. **Trigger:** *Webhooks by Zapier* → **Catch Hook** → Continue → copy the webhook URL
   (`https://hooks.zapier.com/hooks/catch/...`). Webhooks by Zapier needs a paid Zapier plan.
3. In GitHub → repo → Settings → Secrets → Actions, add `ZAPIER_UPLOAD_HOOK=<that URL>`.
4. Send a test payload so Zapier learns the fields, from your computer:
   ```bash
   curl -X POST "$ZAPIER_UPLOAD_HOOK" -H "Content-Type: application/json" -d '{"title":"Test #shorts",
   "description":"test","tags":"a, b","privacy":"private","publish_at":"","category_id":"27",
   "video_url":"https://example.com/v.mp4","thumbnail_url":"","made_for_kids":false,
   "contains_synthetic_media":false,"job_id":"test","idempotency_key":"test"}'
   ```
5. **Action:** *YouTube* → **Upload Video** → connect your channel's Google account, then map:

| YouTube field | Zapier value |
|---|---|
| Video | `video_url` |
| Title | `title` |
| Description | `description` |
| Privacy Status | `privacy` |
| Tags | `tags` |
| Category | `category_id` (27 = Education) |
| Made for kids | `made_for_kids` (No) |
| Publish at | leave empty (the bot publishes at the slot time itself; Zapier's Publish At needs a YouTube Partner account) |

6. Optional second action: *YouTube → Set Thumbnail* (if available on your plan/app) with `thumbnail_url`.
7. Turn the Zap **on**.

## 2. How safe is it?

* A video is sent to Zapier **at most once** (stored in the database by idempotency key). If a send times out
  with an unknown result, the bot stops instead of risking a duplicate — check your channel, then set
  `ZAPIER_FORCE_RESEND=1` for one run if it is really missing.
* The bot only produces a payload after all quality, fact and copyright checks pass.
* `privacy` is `private` until you set `publishing.auto_publish: true` and `PRODUCTION_MODE=publish`.
* Note: YouTube's "altered or synthetic content" checkbox may not be exposed by the Zapier action.
  The payload includes `contains_synthetic_media`; if it is true (AI images used), tick the box in YouTube
  Studio. With real footage + original graphics it is normally false (narration is AI — the description says so).

## 3. What Zapier mode does not do

YouTube Analytics needs your own OAuth token (`YT_CLIENT_ID/SECRET/REFRESH_TOKEN`). Without it the bot still
works; it just learns from trends instead of your channel's performance. You can add the token later.
