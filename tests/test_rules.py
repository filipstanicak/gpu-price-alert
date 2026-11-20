from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from config import AppConfig, NotifyConfig, ProductConfig, RulesConfig
from main import App
from models import Alert, Offer, Stats
from notify import Dispatcher, format_alert
from rules import evaluate, in_quiet_hours, is_suspicious, shop_allowed, spam_check
from sources.base import ParserError
from storage import Storage

TZ = ZoneInfo("Europe/Berlin")
R = RulesConfig(quiet_hours=(time(0), time(7)), daily_report_time=None)
P = ProductConfig(id="rd", name="Red Devil", url="https://geizhals.de/x-a1.html", target_price=850)


def offer(total, shop="Mindfactory"):
    return Offer(shop=shop, price_eur=total, shipping_eur=0.0, in_stock=True, delivery_text="Lagernd",
                 delivery_days_max=0, offer_url="https://example.invalid/o")


def test_target_rule():
    assert evaluate(850, P, R, Stats()) == ["target"]
    assert evaluate(851, P, R, Stats()) == []
    assert evaluate(100, P.model_copy(update={"target_price": 0}), R, Stats()) == []  # 0 = disabled


def test_drop_rule():
    st = Stats(median_7d=1000)
    assert "drop" in evaluate(920, P, R, st)  # exactly -8 %
    assert "drop" not in evaluate(921, P, R, st)


def test_new_low_needs_history():
    assert "new_low" not in evaluate(900, P, R, Stats(min_ever=950, history_days=2.9))
    assert "new_low" in evaluate(900, P, R, Stats(min_ever=950, history_days=3.1))
    assert "new_low" not in evaluate(950, P, R, Stats(min_ever=950, history_days=10))


def test_suspicious():
    assert is_suspicious(390, Stats(median_7d=1000), R)
    assert not is_suspicious(410, Stats(median_7d=1000), R)


def test_shop_whitelist():
    from config import FilterConfig
    f = FilterConfig(shop_whitelist=["Amazon.de", "Notebooksbilliger", "Cyberport"],
                     exclude_merchant_keywords=["marketplace", "stores"])
    assert shop_allowed("amazon.de", f)
    assert shop_allowed("notebooksbilliger.de", f)
    assert not shop_allowed("Amazon Marketplace", f)
    assert not shop_allowed("Cyberport Stores Deutschland", f)
    assert not shop_allowed("x-kom", f)


def test_quiet_hours():
    assert in_quiet_hours(datetime(2026, 11, 27, 3, 0, tzinfo=TZ), R.quiet_hours)
    assert not in_quiet_hours(datetime(2026, 11, 27, 7, 0, tzinfo=TZ), R.quiet_hours)
    assert in_quiet_hours(datetime(2026, 11, 27, 23, 30, tzinfo=TZ), (time(23), time(6)))


def test_spam_protection():
    now = datetime(2026, 11, 27, 12, 0, tzinfo=TZ)
    a = Alert("rd", "RD", offer(900), ["drop"], Stats())
    assert spam_check(a, R, now, False, None) is None
    assert spam_check(a, R, now, True, None) == "same offer already alerted"
    # last alert 1h ago at 910 -> 900 is only -1.1 % -> blocked
    assert spam_check(a, R, now, False, (910, now - timedelta(hours=1))) is not None
    # -2 % or more -> allowed
    a2 = Alert("rd", "RD", offer(891), ["drop"], Stats())
    assert spam_check(a2, R, now, False, (910, now - timedelta(hours=1))) is None
    # 12h later -> allowed again
    assert spam_check(a, R, now, False, (910, now - timedelta(hours=12))) is None
    # quiet hours: drop blocked, target passes
    night = datetime(2026, 11, 27, 2, 0, tzinfo=TZ)
    assert spam_check(a, R, night, False, None) == "quiet hours"
    t = Alert("rd", "RD", offer(800), ["target"], Stats())
    assert spam_check(t, R, night, False, None) is None


def test_format_alert_top_deal_is_green():
    a = Alert("rd", "Red Devil 9070 XT", offer(829, shop="alternate.de"), ["target"],
              Stats(median_7d=899, min_ever=869), target_price=850, great=True)
    n = format_alert(a)
    assert n.title == "🟢 Top-Deal · Red Devil 9070 XT · 829 €"
    assert n.message.splitlines() == [
        "Alternate · auf Lager",
        "829 € inkl. Versand · Ziel 850 € ✓",
        "−7,8 % ggü. 7-Tage-Schnitt (899 €)",
        "Bisheriges Tief: 869 €",
    ]
    assert n.priority == "urgent" and "http" not in n.message and n.click_url


def test_format_alert_plain_drop_and_suspicious():
    st = Stats(median_7d=1000, min_ever=990)
    n = format_alert(Alert("rd", "Red Devil", offer(910), ["drop"], st))
    assert n.title.startswith("📉 Preissturz ·") and n.priority == "default"
    s = format_alert(Alert("rd", "Red Devil", offer(350), ["drop", "target"], st, 979, suspicious=True))
    assert s.title.startswith("⚠️ Prüfen ·") and "Fehlpreis" in s.message


def test_great_deal_rules():
    from rules import is_great_deal
    st = Stats(median_7d=1000)
    assert is_great_deal(970, ["target"], st, R, False)
    assert is_great_deal(880, ["drop"], st, R, False)          # -12 %
    assert not is_great_deal(900, ["drop"], st, R, False)      # only -10 %
    assert not is_great_deal(300, ["target"], st, R, True)     # suspicious never green


