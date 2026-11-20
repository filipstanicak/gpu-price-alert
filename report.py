"""CLI: current state per card.  Usage:  python report.py  [--config config.yaml]"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import AppConfig, load_config
from notify import eur, eur_whole, pct, pretty_shop
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


_CHIP = re.compile(r"\s*(\d{4}(?:\s+(?:XT|Ti))?)$")


def _split_label(label: str) -> tuple[str, str]:
    """'Red Devil 9070 XT' -> ('RX 9070 XT', 'Red Devil'); 'Astral 5080' -> ('RTX 5080', 'Astral')."""
    m = _CHIP.search(label)
    if not m:
        return "", label
    chip = m.group(1)
    return ("RX " if chip.startswith("9") else "RTX ") + chip, label[:m.start()].strip()


_whole_eur = eur_whole


def daily_report(cfg: AppConfig, st: Storage, now: datetime) -> tuple[str, str]:
    """Returns (title_suffix, body). Grouped by GPU, cheapest first, distance to target per card."""
    labels = {p.id: p.label for p in cfg.products}
    groups: dict[str, list[tuple[float, str]]] = {}
    unavailable: dict[str, list[str]] = {}
    hits = 0
    for r in summary_rows(cfg, st, now):
        chip, model = _split_label(labels[r["id"]])
        if r["current"] is None or st.get_state(f"avail:{r['id']}") == "0":
            unavailable.setdefault(chip, []).append(model)
            groups.setdefault(chip, [])
            continue
        cur, target = r["current"], r["target"]
        line = f"{model}: {_whole_eur(cur)}"
        if target:
            diff = cur - target
            if diff <= 0:
                hits += 1
                line = "🟢 " + line + " · Ziel erreicht"
            else:
                line += f" · {_whole_eur(diff)} über Ziel"
        if r["change"] is not None and r["yesterday"]:
            ch = r["change"] / r["yesterday"] * 100
            if abs(ch) >= 0.5:
                line += f" · {'↓' if ch < 0 else '↑'} {abs(ch):.0f} %".replace(".", ",")
        groups.setdefault(chip, []).append((cur, line))
    blocks = []
    for chip, rows in groups.items():
        lines = [line for _, line in sorted(rows)]
        if unavailable.get(chip):
            lines.append("nicht lieferbar: " + ", ".join(unavailable[chip]))
        blocks.append("\n".join([chip] + lines) if chip else "\n".join(lines))
    suffix = f"🟢 {hits} am Ziel" if hits else "noch nichts am Ziel"
    return suffix, "\n\n".join(blocks)


def daily_report_text(cfg: AppConfig, st: Storage, now: datetime) -> str:
    return daily_report(cfg, st, now)[1]


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
