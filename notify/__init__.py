"""Notification formatting + dispatch to all enabled channels.

Push design (short, scannable on a lock screen):
  title:  <marker> <kind> · <card> · <price>
  body:   shop · availability
          price details / comparison lines
The link sits behind the notification tap + a "Zum Angebot" button, not in the text.

Markers: 🟢 Top-Deal (worth buying)  📉 price drop / new low  ⚠️ check carefully  📦 back in stock
ntfy cannot colour notifications, so "green" = 🟢 marker + highest priority.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from config import NotifyConfig
from models import Alert, Offer

log = logging.getLogger(__name__)


@dataclass
class Notification:
    title: str
    message: str
    priority: str = "default"  # "urgent" | "high" | "default" | "low" | "min"
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


def pretty_shop(name: str) -> str:
    n = re.sub(r"\.(de|net|com|eu|at)$", "", name.strip(), flags=re.I)
    return n[:1].upper() + n[1:] if n.islower() else n


def availability(o: Offer) -> str:
    if o.in_stock:
        return "auf Lager"
    text = re.split(r"[.|]", o.delivery_text or "")[0].strip()
    return text[:40] or "Lieferzeit unbekannt"


def format_alert(a: Alert) -> Notification:
    o, st = a.offer, a.stats
    total = o.total_eur
    if a.suspicious:
        marker, kind, prio = "⚠️", "Prüfen", "high"
    elif a.great:
        marker, kind, prio = "🟢", "Top-Deal", "urgent"
    elif "new_low" in a.rules:
        marker, kind, prio = "📉", "Neuer Tiefstpreis", "default"
    else:
        marker, kind, prio = "📉", "Preissturz", "default"

    lines = [f"{pretty_shop(o.shop)} · {availability(o)}"]
    if o.shipping_eur is None:  # shipping ignored: card price only
        price_line = eur(total)
    elif o.shipping_eur == 0:
        price_line = f"{eur(total)} inkl. Versand"
    else:
        price_line = f"{eur(total)} inkl. {eur(o.shipping_eur)} Versand"
    if a.target_price:
        price_line += f" · Ziel {eur(a.target_price)}" + (" ✓" if "target" in a.rules else "")
    lines.append(price_line)
    if st.median_7d:
        diff = (total / st.median_7d - 1) * 100
        lines.append(f"{pct(diff)} ggü. 7-Tage-Schnitt ({eur(st.median_7d)})")
    if st.min_ever is not None:
        lines.append(f"Bisheriges Tief: {eur(st.min_ever)}" + (" → neues Tief" if "new_low" in a.rules else ""))
    if a.suspicious:
        lines.append("Ungewöhnlich günstig – evtl. Fehlpreis oder falsches Produkt.")

    return Notification(
        title=f"{marker} {kind} · {a.product_name} · {eur(total)}",
        message="\n".join(lines),
        priority=prio,
        click_url=o.offer_url,
    )


def format_back_in_stock(label: str, o: Offer) -> Notification:
    return Notification(title=f"📦 Wieder lieferbar · {label} · {eur(o.total_eur)}",
                        message=f"{pretty_shop(o.shop)} · {availability(o)}", click_url=o.offer_url)


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
