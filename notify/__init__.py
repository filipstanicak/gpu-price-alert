"""Notification formatting + dispatch to all enabled channels."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from config import NotifyConfig
from models import Alert

log = logging.getLogger(__name__)

RULE_LABELS = {"target": "Zielpreis", "drop": "Preissturz", "new_low": "Neuer Tiefstpreis"}


@dataclass
class Notification:
    title: str
    message: str
    priority: str = "default"  # "high" | "default" | "low"
    click_url: str | None = None
    tags: list[str] = field(default_factory=list)


def eur(v: float | None) -> str:
    if v is None:
        return "–"
    if abs(v - round(v)) < 0.005:
        return f"{round(v):,} €".replace(",", ".")
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v: float) -> str:
    return f"{v:+.1f} %".replace(".", ",").replace("-", "−")


def format_alert(a: Alert) -> Notification:
    o, st = a.offer, a.stats
    total = o.total_eur
    title = f"{'⚠️' if a.suspicious else '🔥'} {a.product_name} – {eur(total)} bei {o.shop}"
    parts = []
    for r in a.rules:
        if r == "target":
            parts.append(f"Zielpreis (≤ {eur(a.target_price)}) erreicht")
        elif r == "drop" and st.median_7d:
            parts.append(f"Preissturz ({pct((total / st.median_7d - 1) * 100)} ggü. 7-Tage-Median)")
        else:
            parts.append(RULE_LABELS.get(r, r))
    lines = [f"Regel: {' + '.join(parts)}"]
    med = f"{eur(st.median_7d)} ({pct((total / st.median_7d - 1) * 100)})" if st.median_7d else "–"
    lines.append(f"7-Tage-Median: {med} | Bisheriger Tiefstpreis: {eur(st.min_ever)}")
    ship = "inkl. Versand" if not o.shipping_eur else f"inkl. {eur(o.shipping_eur)} Versand"
    lines.append(f"Preis: {eur(o.price_eur)} ({ship})")
    lines.append(f"Lieferbar: {'sofort' if o.in_stock else ''} {o.delivery_text}".strip())
    if a.suspicious:
        lines.append("⚠️ VERDÄCHTIG: weit unter Median – Fehlpreis oder falsches Produkt? Genau prüfen!")
    lines.append(f"👉 {o.offer_url}")
    return Notification(
        title=title,
        message="\n".join(lines),
        priority="high" if a.is_target_hit else "default",
        click_url=o.offer_url,
        tags=["moneybag"] if a.is_target_hit else ["chart_with_downwards_trend"],
    )


class Dispatcher:
    def __init__(self, cfg: NotifyConfig, dry_run: bool = False):
        self.dry_run = dry_run
        self.channels = []
        if cfg.ntfy.enabled and cfg.ntfy.topic:
            from notify.ntfy import NtfyNotifier
            self.channels.append(NtfyNotifier(cfg.ntfy))
        if cfg.telegram.enabled and cfg.telegram.bot_token and cfg.telegram.chat_id:
            from notify.telegram import TelegramNotifier
            self.channels.append(TelegramNotifier(cfg.telegram))
        if not self.channels and not dry_run:
            log.warning("no notification channel configured – alerts will only be logged")

    def send(self, n: Notification) -> bool:
        """Send to all channels. True if at least one channel succeeded.

        Dry run returns False so nothing is recorded as 'sent' in the database.
        """
        if self.dry_run or not self.channels:
            log.info("[NOTIFY %s] %s\n%s", n.priority, n.title, n.message)
            return False
        ok = False
        for ch in self.channels:
            try:
                ch.send(n)
                ok = True
            except Exception as e:  # one broken channel must not stop the others
                log.error("notification via %s failed: %s", type(ch).__name__, e)
        return ok
