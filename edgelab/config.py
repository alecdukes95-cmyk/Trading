"""Paths and user settings. Settings live in userdata/settings.json; secrets live in .env."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from edgelab.brokers import BrokerProfile, all_profiles
from edgelab.data.sessions import DEFAULT_SESSIONS, SessionConfig
from edgelab.fx import DEFAULT_REFERENCE_RATES, FxConverter

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
    account_currency: str = "GBP"
    account_balance: float = 10_000.0
    risk_pct: float = 0.65                 # % of account balance risked per trade
    forex_sizing: str = "fixed"            # "fixed" units per trade, or "risk" (size from stop distance)
    forex_fixed_units: int = 10_000        # 1 mini lot
    crypto_sizing: str = "risk"
    active_broker: str = "Generic UK broker (spread only)"
    brokers: dict = field(default_factory=dict)          # your edited/added broker profiles
    reference_rates: dict = field(default_factory=lambda: dict(DEFAULT_REFERENCE_RATES))
    opening_range_minutes: int = 30
    sessions: dict = field(default_factory=lambda: {k: v.to_dict() for k, v in DEFAULT_SESSIONS.items()})

    def session_for(self, asset_class: str) -> SessionConfig:
        d = self.sessions.get(asset_class)
        return SessionConfig.from_dict(d) if d else DEFAULT_SESSIONS.get(asset_class, DEFAULT_SESSIONS["crypto"])

    @property
    def risk_amount(self) -> float:
        return self.account_balance * self.risk_pct / 100.0

    def broker_profiles(self) -> dict[str, BrokerProfile]:
        return all_profiles(self.brokers)

    def broker(self) -> BrokerProfile:
        profiles = self.broker_profiles()
        return profiles.get(self.active_broker) or next(iter(profiles.values()))

    def converter(self) -> FxConverter:
        return FxConverter(reference=dict(self.reference_rates))


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Settings()
    s = Settings()
    if "account_size" in raw and "account_balance" not in raw:  # settings from version 0.1
        raw["account_balance"] = raw["account_size"]
    for k, v in raw.items():
        if hasattr(s, k):
            setattr(s, k, v)
    # make sure every asset class still has a session
    for k, v in DEFAULT_SESSIONS.items():
        s.sessions.setdefault(k, v.to_dict())
    for k, v in DEFAULT_REFERENCE_RATES.items():
        s.reference_rates.setdefault(k, v)
    return s


def save_settings(s: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
