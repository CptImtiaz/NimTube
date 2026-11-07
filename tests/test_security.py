import pytest

from app import http
from app.log import redact


def test_secret_redaction(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890abcdefXYZ")
    s = redact("calling with sk-test-1234567890abcdefXYZ and key=AIzaSyA1234567890123456789012345678901 ok")
    assert "sk-test" not in s and "AIza" not in s and "***" in s
    assert "ya29" not in redact("token ya29.a0AfH6SMBabcdef")


def test_blocks_private_and_non_http_urls():
    for bad in ("file:///etc/passwd", "http://127.0.0.1/admin", "http://169.254.169.254/latest", "ftp://x.org/a"):
        with pytest.raises(http.HttpError):
            http.validate_public_url(bad)


def test_yaml_is_safe_loaded(tmp_path):
    from app.config import _load_yaml
    p = tmp_path / "evil.yaml"
    p.write_text("a: !!python/object/apply:os.system ['echo hacked']\n")
    with pytest.raises(Exception):
        _load_yaml(p)