# --- end-to-end through App with fake source + recording dispatcher --------------
class FakeSource:
    name = "fake"

    def __init__(self):
        self.prices = {}
        self.error = None

    def handles(self, url):
        return True

    def fetch(self, product):
        if self.error:
            raise self.error
        p = self.prices.get(product.id)
        return [] if p is None else [offer(p)]


class RecDispatcher(Dispatcher):
    def __init__(self):
        super().__init__(NotifyConfig(), dry_run=True)
        self.sent = []

    def send(self, n):
        self.sent.append(n)
        return True


def make_app(target=0):
    cfg = AppConfig(products=[P.model_copy(update={"target_price": target})], rules_default=R)
    src, rec = FakeSource(), RecDispatcher()
    return App(cfg, Storage(":memory:"), rec, sources=[src]), src, rec


def test_e2e_target_alert_once():
    app, src, rec = make_app(target=5000)  # acceptance test: target set very high -> alert
    t0 = datetime(2026, 11, 20, 12, 0, tzinfo=TZ)
    src.prices["rd"] = 1000
    app.run_cycle(t0)
    assert len(rec.sent) == 1 and rec.sent[0].title.startswith("🟢 Top-Deal")
    app.run_cycle(t0 + timedelta(minutes=30))  # same offer -> no duplicate
    assert len(rec.sent) == 1


def test_e2e_drop_and_new_low():
    app, src, rec = make_app()
    t = datetime(2026, 11, 20, 12, 0, tzinfo=TZ)
    for i in range(4 * 24 // 6):  # 4 days of history @ 1000 €
        src.prices["rd"] = 1000
        app.run_cycle(t + timedelta(hours=6 * i))
    assert rec.sent == []
    src.prices["rd"] = 900
    app.run_cycle(t + timedelta(days=4, hours=1))
    assert len(rec.sent) == 1
    assert rec.sent[0].title.startswith("📉 Neuer Tiefstpreis") and "→ neues Tief" in rec.sent[0].message


def test_e2e_source_failure_warns_once_then_recovers():
    app, src, rec = make_app()
    t = datetime(2026, 11, 20, 12, 0, tzinfo=TZ)
    src.error = ParserError("layout changed")
    app.run_cycle(t)
    app.run_cycle(t + timedelta(minutes=30))
    assert [n.title for n in rec.sent] == ["⚠️ Störung · Fake · Red Devil"]
    src.error = None
    src.prices["rd"] = 1000
    app.run_cycle(t + timedelta(hours=1))
    assert rec.sent[-1].title.startswith("✅")


def test_e2e_back_in_stock():
    app, src, rec = make_app()
    t = datetime(2026, 11, 20, 12, 0, tzinfo=TZ)
    app.run_cycle(t)  # no offers
    src.prices["rd"] = 1200
    app.run_cycle(t + timedelta(minutes=30))
    assert rec.sent and rec.sent[0].title.startswith("📦 Wieder lieferbar")


def test_interval_respects_floor_and_hot_phase():
    from config import HotPhase, PollConfig
    app, _, _ = make_app()
    app.cfg.poll = PollConfig(default_interval_minutes=30, jitter_seconds=120, hot_phase=HotPhase(
        start=datetime(2026, 11, 26, tzinfo=TZ), end=datetime(2026, 11, 30, 23, 59, tzinfo=TZ), interval_minutes=10))
    hot = app.interval_seconds(datetime(2026, 11, 27, 12, tzinfo=TZ))
    assert 600 <= hot <= 720
    normal = app.interval_seconds(datetime(2026, 11, 20, 12, tzinfo=TZ))
    assert 1680 <= normal <= 1920


def test_compare_rule_uses_cheapest_on_each_side():
    from config import CompareRule
    prods = [P.model_copy(update={"id": i, "short_name": i}) for i in ("a1", "a2", "b1", "b2")]
    cfg = AppConfig(products=prods, rules_default=R,
                    compare=[CompareRule(name="AMD vs NV", a=["a1", "a2"], b=["b1", "b2"], max_diff=150)])
    rec = RecDispatcher()
    app = App(cfg, Storage(":memory:"), rec, sources=[])
    t = datetime(2026, 11, 20, 12, 0, tzinfo=TZ)
    app.check_compare_rules({"a1": offer(1000), "a2": offer(950), "b1": offer(1200), "b2": offer(1090)}, t)
    assert len(rec.sent) == 1 and "140 €" in rec.sent[0].title and "a2" in rec.sent[0].message
    app.check_compare_rules({"a1": offer(1000), "a2": offer(950), "b1": offer(1200), "b2": offer(1200)}, t)
    assert len(rec.sent) == 1  # 250 € gap -> no alert


def test_is_due_respects_interval_and_hot_phase():
    from config import HotPhase, PollConfig
    app, _, _ = make_app()
    app.cfg.poll = PollConfig(default_interval_minutes=30, hot_phase=HotPhase(
        start=datetime(2026, 11, 26, tzinfo=TZ), end=datetime(2026, 11, 30, 23, 59, tzinfo=TZ), interval_minutes=15))
    normal = datetime(2026, 11, 20, 12, 0, tzinfo=TZ)
    assert app.is_due(normal)  # first run ever
    app.st.set_state("last_run", normal.isoformat())
    assert not app.is_due(normal + timedelta(minutes=15))  # 15-min trigger, normal phase -> skip
    assert app.is_due(normal + timedelta(minutes=29))      # within 3 min tolerance of 30
    hot = datetime(2026, 11, 27, 12, 0, tzinfo=TZ)
    app.st.set_state("last_run", hot.isoformat())
    assert not app.is_due(hot + timedelta(minutes=5))
    assert app.is_due(hot + timedelta(minutes=14))          # every 15-min trigger polls in the hot phase
