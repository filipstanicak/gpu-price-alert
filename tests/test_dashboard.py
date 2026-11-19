import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import AppConfig, ProductConfig
from dashboard import build_data, group_of, render
from models import Offer
from storage import Storage

TZ = ZoneInfo("Europe/Berlin")


def _offer(price, shop="galaxus"):
    return Offer(shop=shop, price_eur=price, shipping_eur=0, in_stock=True, delivery_text="", delivery_days_max=0,
                 offer_url="https://geizhals.de/redir/abc")


def test_group_of():
    assert group_of("PowerColor Red Devil RX 9070 XT") == "Radeon RX 9070 XT"
    assert group_of("MSI RTX 5070 Ti Vanguard SOC") == "GeForce RTX 5070 Ti"
    assert group_of("Asus RTX 5080 Noctua OC") == "GeForce RTX 5080"


def test_build_and_render_dashboard():
    cfg = AppConfig(products=[
        ProductConfig(id="rd", name="Red Devil RX 9070 XT", short_name="Red Devil</script><b>x", target_price=979,
                      url="https://geizhals.de/rd-a1.html"),
        ProductConfig(id="vg", name="Vanguard RTX 5080", url="https://geizhals.de/vg-a2.html"),
    ])
    st = Storage(":memory:")
    t = datetime(2026, 11, 20, 12, 0, tzinfo=TZ)
    st.add_price_point("rd", _offer(1064), t - timedelta(hours=2))
    st.add_price_point("rd", _offer(970, "notebooksbilliger.de"), t - timedelta(hours=1))
    st.set_state("avail:rd", "1")
    st.set_state("avail:vg", "0")

    data = build_data(cfg, st, t)
    rd, vg = data["cards"]
    assert rd["current"] == 970 and rd["shop"] == "Notebooksbilliger" and rd["min"] == 970
    assert rd["url"] == "https://geizhals.de/rd-a1.html"  # tokenised /redir/ links are not published
    assert len(rd["points"]) == 2 and rd["group"] == "Radeon RX 9070 XT"
    assert vg["current"] is None and vg["available"] is False and vg["points"] == []

    html = render(data)
    payload = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S).group(1)
    assert "</script>" not in payload  # a label cannot break out of the data block
    assert json.loads(payload.replace("<\\/", "</"))["cards"][0]["label"] == "Red Devil</script><b>x"
