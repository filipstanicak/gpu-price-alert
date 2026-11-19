from __future__ import annotations

import logging
import random
import re
import time
from typing import Protocol

import httpx

from config import PollConfig, ProductConfig
from models import Offer

log = logging.getLogger(__name__)


class SourceError(Exception):
    """Network/HTTP problem (temporary)."""


class ParserError(Exception):
    """Page fetched but structure not as expected (layout change, wrong product, ...)."""


class Source(Protocol):
    name: str

    def handles(self, url: str) -> bool: ...

    def fetch(self, product: ProductConfig) -> list[Offer]: ...


class PoliteFetcher:
    """HTTP GET with a minimum gap between requests, timeouts and retry with backoff."""

    def __init__(self, poll: PollConfig, client: httpx.Client | None = None, label: str = "http"):
        self.poll = poll
        self.label = label
        self.client = client or httpx.Client(
            headers={"User-Agent": poll.user_agent, "Accept-Language": "de-DE,de;q=0.9"},
            timeout=httpx.Timeout(25.0),
            follow_redirects=True,
        )
        self._last_request = 0.0
        self._paused_until = 0.0  # set after HTTP 429: no more requests to this site until then

    def get(self, url: str) -> str:
        if time.monotonic() < self._paused_until:
            raise SourceError(f"{self.label}: rate limited (HTTP 429) – paused, skipping {url}")
        last_exc: Exception | None = None
        for attempt, delay in enumerate([0, 10, 30], 1):
            gap = self.poll.delay_between_requests_seconds * random.uniform(1.0, 1.5)
            wait = max(delay, gap - (time.monotonic() - self._last_request))
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                r = self.client.get(url)
                if r.status_code == 200:
                    return r.text
                last_exc = SourceError(f"HTTP {r.status_code} for {url}")
                if r.status_code == 429:  # back off for the rest of this run, never hammer
                    retry_after = r.headers.get("Retry-After", "") if hasattr(r, "headers") else ""
                    self._paused_until = time.monotonic() + (int(retry_after) if retry_after.isdigit() else 900)
                    break
                if r.status_code in (403, 404, 410):
                    break  # retrying won't help
            except httpx.HTTPError as e:
                last_exc = SourceError(f"{type(e).__name__}: {e}")
            log.warning("%s attempt %d failed: %s", self.label, attempt, last_exc)
        raise last_exc or SourceError(url)


def norm_mpn(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def check_variant(product: ProductConfig, title: str, mpn: str) -> None:
    """Raise ParserError if the page is not exactly the configured variant."""
    bad = next((k for k in product.exclude_keywords if k.lower() in title.lower()), None)
    if bad:
        raise ParserError(f"page title '{title}' contains excluded keyword '{bad}' – wrong URL?")
    # exact match: "RX9070XT 16G-E/OC/WHITE" must NOT pass for "RX9070XT 16G-E/OC"
    if product.mpns and mpn and norm_mpn(mpn) not in {norm_mpn(m) for m in product.mpns}:
        raise ParserError(f"MPN mismatch: page has '{mpn}', expected one of {product.mpns}")
