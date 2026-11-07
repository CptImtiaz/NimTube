"""Run ONCE on your own computer to authorise GLOBAL TOP 10 AI to upload to YOUR channel.

Prerequisites (Google Cloud Console, same project as your YouTube Data API key):
  1. APIs & Services -> Library: enable "YouTube Data API v3" and "YouTube Analytics API".
  2. OAuth consent screen: External, add your Google account as a Test user, then "Publish app"
     (testing-mode refresh tokens expire after 7 days).
  3. Credentials -> Create credentials -> OAuth client ID -> "Desktop app".
     Put YT_CLIENT_ID and YT_CLIENT_SECRET in your local .env (or download the JSON next to this
     script as client_secret.json). Never commit either.

Usage:
  pip install google-auth-oauthlib
  python scripts/get_youtube_token.py              # upload + read-only + analytics (least privilege)
  python scripts/get_youtube_token.py --captions   # also allow caption upload / pinned comments (broader scope)

Copy the three printed values into GitHub -> Settings -> Secrets and variables -> Actions.
"""
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.youtube_upload.publisher import SCOPES, CAPTION_SCOPE  # noqa: E402

scopes = SCOPES + ([CAPTION_SCOPE] if "--captions" in sys.argv else [])
from app.config import env  # noqa: E402  (also loads the local .env)

secret = Path(__file__).with_name("client_secret.json")
if env("YT_CLIENT_ID") and env("YT_CLIENT_SECRET"):
    flow = InstalledAppFlow.from_client_config({"installed": {
        "client_id": env("YT_CLIENT_ID"), "client_secret": env("YT_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]}}, scopes=scopes)
elif secret.exists():
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), scopes=scopes)
else:
    sys.exit("Set YT_CLIENT_ID and YT_CLIENT_SECRET in .env (or put client_secret.json next to this script).")
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("\nAdd these as GitHub Actions secrets (keep them private):\n")
print(f"YT_CLIENT_ID={creds.client_id}")
print(f"YT_CLIENT_SECRET={creds.client_secret}")
print(f"YT_REFRESH_TOKEN={creds.refresh_token}")
env_file = Path(__file__).resolve().parent.parent / ".env"
if env_file.exists() and creds.refresh_token:
    lines = [l for l in env_file.read_text().splitlines() if not l.startswith("YT_REFRESH_TOKEN=")]
    env_file.write_text("\n".join(lines + [f"YT_REFRESH_TOKEN={creds.refresh_token}"]) + "\n")
    print("(also saved YT_REFRESH_TOKEN to your local .env)")
print("\nGranted scopes:", ", ".join(scopes))
