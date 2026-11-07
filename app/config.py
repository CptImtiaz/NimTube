"""Configuration: YAML files (safe_load only) + environment variables for secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
NICHES_DIR = ROOT / "niches"

MODES = ("dry-run", "test", "private", "publish", "queue")
#  dry-run : discovery + topic + research + ranking + script only, no media, no upload
#  test    : full video produced, NO upload
#  private : full video, uploaded as PRIVATE
#  publish : full video, uploaded with settings.publishing (public/unlisted/scheduled) if auto_publish is true
#  queue   : full video, hosted + queued for a publish slot (nightly batch); sent to Zapier at the slot time


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def load_dotenv(path: Path | None = None) -> None:
    """Local runs: read KEY=value lines from .env (gitignored, never committed). Real environment variables
    and GitHub Secrets always win over the file."""
    path = path or ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'").strip("`")
        if k and v and not os.environ.get(k):
            os.environ[k] = v


if not os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("GT10_NO_DOTENV"):
    load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    raw: dict
    providers: dict
    rules: dict
    niches: dict[str, dict]
    data_dir: Path
    mode: str = "test"
    mock: bool = False          # use recorded fixtures instead of network (tests / offline demo)
    extra: dict = field(default_factory=dict)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def db_path(self) -> Path:
        return self.data_dir / "global_top10.db"

    @property
    def output_dir(self) -> Path:
        return Path(env("OUTPUT_DIR") or ROOT / "output")


def load_settings(mode: str = "test", mock: bool | None = None, settings_file: Path | None = None) -> Settings:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    raw = _load_yaml(settings_file or CONFIG_DIR / "settings.yaml")
    providers = _load_yaml(CONFIG_DIR / "providers.yaml")
    rules = _load_yaml(CONFIG_DIR / "ranking_rules.yaml")
    enabled = set(raw.get("topics", {}).get("enabled_niches", []))
    niches = {}
    for p in sorted(NICHES_DIR.glob("*.yaml")):
        n = _load_yaml(p)
        if n.get("name") in enabled:
            niches[n["name"]] = n
    data_dir = Path(env("DATA_DIR") or ROOT / "database")
    data_dir.mkdir(parents=True, exist_ok=True)
    if mock is None:
        mock = env("GT10_MOCK").lower() in ("1", "true", "yes")
    return Settings(raw=raw, providers=providers, rules=rules, niches=niches, data_dir=data_dir, mode=mode, mock=mock)
