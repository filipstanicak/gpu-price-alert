"""GPU price alert – scheduler / entry point.

  python main.py                 run until poll.run_until
  python main.py --once          single pass (e.g. for cron)
  python main.py --dry-run       log notifications instead of sending
  python main.py --test-notify   send one sample push and exit
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time as _time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import rules
from config import AppConfig, ProductConfig, load_config
from models import Alert, Offer, Stats
from notify import Dispatcher, Notification, eur, format_alert, format_back_in_stock, pretty_shop
from report import daily_report_text
from sources.base import ParserError, SourceError
from sources.geizhals import GeizhalsSource
from sources.shops import SHOP_SOURCES
from storage import Storage

log = logging.getLogger("gpu-alert")
FAIL_WARN_AFTER = 3  # consecutive network failures before a warning push (parser errors warn at once)


def prefer_direct_offers(offers: list[Offer]) -> list[Offer]:
    """A price read directly from the shop is fresher than Geizhals' copy of it (often 30-60 min old):
    drop Geizhals offers of every shop that was successfully read directly."""
    direct = {rules.normalize_shop(o.shop) for o in offers if o.source and o.source != "geizhals"}
    return [o for o in offers if o.source != "geizhals" or rules.normalize_shop(o.shop) not in direct]


class App:
    def __init__(self, cfg: AppConfig, storage: Storage, dispatcher: Dispatcher, sources=None):
        self.cfg = cfg
        self.st = storage
        self.notify = dispatcher
        self.sources = sources if sources is not None else [
            s for s in [GeizhalsSource(cfg.poll), *(cls(cfg.poll) for cls in SHOP_SOURCES)]
            if s.name not in cfg.disabled_sources]
        self.tz = ZoneInfo(cfg.poll.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    # --- one polling pass ---------------------------------------------------
    def run_cycle(self, now: datetime | None = None) -> dict[str, Offer | None]:
        now = now or self.now()
        bests: dict[str, Offer | None] = {}
        for product in self.cfg.products:
            try:
                bests[product.id] = self.process_product(product, now)
            except Exception:  # never let one product kill the loop
                log.exception("unexpected error for %s", product.id)
                bests[product.id] = None
        self.check_compare_rules(bests, now)
        self.maybe_daily_report(now)
        return bests

    def fetch_offers(self, product: ProductConfig) -> list[Offer] | None:
        offers: list[Offer] = []
        any_ok = False
        for src in self.sources:
            if not any(src.handles(u) for u in [*product.urls, *product.shop_urls]):
                continue
            key = f"{product.id}:{src.name}"
            try:
                offers.extend(src.fetch(product))
                any_ok = True
                self._source_ok(key, product)
            except (SourceError, ParserError) as e:
                self._source_failed(key, product, src.name, e)
        return prefer_direct_offers(offers) if any_ok else None

    def process_product(self, product: ProductConfig, now: datetime) -> Offer | None:
        offers = self.fetch_offers(product)
        if offers is None:
            return None
        valid = rules.filter_offers(offers, product, self.cfg.filters)
        best = rules.best_offer(valid)
        was_available = self.st.get_state(f"avail:{product.id}")
        self.st.set_state(f"avail:{product.id}", "1" if best else "0")
        if best is None:
            log.info("%-14s %d offers, none valid (whitelist/stock)", product.id, len(offers))
            return None
        if was_available == "0":  # not on the very first run, only on a real 0 -> 1 transition
            self.notify.send(format_back_in_stock(product.label, best))
        r = self.cfg.rules_default
        stats = self.st.stats(product.id, now, r.median_days)  # history BEFORE this point
        self.st.add_price_point(product.id, best, now)
        hits = rules.evaluate(best.total_eur, product, r, stats)
        log.info("%-14s best %s @ %s (%d/%d valid) median=%s min=%s rules=%s", product.id,
                 eur(best.total_eur), best.shop, len(valid), len(offers),
                 eur(stats.median_7d), eur(stats.min_ever), hits or "-")
        if hits:
            suspicious = rules.is_suspicious(best.total_eur, stats, r)
            alert = Alert(product.id, product.label, best, hits, stats, product.target_price,
                          suspicious=suspicious,
                          great=rules.is_great_deal(best.total_eur, hits, stats, r, suspicious))
            self.maybe_send_alert(alert, now)
        return best

    def maybe_send_alert(self, alert: Alert, now: datetime) -> bool:
        o = alert.offer
        reason = rules.spam_check(
            alert, self.cfg.rules_default, now,
            already_sent_same=self.st.alert_exists(alert.product_id, o.shop, o.total_eur),
            last_alert=self.st.last_alert(alert.product_id),
        )
        if reason:
            log.info("%-14s alert suppressed: %s", alert.product_id, reason)
            return False
        if self.notify.send(format_alert(alert)):
            self.st.record_alert(alert.product_id, o.shop, o.total_eur, "+".join(alert.rules), now)
            return True
        return False

    # --- source health warnings ---------------------------------------------
    def _source_failed(self, key: str, product: ProductConfig, source: str, err: Exception) -> None:
        n = int(self.st.get_state(f"fail:{key}") or 0) + 1
        self.st.set_state(f"fail:{key}", str(n))
        log.warning("%-14s source %s failed (%dx): %s", product.id, source, n, err)
        if self.st.get_state(f"warned:{key}"):
            return
        if isinstance(err, ParserError) or n >= FAIL_WARN_AFTER:
            self.notify.send(Notification(
                title=f"⚠️ Störung · {source.capitalize()} · {product.label}",
                message=f"{err}\nDie App läuft weiter. Diese Meldung kommt nur einmal.",
                priority="low", click_url=product.urls[0]))
            self.st.set_state(f"warned:{key}", "1")

    def _source_ok(self, key: str, product: ProductConfig) -> None:
        if self.st.get_state(f"warned:{key}"):
            self.notify.send(Notification(title=f"✅ Wieder ok · {key.split(':')[1].capitalize()} · {product.label}",
                                          message="Preise werden wieder abgerufen.", priority="min"))
        self.st.set_state(f"warned:{key}", None)
        self.st.set_state(f"fail:{key}", None)

    # --- extras -------------------------------------------------------------
    def check_compare_rules(self, bests: dict[str, Offer | None], now: datetime) -> None:
        names = {p.id: p.label for p in self.cfg.products}
        for c in self.cfg.compare:
            a_ids = [pid for pid in c.a if bests.get(pid)]
            b_ids = [pid for pid in c.b if bests.get(pid)]
            if not a_ids or not b_ids:
                continue
            a_id = min(a_ids, key=lambda pid: bests[pid].total_eur)
            b_id = min(b_ids, key=lambda pid: bests[pid].total_eur)
            a, b = bests[a_id], bests[b_id]
            diff = b.total_eur - a.total_eur
            if diff >= c.max_diff:
                continue
            key = f"cmp:{c.name}"
            last = self.st.get_state(key)
            if last:
                last_diff, last_at = last.split("|")
                if (now - datetime.fromisoformat(last_at) < timedelta(hours=self.cfg.rules_default.realert_cooldown_hours)
                        and diff > float(last_diff) - 25):
                    continue
            ok = self.notify.send(Notification(
                title=f"⚖️ Vergleich · {c.name} · nur {eur(diff)} Abstand",
                message=(f"{names[a_id]}: {eur(a.total_eur)} · {pretty_shop(a.shop)}\n"
                         f"{names[b_id]}: {eur(b.total_eur)} · {pretty_shop(b.shop)}\n"
                         f"Schwelle: unter {eur(c.max_diff)}"),
                click_url=b.offer_url))
            if ok:
                self.st.set_state(key, f"{diff}|{now.isoformat()}")

    def maybe_daily_report(self, now: datetime) -> None:
        t = self.cfg.rules_default.daily_report_time
        if not t or now.astimezone(self.tz).time() < t:
            return
        today = now.astimezone(self.tz).date().isoformat()
        if self.st.get_state("daily_report") == today:
            return
        if self.notify.send(Notification(title=f"📊 Tagesreport · {now.astimezone(self.tz):%d.%m.}",
                                         message=daily_report_text(self.cfg, self.st, now), priority="low",
                                         click_url=self.cfg.dashboard_url)):
            self.st.set_state("daily_report", today)

    # --- scheduling -------------------------------------------------------------
    def interval_seconds(self, now: datetime) -> float:
        p = self.cfg.poll
        minutes = p.default_interval_minutes
        if p.hot_phase and p.hot_phase.start <= now <= p.hot_phase.end:
            minutes = p.hot_phase.interval_minutes
        secs = minutes * 60 + random.uniform(-p.jitter_seconds, p.jitter_seconds)
        return max(secs, 600)  # hard floor: never faster than every 10 minutes

    def run_forever(self) -> None:
        until = self.cfg.poll.run_until
        while True:
            now = self.now()
            if until and now > until:
                log.info("run_until %s reached – stopping", until)
                return
            self.run_cycle(now)
            wait = self.interval_seconds(self.now())
            log.info("next run in %.1f min", wait / 60)
            _time.sleep(wait)


def sample_notification() -> Notification:
    offer = Offer(shop="alternate.de", price_eur=949, shipping_eur=0, in_stock=True,
                  delivery_text="Lagernd, Lieferung 1-2 Werktage", delivery_days_max=0,
                  offer_url="https://geizhals.de/powercolor-red-devil-radeon-rx-9070-xt-a3397494.html")
    a = Alert("rd-9070xt", "Red Devil 9070 XT (TEST)", offer, ["target", "new_low"],
              Stats(median_7d=1063.99, min_ever=1063.99, history_days=5), target_price=979, great=True)
    return format_alert(a)


def main() -> None:
    ap = argparse.ArgumentParser(description="GPU Black-Week price alert")
    ap.add_argument("--config")
    ap.add_argument("--db", help="override database path (e.g. for test runs)")
    ap.add_argument("--once", action="store_true", help="single polling pass, then exit")
    ap.add_argument("--force", action="store_true", help="with --once: ignore the 10-minute minimum gap")
    ap.add_argument("--dry-run", action="store_true", help="log notifications instead of sending")
    ap.add_argument("--test-notify", action="store_true", help="send a sample push and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = load_config(args.config)
    if args.db:
        cfg.database = args.db
    dispatcher = Dispatcher(cfg.notify, dry_run=args.dry_run)
    if args.test_notify:
        ok = dispatcher.send(sample_notification())
        print("Test-Push gesendet." if ok else "Test-Push fehlgeschlagen – Log prüfen.")
        sys.exit(0 if ok else 1)

    app = App(cfg, Storage(cfg.db_path), dispatcher)
    if args.once:
        now = app.now()
        if cfg.poll.run_until and now > cfg.poll.run_until:
            log.info("run_until %s reached – nothing to do", cfg.poll.run_until)
            return
        last = app.st.get_state("last_run")
        if last and not args.force and now - datetime.fromisoformat(last) < timedelta(minutes=10):
            log.info("last run %s less than 10 min ago – skipping (use --force)", last)
            return
        app.st.set_state("last_run", now.isoformat())
        app.run_cycle(now)
    else:
        app.run_forever()


if __name__ == "__main__":
    main()
