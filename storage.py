"""SQLite storage: price history, sent alerts, small key/value state."""
from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from models import Offer, Stats

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_points (
    id INTEGER PRIMARY KEY,
    product_id TEXT NOT NULL,
    shop TEXT NOT NULL,
    price_eur REAL NOT NULL,
    shipping_eur REAL NOT NULL,
    total_eur REAL NOT NULL,
    in_stock INTEGER NOT NULL,
    delivery_text TEXT,
    offer_url TEXT,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pp_product_time ON price_points(product_id, fetched_at);
CREATE TABLE IF NOT EXISTS alerts_sent (
    id INTEGER PRIMARY KEY,
    product_id TEXT NOT NULL,
    shop TEXT NOT NULL,
    total_eur REAL NOT NULL,
    rule TEXT NOT NULL,
    sent_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alerts_product ON alerts_sent(product_id, sent_at);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class Storage:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # --- price history -------------------------------------------------
    def add_price_point(self, product_id: str, offer: Offer, at: datetime) -> None:
        self.db.execute(
            "INSERT INTO price_points(product_id, shop, price_eur, shipping_eur, total_eur,"
            " in_stock, delivery_text, offer_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (product_id, offer.shop, offer.price_eur, offer.shipping_eur or 0.0, offer.total_eur,
             int(offer.in_stock), offer.delivery_text, offer.offer_url, _iso(at)),
        )
        self.db.commit()

    def stats(self, product_id: str, now: datetime, median_days: int = 7) -> Stats:
        cur = self.db.execute(
            "SELECT MIN(total_eur) mn, MIN(fetched_at) first FROM price_points WHERE product_id=?",
            (product_id,),
        ).fetchone()
        st = Stats()
        if cur["first"] is None:
            return st
        st.min_ever = cur["mn"]
        st.history_days = (now - _dt(cur["first"])).total_seconds() / 86400
        since = _iso(now - timedelta(days=median_days))
        vals = [r[0] for r in self.db.execute(
            "SELECT total_eur FROM price_points WHERE product_id=? AND fetched_at>=?",
            (product_id, since))]
        st.median_7d = statistics.median(vals) if vals else None
        last = self.db.execute(
            "SELECT total_eur FROM price_points WHERE product_id=? ORDER BY fetched_at DESC LIMIT 1",
            (product_id,)).fetchone()
        st.prev_best = last[0] if last else None
        return st

    def latest_point(self, product_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM price_points WHERE product_id=? ORDER BY fetched_at DESC LIMIT 1",
            (product_id,)).fetchone()

    def best_in_window(self, product_id: str, start: datetime, end: datetime) -> float | None:
        r = self.db.execute(
            "SELECT MIN(total_eur) FROM price_points WHERE product_id=? AND fetched_at>=? AND fetched_at<?",
            (product_id, _iso(start), _iso(end))).fetchone()
        return r[0]

    def last_in_window(self, product_id: str, start: datetime, end: datetime) -> float | None:
        r = self.db.execute(
            "SELECT total_eur FROM price_points WHERE product_id=? AND fetched_at>=? AND fetched_at<?"
            " ORDER BY fetched_at DESC LIMIT 1",
            (product_id, _iso(start), _iso(end))).fetchone()
        return r[0] if r else None

    def history(self, product_id: str) -> list[sqlite3.Row]:
        return list(self.db.execute(
            "SELECT fetched_at, total_eur, shop FROM price_points WHERE product_id=? ORDER BY fetched_at",
            (product_id,)))

    # --- alerts ----------------------------------------------------------
    def alert_exists(self, product_id: str, shop: str, total_eur: float) -> bool:
        return self.db.execute(
            "SELECT 1 FROM alerts_sent WHERE product_id=? AND shop=? AND ABS(total_eur-?)<0.005 LIMIT 1",
            (product_id, shop, total_eur)).fetchone() is not None

    def last_alert(self, product_id: str) -> tuple[float, datetime] | None:
        r = self.db.execute(
            "SELECT total_eur, sent_at FROM alerts_sent WHERE product_id=? ORDER BY sent_at DESC LIMIT 1",
            (product_id,)).fetchone()
        return (r["total_eur"], _dt(r["sent_at"])) if r else None

    def record_alert(self, product_id: str, shop: str, total_eur: float, rule: str, at: datetime) -> None:
        self.db.execute(
            "INSERT INTO alerts_sent(product_id, shop, total_eur, rule, sent_at) VALUES (?,?,?,?,?)",
            (product_id, shop, total_eur, rule, _iso(at)))
        self.db.commit()

    # --- key/value state -----------------------------------------------
    def get_state(self, key: str) -> str | None:
        r = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def set_state(self, key: str, value: str | None) -> None:
        if value is None:
            self.db.execute("DELETE FROM state WHERE key=?", (key,))
        else:
            self.db.execute("INSERT OR REPLACE INTO state(key, value) VALUES (?,?)", (key, value))
        self.db.commit()
