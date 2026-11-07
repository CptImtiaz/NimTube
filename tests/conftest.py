import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "db"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    for k in ("ANTHROPIC_API_KEY", "ZAPIER_UPLOAD_HOOK", "UPLOAD_BACKEND", "OPENAI_API_KEY", "GEMINI_API_KEY", "YOUTUBE_API_KEY", "ELEVENLABS_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    from app.config import load_settings
    return load_settings(mode="test", mock=True)


@pytest.fixture
def db(settings):
    from app.db import DB
    return DB(settings.db_path)
