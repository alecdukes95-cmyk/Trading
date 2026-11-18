"""Paths and user settings. Settings live in userdata/settings.json; secrets live in .env."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from edgelab.data.sessions import DEFAULT_SESSIONS, SessionConfig

ROOT = Path(__file__).resolve().parent.parent

try:  # API keys are read from .env, never stored in code
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover
    pass


def user_dir() -> Path:
    p = Path(os.environ.get("EDGELAB_HOME", ROOT / "userdata"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return user_dir() / "edgelab.db"


def settings_path() -> Path:
    return user_dir() / "settings.json"


def api_key(name: str) -> str | None:
    """Read an API key from the environment / .env file (e.g. OANDA_API_KEY)."""
    v = os.environ.get(name, "").strip()
    return v or None


@dataclass
class Settings:
    display_tz: str = "Europe/London"
    account_currency: str = "USD"
    account_size: float = 10_000.0
    risk_pct: float = 0.65
    commission_per_trade: float = 0.0
    forex_spread_pips: float = 1.0
    forex_slippage_pips: float = 0.2
    crypto_fee_pct: float = 0.10       # per side
    crypto_slippage_pct: float = 0.02  # per side
    opening_range_minutes: int = 30
    sessions: dict = field(default_factory=lambda: {k: v.to_dict() for k, v in DEFAULT_SESSIONS.items()})

    def session_for(self, asset_class: str) -> SessionConfig:
        d = self.sessions.get(asset_class)
        return SessionConfig.from_dict(d) if d else DEFAULT_SESSIONS.get(asset_class, DEFAULT_SESSIONS["crypto"])

    @property
    def risk_amount(self) -> float:
        return self.account_size * self.risk_pct / 100.0


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Settings()
    s = Settings()
    for k, v in raw.items():
        if hasattr(s, k):
            setattr(s, k, v)
    # make sure every asset class still has a session
    for k, v in DEFAULT_SESSIONS.items():
        s.sessions.setdefault(k, v.to_dict())
    return s


def save_settings(s: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
