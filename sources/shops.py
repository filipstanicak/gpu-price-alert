"""Direct shop adapters (Mindfactory, Alternate, Proshop).

All three expose schema.org product data (JSON-LD or microdata) on their product pages,
which is much more stable than scraping the visible layout. Only product pages are fetched;
robots.txt of all three allows them (checked 2026-11-19). Shops that block automated
requests (idealo, Notebooksbilliger, Cyberport, Computeruniverse, Galaxus, Amazon) are
deliberately not supported.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from config import PollConfig, ProductConfig
from models import Offer
from sources.base import ParserError, PoliteFetcher, check_variant
from sources.geizhals import parse_euro

IN_STOCK = {"instock", "limitedavailability", "onlineonly", "instoreonly"}


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _avail(v) -> str:
    return str(v or "").rstrip("/").rsplit("/", 1)[-1].lower()


def extract_schema_product(html: str) -> dict:
    """Return {name, mpn, sku, price, availability, shipping} from JSON-LD, falling back to microdata."""
    tree = HTMLParser(html)
    out = {"name": "", "mpn": "", "sku": "", "price": None, "availability": "", "shipping": None}
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.text(), strict=False)
        except ValueError:
            continue
        for node in _walk(data):
            t = node.get("@type")
            if t != "Product" and not (isinstance(t, list) and "Product" in t):
                continue
            offers = node.get("offers")
            offer = offers[0] if isinstance(offers, list) and offers else offers
            if not isinstance(offer, dict):
                continue
            price = offer.get("price", offer.get("lowPrice"))
            rate = ((offer.get("shippingDetails") or {}).get("shippingRate") or {}).get("value")
            out.update(name=node.get("name", ""), mpn=str(node.get("mpn", "")), sku=str(node.get("sku", "")),
                       price=float(price) if price not in (None, "") else None,
                       availability=_avail(offer.get("availability")),
                       shipping=float(rate) if rate not in (None, "") else None)
            return out
    # microdata fallback (Mindfactory)
    p = tree.css_first('[itemprop="price"]')
    if p is not None:
        raw = p.attributes.get("content") or p.text()
        try:
            out["price"] = float(raw.replace(",", ".")) if raw else None
        except ValueError:
            out["price"] = parse_euro("€ " + raw)
    a = tree.css_first('[itemprop="availability"]')
    if a is not None:
        out["availability"] = _avail(a.attributes.get("href") or a.attributes.get("content"))
    sku = tree.css_first('[itemprop="sku"]')
    if sku is not None:
        out["sku"] = sku.attributes.get("content") or sku.text(strip=True)
    h1 = tree.css_first("h1")
    out["name"] = h1.text(strip=True) if h1 else ""
    return out


class ShopSource:
    """One shop = one instance. Subclasses only differ in MPN field and shipping parsing."""

    name = "shop"
    shop_label = "Shop"
    domain = ""
    default_shipping: float | None = None

    def __init__(self, poll: PollConfig, client: httpx.Client | None = None):
        self.http = PoliteFetcher(poll, client, label=self.name)

    def handles(self, url: str) -> bool:
        return urlsplit(url).netloc.endswith(self.domain)

    def mpn(self, data: dict) -> str:
        return data["mpn"]

    def shipping(self, html: str, data: dict) -> float | None:
        return data["shipping"] if data["shipping"] is not None else self.default_shipping

    def parse(self, html: str, url: str) -> Offer:
        data = extract_schema_product(html)
        if data["price"] is None or not data["name"]:
            raise ParserError(f"{self.shop_label}: no schema.org price/name found (layout changed or blocked?)")
        in_stock = data["availability"] in IN_STOCK
        return Offer(
            shop=self.shop_label,
            price_eur=round(data["price"], 2),
            shipping_eur=self.shipping(html, data),
            in_stock=in_stock,
            delivery_text="auf Lager (direkt beim Shop)" if in_stock else f"{data['availability'] or 'unbekannt'} (direkt beim Shop)",
            delivery_days_max=0 if in_stock else None,
            offer_url=url,
            title=data["name"],
            source=self.name,
            status=data["availability"],
        )

    def fetch(self, product: ProductConfig) -> list[Offer]:
        offers = []
        for url in [u for u in product.shop_urls if self.handles(u)]:
            html = self.http.get(url)
            offer = self.parse(html, url)
            check_variant(product, offer.title, self.mpn(extract_schema_product(html)))
            offers.append(offer)
        return offers


class MindfactorySource(ShopSource):
    name, shop_label, domain = "mindfactory", "Mindfactory", "mindfactory.de"
    default_shipping = 8.90  # standard shipping for graphics cards (not shown on the product page)

    def mpn(self, data: dict) -> str:
        return data["sku"]  # Mindfactory puts the manufacturer part number into itemprop="sku"


class AlternateSource(ShopSource):
    name, shop_label, domain = "alternate", "Alternate", "alternate.de"
    default_shipping = 7.99

    def shipping(self, html: str, data: dict) -> float | None:
        if "Gratis Versand" in html:
            return 0.0
        m = re.search(r"Versandkosten\s*ab\s*(?:<[^>]+>\s*)*(€\s*[\d.]+,\d{2})", html)
        return parse_euro(m.group(1)) if m else self.default_shipping


class ProshopSource(ShopSource):
    name, shop_label, domain = "proshop", "Proshop", "proshop.de"
    default_shipping = 5.99


SHOP_SOURCES = [MindfactorySource, AlternateSource, ProshopSource]
