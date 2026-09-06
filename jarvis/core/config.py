"""Central configuration, loaded once from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


def _path(env_key: str, default: str) -> Path:
    raw = os.getenv(env_key, default)
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    fast_model: str = os.getenv("JARVIS_FAST_MODEL", "claude-haiku-4-5")
    deep_model: str = os.getenv("JARVIS_DEEP_MODEL", "claude-sonnet-5")
    monthly_budget_usd: float = float(os.getenv("JARVIS_MONTHLY_BUDGET_USD", "15.00"))

    brave_api_key: str = os.getenv("BRAVE_API_KEY", "")

    google_credentials_file: Path = field(
        default_factory=lambda: _path("GOOGLE_CREDENTIALS_FILE", "credentials_google.json")
    )
    google_token_file: Path = field(
        default_factory=lambda: _path("GOOGLE_TOKEN_FILE", "token_google.json")
    )

    ms_client_id: str = os.getenv("MS_CLIENT_ID", "")
    ms_tenant_id: str = os.getenv("MS_TENANT_ID", "common")
    ms_token_file: Path = field(
        default_factory=lambda: _path("MS_TOKEN_FILE", "token_microsoft.json")
    )

    host: str = os.getenv("JARVIS_HOST", "127.0.0.1")
    port: int = int(os.getenv("JARVIS_PORT", "8765"))

    user_name: str = os.getenv("JARVIS_USER_NAME", "Sir")
    timezone: str = os.getenv("JARVIS_TIMEZONE", "America/New_York")

    db_path: Path = DATA_DIR / "jarvis.sqlite3"

    @property
    def google_enabled(self) -> bool:
        return self.google_token_file.exists()

    @property
    def microsoft_enabled(self) -> bool:
        return bool(self.ms_client_id) and self.ms_token_file.exists()


cfg = Config()
