"""CLI: current state per card.  Usage:  python report.py  [--config config.yaml]"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import AppConfig, load_config
from notify import eur, pct
from storage import Storage


def summary_rows(cfg: AppConfig, st: Storage, now: datetime) -> list[dict]:
    tz = ZoneInfo(cfg.poll.timezone)
    today0 = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for p in cfg.products:
        last = st.latest_point(p.id)
        s = st.stats(p.id, now, cfg.rules_default.median_days)
        yesterday = st.last_in_window(p.id, today0 - timedelta(days=1), today0)
        cur = last["total_eur"] if last else None
        rows.append({
            "id": p.id, "name": p.name, "current": cur,
            "shop": last["shop"] if last else "", "fetched_at": last["fetched_at"] if last else "",
            "yesterday": yesterday,
            "change": (cur - yesterday) if cur is not None and yesterday is not None else None,
            "median_7d": s.median_7d, "min_ever": s.min_ever, "target": p.target_price or None,
            "url": last["offer_url"] if last else p.urls[0],
        })
    return rows


def daily_report_text(cfg: AppConfig, st: Storage, now: datetime) -> str:
    lines = []
    for r in summary_rows(cfg, st, now):
        chg = ""
        if r["change"] is not None and r["yesterday"]:
            chg = f" ({pct(r['change'] / r['yesterday'] * 100)} ggü. gestern)"
        lines.append(f"• {r['name']}: {eur(r['current'])} @ {r['shop'] or '–'}{chg} | Tief: {eur(r['min_ever'])}")
    return "\n".join(lines)


def print_table(rows: list[dict]) -> None:
    hdr = ["Karte", "Aktuell", "Shop", "Δ Vortag", "Median 7T", "Tiefst", "Ziel", "Stand (UTC)"]
    data = [[r["name"], eur(r["current"]), r["shop"] or "–",
             (eur(r["change"]).replace("€", "").strip() + " €") if r["change"] is not None else "–",
             eur(r["median_7d"]), eur(r["min_ever"]), eur(r["target"]), r["fetched_at"][:16].replace("T", " ")]
            for r in rows]
    widths = [max(len(str(x)) for x in col) for col in zip(hdr, *data)]
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*hdr))
    print("  ".join("-" * w for w in widths))
    for d in data:
        print(fmt.format(*d))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config(args.config)
    st = Storage(cfg.db_path)
    print_table(summary_rows(cfg, st, datetime.now(ZoneInfo(cfg.poll.timezone))))


if __name__ == "__main__":
    main()
