from __future__ import annotations

from typing import Protocol

from config import ProductConfig
from models import Offer


class SourceError(Exception):
    """Network/HTTP problem (temporary)."""


class ParserError(Exception):
    """Page fetched but structure not as expected (layout change, wrong product, ...)."""


class Source(Protocol):
    name: str

    def handles(self, url: str) -> bool: ...

    def fetch(self, product: ProductConfig) -> list[Offer]: ...
