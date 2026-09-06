"""Central configuration, loaded once from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
IMAGES_DIR = DATA_DIR / "images"
MODELS_DIR = DATA_DIR / "models"
IMAGES_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(exist_ok=True)


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

    # --- image generation ---
    image_provider: str = os.getenv("JARVIS_IMAGE_PROVIDER", "fal").strip().lower()
    fal_key: str = os.getenv("FAL_KEY", "")
    together_api_key: str = os.getenv("TOGETHER_API_KEY", "")
    together_image_model: str = os.getenv(
        "TOGETHER_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell-Free"
    )

    # --- Tripo3D ---
    tripo_api_key: str = os.getenv("TRIPO_API_KEY", "")
    tripo_base_url: str = os.getenv(
        "TRIPO_BASE_URL", "https://openapi.tripo3d.ai/v3"
    ).rstrip("/")
    tripo_model_version: str = os.getenv("TRIPO_MODEL_VERSION", "v3.1-20260211")
    # How long a turn will wait for a 3D job before handing back a job id.
    tripo_wait_seconds: int = int(os.getenv("TRIPO_WAIT_SECONDS", "240"))

    ms_client_id: str = os.getenv("MS_CLIENT_ID", "")
    ms_tenant_id: str = os.getenv("MS_TENANT_ID", "common")
    ms_token_file: Path = field(
        default_factory=lambda: _path("MS_TOKEN_FILE", "token_microsoft.json")
    )

    host: str = os.getenv("JARVIS_HOST", "127.0.0.1")
    port: int = int(os.getenv("JARVIS_PORT", "8765"))

    user_name: str = os.getenv("JARVIS_USER_NAME", "Duke")
    timezone: str = os.getenv("JARVIS_TIMEZONE", "America/New_York")

    db_path: Path = DATA_DIR / "jarvis.sqlite3"
    images_dir: Path = IMAGES_DIR
    models_dir: Path = MODELS_DIR

    @property
    def google_enabled(self) -> bool:
        return self.google_token_file.exists()

    @property
    def microsoft_enabled(self) -> bool:
        return bool(self.ms_client_id) and self.ms_token_file.exists()

    @property
    def images_enabled(self) -> bool:
        if self.image_provider == "fal":
            return bool(self.fal_key)
        if self.image_provider == "together":
            return bool(self.together_api_key)
        return False

    @property
    def tripo_enabled(self) -> bool:
        return bool(self.tripo_api_key)


cfg = Config()


def tz() -> ZoneInfo:
    """The user's timezone, resolved once.

    Windows has no system timezone database -- Python reads the `tzdata`
    package instead -- so this is the first thing to fail on a fresh Windows
    install. Say why, rather than surfacing a bare ZoneInfoNotFoundError from
    inside whichever tool happened to ask for the time.
    """
    global _TZ
    if _TZ is None:
        try:
            _TZ = ZoneInfo(cfg.timezone)
        except ZoneInfoNotFoundError as exc:
            raise RuntimeError(
                f"Unknown timezone {cfg.timezone!r}. Set JARVIS_TIMEZONE in .env to an "
                f"IANA name such as America/New_York. If the name looks right, the "
                f"tzdata package is missing -- run: pip install tzdata"
            ) from exc
    return _TZ


_TZ: ZoneInfo | None = None
