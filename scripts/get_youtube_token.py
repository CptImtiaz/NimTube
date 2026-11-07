"""Run ONCE on your own computer to authorise GLOBAL TOP 10 AI to upload to YOUR channel.

Prerequisites (Google Cloud Console, same project as your YouTube Data API key):
  1. APIs & Services -> Library: enable "YouTube Data API v3" and "YouTube Analytics API".
  2. OAuth consent screen: External, add your Google account as a Test user, then "Publish app"
     (testing-mode refresh tokens expire after 7 days).
  3. Credentials -> Create credentials -> OAuth client ID -> "Desktop app" -> download JSON
     and save it next to this script as client_secret.json  (never commit it).

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
secret = Path(__file__).with_name("client_secret.json")
if not secret.exists():
    sys.exit(f"Missing {secret}. Download your Desktop OAuth client JSON there first.")
flow = InstalledAppFlow.from_client_secrets_file(str(secret), scopes=scopes)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("\nAdd these as GitHub Actions secrets (keep them private):\n")
print(f"YT_CLIENT_ID={creds.client_id}")
print(f"YT_CLIENT_SECRET={creds.client_secret}")
print(f"YT_REFRESH_TOKEN={creds.refresh_token}")
print("\nGranted scopes:", ", ".join(scopes))
