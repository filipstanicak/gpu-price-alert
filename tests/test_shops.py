from pathlib import Path

import pytest

from config import FilterConfig, PollConfig, ProductConfig
from main import prefer_direct_offers
from models import Offer
from rules import filter_offers
from sources.base import ParserError
from sources.shops import AlternateSource, MindfactorySource, ProshopSource

FIX = Path(__file__).resolve().parent / "fixtures"
POLL = PollConfig(delay_between_requests_seconds=1)


def load(name):
    return (FIX / name).read_text(encoding="utf-8", errors="replace")


class FakeClient:
    def __init__(self, html):
        self.html = html

    def get(self, url):
        class R:
            status_code = 200
            text = self.html
        return R()


@pytest.mark.parametrize("cls,fixture,price,ship,in_stock", [
    (MindfactorySource, "mindfactory_red_devil.html", 1149.00, 8.90, True),
    (AlternateSource, "alternate_red_devil.html", 1085.00, 7.99, False),
    (AlternateSource, "alternate_noctua.html", 2254.00, 0.0, False),   # "Gratis Versand"
    (ProshopSource, "proshop_red_devil.html", 1119.90, 5.99, True),
    (ProshopSource, "proshop_tuf.html", 1579.90, 5.99, False),         # PreOrder
])
def test_parse_shop_pages(cls, fixture, price, ship, in_stock):
    o = cls(POLL).parse(load(fixture), "https://x/")
    assert (o.price_eur, o.shipping_eur, o.in_stock) == (price, ship, in_stock)
    assert o.shop == cls.shop_label and o.source == cls.name


def rd(**kw):
    return ProductConfig(id="rd", name="RD", url="https://geizhals.de/x-a1.html",
                         shop_urls=["https://www.mindfactory.de/p_1.html", "https://www.alternate.de/p/1",
                                    "https://www.proshop.de/p/1"],
                         mpns=["RX9070XT 16G-E/OC"], exclude_keywords=["white"], **kw)


@pytest.mark.parametrize("cls,fixture", [
    (MindfactorySource, "mindfactory_red_devil.html"),
    (AlternateSource, "alternate_red_devil.html"),
    (ProshopSource, "proshop_red_devil.html"),
])
def test_fetch_checks_exact_mpn(cls, fixture):
    src = cls(POLL, client=FakeClient(load(fixture)))
    assert len(src.fetch(rd())) == 1  # only its own domain's URL
    with pytest.raises(ParserError):
        src.fetch(rd().model_copy(update={"mpns": ["RX9070XT 16G-E/OC/WHITE"]}))


def test_blocked_or_changed_page_raises():
    with pytest.raises(ParserError):
        ProshopSource(POLL).parse("<html><body>Access denied</body></html>", "https://x/")


def test_whitelist_accepts_shop_labels():
    f = FilterConfig(shop_whitelist=["Mindfactory", "Alternate", "Proshop"])
    offers = [MindfactorySource(POLL).parse(load("mindfactory_red_devil.html"), "u"),
              ProshopSource(POLL).parse(load("proshop_red_devil.html"), "u")]
    assert len(filter_offers(offers, rd(), f)) == 2


def _o(shop, price, source):
    return Offer(shop=shop, price_eur=price, shipping_eur=0, in_stock=True, delivery_text="", delivery_days_max=0,
                 offer_url="u", source=source)


def test_direct_shop_price_replaces_geizhals_copy():
    offers = [_o("Proshop.de", 1000, "geizhals"), _o("Mindfactory", 1100, "geizhals"), _o("galaxus", 1050, "geizhals"),
              _o("Proshop", 1120, "proshop")]
    kept = prefer_direct_offers(offers)
    assert {(o.shop, o.source) for o in kept} == {("Mindfactory", "geizhals"), ("galaxus", "geizhals"),
                                                  ("Proshop", "proshop")}


def test_card_price_only_when_shipping_ignored():
    o = _o("Mindfactory", 1149, "mindfactory")
    o.shipping_eur = None  # shop page without recognisable shipping
    priced = Offer(shop="Proshop", price_eur=1119.90, shipping_eur=5.99, in_stock=True, delivery_text="",
                   delivery_days_max=0, offer_url="u", source="proshop")
    f = FilterConfig(shop_whitelist=["Mindfactory", "Proshop"], include_shipping=False)
    kept = filter_offers([o, priced], rd(), f)
    assert {(x.shop, x.total_eur) for x in kept} == {("Mindfactory", 1149), ("Proshop", 1119.90)}
    # with shipping counted, the offer with unknown shipping is skipped and Proshop costs 1125.89
    f2 = FilterConfig(shop_whitelist=["Mindfactory", "Proshop"], include_shipping=True)
    assert [(x.shop, x.total_eur) for x in filter_offers([o, priced], rd(), f2)] == [("Proshop", 1125.89)]
