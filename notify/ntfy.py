from __future__ import annotations

import httpx

from config import NtfyConfig

_PRIO = {"high": 4, "default": 3, "low": 2}


class NtfyNotifier:
    def __init__(self, cfg: NtfyConfig):
        self.cfg = cfg

    def send(self, n) -> None:
        # JSON publishing avoids HTTP-header encoding problems with emojis/umlauts in the title.
        payload = {
            "topic": self.cfg.topic,
            "title": n.title,
            "message": n.message,
            "priority": _PRIO.get(n.priority, 3),
            "tags": n.tags,
        }
        if n.click_url:
            payload["click"] = n.click_url
            payload["actions"] = [{"action": "view", "label": "Zum Angebot", "url": n.click_url}]
        headers = {"Authorization": f"Bearer {self.cfg.token}"} if self.cfg.token else {}
        r = httpx.post(self.cfg.server.rstrip("/"), json=payload, headers=headers, timeout=15)
        r.raise_for_status()
