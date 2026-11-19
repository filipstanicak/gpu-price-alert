"""Offer filtering, alert rules and spam protection. Pure functions where possible (easy to test)."""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta

from config import FilterConfig, ProductConfig, RulesConfig
from models import Alert, Offer, Stats

_DOMAIN_SUFFIX = re.compile(r"\.(de|net|com|at|eu|ch)$")


def normalize_shop(name: str) -> str:
    n = name.strip().lower()
    n = _DOMAIN_SUFFIX.sub("", n)
    return re.sub(r"\s+", " ", n)


def shop_allowed(shop: str, f: FilterConfig) -> bool:
    n = normalize_shop(shop)
    if any(k.lower() in n for k in f.exclude_merchant_keywords):
        return False
    if not f.shop_whitelist:
        return True
    return n in {normalize_shop(w) for w in f.shop_whitelist}


def delivery_ok(offer: Offer, f: FilterConfig) -> bool:
    if offer.in_stock:
        return True
    return offer.delivery_days_max is not None and offer.delivery_days_max <= f.max_delivery_days


def excluded_by_keywords(text: str, keywords: list[str]) -> str | None:
    t = text.lower()
    for k in keywords:
        if k.lower() in t:
            return k
    return None


def filter_offers(offers: list[Offer], product: ProductConfig, f: FilterConfig) -> list[Offer]:
    out = []
    for o in offers:
        if not shop_allowed(o.shop, f):
            continue
        if not delivery_ok(o, f):
            continue
        if excluded_by_keywords(o.title, product.exclude_keywords):
            continue
        if f.include_shipping and o.shipping_eur is None:
            # unknown shipping -> can't compare fairly; skip rather than under-report the price
            continue
        out.append(o)
    return out


def best_offer(offers: list[Offer]) -> Offer | None:
    return min(offers, key=lambda o: (o.total_eur, not o.in_stock)) if offers else None


def evaluate(total: float, product: ProductConfig, r: RulesConfig, stats: Stats) -> list[str]:
    hits = []
    if product.target_price and total <= product.target_price:
        hits.append("target")
    drop = product.drop_percent if product.drop_percent is not None else r.drop_percent
    if stats.median_7d and total <= stats.median_7d * (1 - drop / 100):
        hits.append("drop")
    if (stats.min_ever is not None and stats.history_days >= r.new_low_min_history_days
            and total < stats.min_ever - 0.005):
        hits.append("new_low")
    return hits


def is_suspicious(total: float, stats: Stats, r: RulesConfig) -> bool:
    return bool(stats.median_7d) and total < stats.median_7d * r.suspicious_below_median_percent / 100


def in_quiet_hours(now_local: datetime, quiet: tuple[time, time] | None) -> bool:
    if not quiet:
        return False
    start, end = quiet
    t = now_local.time()
    return start <= t < end if start <= end else (t >= start or t < end)


def spam_check(alert: Alert, r: RulesConfig, now_local: datetime,
               already_sent_same: bool, last_alert: tuple[float, datetime] | None) -> str | None:
    """Return None if the alert may be sent, otherwise the reason it is suppressed."""
    total = alert.offer.total_eur
    if already_sent_same:
        return "same offer already alerted"
    if last_alert:
        last_total, last_at = last_alert
        dropped_enough = total <= last_total * (1 - r.realert_min_extra_drop_percent / 100)
        cooled_down = now_local - last_at >= timedelta(hours=r.realert_cooldown_hours)
        if not (dropped_enough or cooled_down):
            return f"re-alert blocked (last {last_total:.2f} €, {last_at:%d.%m. %H:%M})"
    if in_quiet_hours(now_local, r.quiet_hours) and not alert.is_target_hit:
        return "quiet hours"
    return None
