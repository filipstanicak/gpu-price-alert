"""Geizhals.de product page adapter.

Only fetches product pages (``...-aNNNNNNN.html``), which robots.txt allows.
Search (``/?fs=``) and ``/redir/`` are disallowed and never requested.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

from config import PollConfig, ProductConfig
from models import Offer
from sources.base import ParserError, PoliteFetcher, check_variant

log = logging.getLogger(__name__)

_AMOUNT = re.compile(r"€\s*(\d+(?:\.\d{3})*)(?:,(\d{2}|-))?")
_NOT_SHIPPING_CTX = ("minus", " ab", "nachnahme", "express", "finanzierung", "abholung")
_IN_STOCK = re.compile(r"(auf lager|lagernd|sofort lieferbar|sofort versandfertig|ab lager)", re.I)
_RANGE_DAYS = re.compile(r"(\d+)\s*(?:-|–|bis)\s*(\d+)\s*(werktag|tag|woche)", re.I)
# Exact sentence of the empty offer list. NOT "keine Angebote": that also appears for other variants in the dropdown.
_NO_OFFERS = re.compile(r"keine\s+Anbieter\s+f(?:ü|&uuml;)r\s+dieses\s+Produkt", re.I)
_SINGLE_DAYS = re.compile(r"(\d+)\s*(werktag|tag|woche)", re.I)


def _text(node: Node | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.text(separator=" ")).strip()


def parse_euro(s: str) -> float | None:
    m = _AMOUNT.search(s)
    if not m:
        return None
    euros = int(m.group(1).replace(".", ""))
    cents = m.group(2)
    return euros + (int(cents) / 100 if cents and cents != "-" else 0)


def parse_shipping(text: str) -> float | None:
    """Extract standard shipping cost from Geizhals' payment/shipping cell.

    Returns 0.0 for free shipping, None if nothing could be determined.
    """
    gratis = text.upper().find("GRATISVERSAND")
    for m in _AMOUNT.finditer(text):
        before = text[max(0, m.start() - 25):m.start()].lower()
        after = text[m.end():m.end() + 15].lower()
        if any(k in before for k in _NOT_SHIPPING_CTX) or "rabatt" in after or "warenwert" in after:
            continue
        if gratis != -1 and gratis < m.start():
            return 0.0
        return parse_euro(m.group(0))
    return 0.0 if gratis != -1 else None


def parse_delivery_days(text: str) -> int | None:
    m = _RANGE_DAYS.search(text)
    if m:
        n, unit = int(m.group(2)), m.group(3).lower()
    else:
        m = _SINGLE_DAYS.search(text)
        if not m:
            return None
        n, unit = int(m.group(1)), m.group(2).lower()
    return n * 7 if unit.startswith("woche") else n


def text_says_in_stock(delivery: str) -> bool:
    m = _IN_STOCK.search(delivery)
    return bool(m) and not delivery[max(0, m.start() - 8):m.start()].lower().rstrip().endswith("nicht")


def parse_product_page(html: str, base_url: str = "") -> tuple[dict, list[Offer]]:
    tree = HTMLParser(html)
    title = _text(tree.css_first("h1"))
    mpn = _text(tree.css_first(".variant__header__mpn-showroom"))
    meta = {"title": title, "mpn": mpn}

    nodes = [n for n in tree.css("div.offer") if (n.attributes.get("id") or "").startswith("offer-index-")]
    if not title:
        raise ParserError("no product title found (layout changed or blocked page?)")
    if not nodes:
        if _NO_OFFERS.search(html):
            return meta, []  # valid page, product currently not offered anywhere
        raise ParserError("no offer list found on page (layout changed?)")

    offers: list[Offer] = []
    for n in nodes:
        cls = n.attributes.get("class") or ""
        status = next((s.split("--")[1] for s in cls.split() if s.startswith("offer--")
                       and s.split("--")[1] in ("available", "shortly", "unavailable")), "")
        price = parse_euro(_text(n.css_first(".gh_price")))
        if price is None:
            continue
        merchant_a = n.css_first("a.gh_offerlist__offerurl") or n.css_first("a.offer_bt")
        shop = (merchant_a.attributes.get("data-merchant-name") if merchant_a else None) \
            or _text(n.css_first(".merchant__logo-caption"))
        link = n.css_first("a.offer_bt")
        url = (link.attributes.get("href") if link else None) or base_url
        delivery = _text(n.css_first(".offer__delivery-time"))
        payment = _text(n.css_first(".offer__delivery-payment"))
        details = n.css_first(".offer__details div")
        in_stock = status == "available" or text_says_in_stock(delivery)
        offers.append(Offer(
            shop=shop.strip(),
            price_eur=price,
            shipping_eur=parse_shipping(payment),
            in_stock=in_stock,
            delivery_text=delivery,
            delivery_days_max=0 if in_stock and status == "available" else parse_delivery_days(delivery),
            offer_url=url,
            title=_text(details),
            source="geizhals",
            status=status,
        ))
    if nodes and not offers:
        raise ParserError("offer rows found but no prices parsed (layout changed?)")
    return meta, offers


def _with_params(url: str, **params) -> str:
    p = urlsplit(url)
    q = dict(parse_qsl(p.query))
    q.update({k: str(v) for k, v in params.items()})
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


class GeizhalsSource:
    name = "geizhals"

    def __init__(self, poll: PollConfig, client: httpx.Client | None = None):
        self.poll = poll
        self.http = PoliteFetcher(poll, client, label="geizhals")

    def handles(self, url: str) -> bool:
        return "geizhals." in urlsplit(url).netloc

    def fetch(self, product: ProductConfig) -> list[Offer]:
        offers: list[Offer] = []
        for url in [u for u in product.urls if self.handles(u)]:
            html = self.http.get(_with_params(url, hloc="de"))
            meta, page_offers = parse_product_page(html, base_url=url)
            check_variant(product, meta["title"], meta["mpn"])
            offers.extend(page_offers)
            for page in range(2, self.poll.max_pages + 1):
                if f"page={page}" not in html:
                    break
                html = self.http.get(_with_params(url, hloc="de", page=page))
                offers.extend(parse_product_page(html, base_url=url)[1])
        return offers
