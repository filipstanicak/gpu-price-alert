from __future__ import annotations

import httpx

from config import TelegramConfig


class TelegramNotifier:
    def __init__(self, cfg: TelegramConfig):
        self.cfg = cfg

    def send(self, n) -> None:
        text = f"{n.title}\n\n{n.message}" + (f"\n\n👉 {n.click_url}" if n.click_url else "")
        r = httpx.post(
            f"https://api.telegram.org/bot{self.cfg.bot_token}/sendMessage",
            json={"chat_id": self.cfg.chat_id, "text": text[:4000], "disable_web_page_preview": True,
                  "disable_notification": n.priority == "low"},
            timeout=15,
        )
        r.raise_for_status()
