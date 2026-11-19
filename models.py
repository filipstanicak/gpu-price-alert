from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Offer:
    shop: str
    price_eur: float
    shipping_eur: float | None  # None = could not be determined
    in_stock: bool
    delivery_text: str
    delivery_days_max: int | None
    offer_url: str
    title: str = ""
    source: str = ""
    status: str = ""  # source-specific raw status, e.g. "available"/"shortly"/"unavailable"

    @property
    def total_eur(self) -> float:
        return round(self.price_eur + (self.shipping_eur or 0.0), 2)


@dataclass
class PricePoint:
    product_id: str
    shop: str
    price_eur: float
    shipping_eur: float
    total_eur: float
    in_stock: bool
    delivery_text: str
    offer_url: str
    fetched_at: datetime


@dataclass
class Stats:
    """History of a product *before* the current price point was stored."""

    median_7d: float | None = None
    min_ever: float | None = None
    history_days: float = 0.0
    prev_best: float | None = None  # last stored best price


@dataclass
class Alert:
    product_id: str
    product_name: str
    offer: Offer
    rules: list[str]  # "target", "drop", "new_low"
    stats: Stats
    target_price: float = 0
    suspicious: bool = False
    great: bool = False  # worth buying -> green "Top-Deal"
    extra: dict = field(default_factory=dict)

    @property
    def is_target_hit(self) -> bool:
        return "target" in self.rules
