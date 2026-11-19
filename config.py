"""Load and validate config.yaml (with ${ENV_VAR} expansion from the environment / .env)."""
from __future__ import annotations

import os
import re
from datetime import datetime, time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

BASE_DIR = Path(__file__).resolve().parent
_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


class HotPhase(BaseModel):
    start: datetime
    end: datetime
    interval_minutes: int = Field(15, ge=10)


class PollConfig(BaseModel):
    default_interval_minutes: int = Field(30, ge=10)  # never faster than every 10 min
    hot_phase: HotPhase | None = None
    jitter_seconds: int = Field(120, ge=0)
    run_until: datetime | None = None
    delay_between_requests_seconds: float = Field(4.0, ge=1.0)
    max_pages: int = Field(2, ge=1, le=3)  # Geizhals offer list pages per product URL
    user_agent: str = "Mozilla/5.0 (compatible; gpu-price-alert/1.0; private price watcher)"
    timezone: str = "Europe/Berlin"


class FilterConfig(BaseModel):
    max_delivery_days: int = 5
    include_shipping: bool = True
    shop_whitelist: list[str] = Field(default_factory=list)
    exclude_merchant_keywords: list[str] = Field(
        default_factory=lambda: ["marketplace", "ebay", "store", "filiale"]
    )


class RulesConfig(BaseModel):
    drop_percent: float = 8.0
    median_days: int = 7
    new_low_min_history_days: float = 3
    realert_min_extra_drop_percent: float = 2.0
    realert_cooldown_hours: float = 12
    quiet_hours: tuple[time, time] | None = None
    suspicious_below_median_percent: float = 40.0
    great_deal_drop_percent: float = 12.0  # green "Top-Deal": target hit or this far below 7-day median
    daily_report_time: time | None = time(20, 0)


class NtfyConfig(BaseModel):
    enabled: bool = True
    server: str = "https://ntfy.sh"
    topic: str = ""
    token: str | None = None


class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


class NotifyConfig(BaseModel):
    ntfy: NtfyConfig = NtfyConfig()
    telegram: TelegramConfig = TelegramConfig()


class ProductConfig(BaseModel):
    id: str
    name: str
    short_name: str = ""  # used in push titles and the daily report
    url: str | None = None
    urls: list[str] = Field(default_factory=list)
    shop_urls: list[str] = Field(default_factory=list)  # direct shop product pages (Mindfactory/Alternate/Proshop)
    mpns: list[str] = Field(default_factory=list)  # page MPN must exactly equal one of these
    target_price: float = 0  # 0 = rule disabled
    drop_percent: float | None = None  # overrides rules_default.drop_percent
    exclude_keywords: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _merge_urls(self) -> "ProductConfig":
        if self.url:
            self.urls = [self.url, *[u for u in self.urls if u != self.url]]
        if not self.urls:
            raise ValueError(f"product {self.id}: 'url' or 'urls' required")
        return self

    @property
    def label(self) -> str:
        return self.short_name or self.name


class CompareRule(BaseModel):
    """Alert when the cheapest of `b` minus the cheapest of `a` drops below `max_diff`."""

    name: str
    a: list[str]
    b: list[str]
    max_diff: float

    @field_validator("a", "b", mode="before")
    @classmethod
    def _listify(cls, v):
        return [v] if isinstance(v, str) else v


class AppConfig(BaseModel):
    poll: PollConfig = PollConfig()
    filters: FilterConfig = FilterConfig()
    rules_default: RulesConfig = RulesConfig()
    notify: NotifyConfig = NotifyConfig()
    products: list[ProductConfig]
    compare: list[CompareRule] = Field(default_factory=list)
    disabled_sources: list[str] = Field(default_factory=list)  # e.g. ["mindfactory", "proshop"]
    database: str = "data/prices.sqlite3"

    @model_validator(mode="after")
    def _check_ids(self) -> "AppConfig":
        ids = [p.id for p in self.products]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate product ids")
        for c in self.compare:
            for pid in [*c.a, *c.b]:
                if pid not in ids:
                    raise ValueError(f"compare rule {c.name}: unknown product {pid}")
        return self

    @property
    def db_path(self) -> Path:
        p = Path(self.database)
        return p if p.is_absolute() else BASE_DIR / p


def _expand_env(obj):
    if isinstance(obj, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), obj)
    if isinstance(obj, list):
        return [_expand_env(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    return obj


def load_config(path: str | Path | None = None) -> AppConfig:
    load_dotenv(BASE_DIR / ".env")
    path = Path(path or os.environ.get("GPU_ALERT_CONFIG") or BASE_DIR / "config.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return AppConfig.model_validate(_expand_env(raw))
