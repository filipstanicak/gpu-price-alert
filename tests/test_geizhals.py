from pathlib import Path

import pytest

from config import FilterConfig, PollConfig, ProductConfig
from rules import best_offer, filter_offers
from sources.base import ParserError
from sources.geizhals import (GeizhalsSource, parse_delivery_days, parse_euro, parse_product_page,
                              parse_shipping, text_says_in_stock)

FIX = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_parse_euro():
    assert parse_euro("€ 1063,99") == 1063.99
    assert parse_euro("€ 1.063,99") == 1063.99
    assert parse_euro("€ 150,-") == 150
    assert parse_euro("kein Preis") is None


@pytest.mark.parametrize("text,expected", [
    ("Kreditkarte, PayPal GRATISVERSAND . Lieferung nur innerhalb Deutschlands.", 0.0),
    ("Vorkasse, Kreditkarte, PayPal, Klarna € 8,90 . Expressversand ab € 16,90 möglich.", 8.90),
    ("Vorkasse € 5,99 minus € 6,04 Rabatt. Kreditkarte, PayPal € 5,99 .", 5.99),
    ("Vorkasse, PayPal GRATISVERSAND minus € 5,39 Rabatt bei Vorkasse.", 0.0),
    ("Vorkasse, PayPal, Klarna € 5,95 . Nachnahme € 9,95 .", 5.95),
    ("Kreditkarte/EC-Karte bei Abholung möglich. 0%-Finanzierung ab € 150,- Warenwert", None),
])
def test_parse_shipping(text, expected):
    assert parse_shipping(text) == expected


def test_delivery_days_and_stock():
    assert parse_delivery_days("Lieferzeit 3-7 Werktage") == 7
    assert parse_delivery_days("sofort lieferbar, Lieferzeit 1-3 Werktage") == 3
    assert parse_delivery_days("Lieferzeit 2 Wochen") == 14
    assert parse_delivery_days("unbekannt") is None
    assert text_says_in_stock("Auf Lager, 1-2 Werktage")
    assert not text_says_in_stock("Leipzig: nicht lagernd, voraussichtlich abholbereit")


def test_parse_red_devil_page():
    meta, offers = parse_product_page(load("geizhals_red_devil.html"))
    assert meta["mpn"] == "RX9070XT 16G-E/OC"
    assert "Red Devil" in meta["title"] and "White" not in meta["title"]
    assert len(offers) == 22
    first = offers[0]
    assert (first.shop, first.price_eur, first.shipping_eur, first.in_stock) == ("galaxus", 1063.99, 0.0, True)
    mf = next(o for o in offers if o.shop == "Mindfactory")
    assert mf.total_eur == 1157.90  # 1149,00 + 8,90 Versand
    assert all(o.offer_url.startswith("https://geizhals.de/redir/") for o in offers)


def test_filter_whitelist_stock_shipping():
    _, offers = parse_product_page(load("geizhals_red_devil.html"))
    f = FilterConfig(shop_whitelist=["Mindfactory", "Proshop", "Galaxus", "Cyberport", "Computeruniverse",
                                     "Notebooksbilliger"],
                     exclude_merchant_keywords=["marketplace", "stores"])
    p = ProductConfig(id="rd", name="RD", url="https://geizhals.de/x-a1.html", exclude_keywords=["white"])
    valid = filter_offers(offers, p, f)
    shops = {o.shop for o in valid}
    # Cyberport/computeruniverse: 5-7 days -> out; Cyberport Stores: excluded merchant; x-kom: not whitelisted
    assert shops == {"galaxus", "notebooksbilliger.de", "Proshop.de", "Mindfactory"}
    assert best_offer(valid).total_eur == 1063.99


def test_exclude_keyword_on_offer_title():
    _, offers = parse_product_page(load("geizhals_red_devil.html"))
    p = ProductConfig(id="rd", name="RD", url="https://geizhals.de/x-a1.html", exclude_keywords=["red devil"])
    assert all("red devil" not in o.title.lower() for o in filter_offers(offers, p, FilterConfig()))


def test_page_without_offers_is_valid():
    meta, offers = parse_product_page(load("geizhals_trio_no_offers.html"))
    assert offers == [] and "Gaming Trio OC Plus" in meta["title"]


def test_broken_layout_raises():
    with pytest.raises(ParserError):
        parse_product_page("<html><body><h1>Foo</h1><style>.offerlist{}</style></body></html>")
    with pytest.raises(ParserError):
        parse_product_page("<html><body>Access denied</body></html>")


class FakeClient:
    def __init__(self, html):
        self.html = html

    def get(self, url):
        class R:
            status_code = 200
            text = self.html
        return R()


def _src(html):
    return GeizhalsSource(PollConfig(delay_between_requests_seconds=1), client=FakeClient(html))


def test_mpn_must_match_exactly():
    html = load("geizhals_red_devil.html")
    ok = ProductConfig(id="rd", name="RD", url="https://geizhals.de/x-a1.html", mpns=["RX9070XT 16G-E/OC"])
    assert len(_src(html).fetch(ok)) == 22
    white = ProductConfig(id="rd", name="RD", url="https://geizhals.de/x-a1.html", mpns=["RX9070XT 16G-E/OC/WHITE"])
    with pytest.raises(ParserError):
        _src(html).fetch(white)


def test_wrong_variant_url_detected_by_title():
    h1 = 'variant__header__headline">PowerColor Red Devil Radeon RX 9070 XT,'
    html = load("geizhals_red_devil.html")
    assert h1 in html
    html = html.replace(h1, h1.replace("XT,", "XT Limited Edition,"))
    p = ProductConfig(id="rd", name="RD", url="https://geizhals.de/x-a1.html", exclude_keywords=["limited"])
    with pytest.raises(ParserError):
        _src(html).fetch(p)


def test_429_pauses_site_without_retries(monkeypatch):
    import time as _t
    from sources.base import PoliteFetcher, SourceError
    calls = []

    class C:
        def get(self, url):
            calls.append(url)

            class R:
                status_code = 429
                headers = {"Retry-After": "600"}
                text = ""
            return R()

    monkeypatch.setattr(_t, "sleep", lambda s: None)
    f = PoliteFetcher(PollConfig(delay_between_requests_seconds=1), client=C())
    with pytest.raises(SourceError):
        f.get("https://geizhals.de/a")
    with pytest.raises(SourceError, match="paused"):
        f.get("https://geizhals.de/b")
    assert calls == ["https://geizhals.de/a"]  # one request, no retries, second page skipped
