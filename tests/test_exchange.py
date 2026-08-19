"""Bulk exchange pricing: what a currency actually costs to buy.

The index priced Omen of the Sovereign at 26.5 ex off 31 listings while the
exchange held 1303 offers at 1:1 — 26x high, and nothing cross-checked it
because currency never reaches the trade search. The exchange is the deeper
book, so it prices anything it carries.

Both ends of that book are junk. Divine's cheapest ask is one exalted for one
divine, a trap; its dearest is 11,000. Only depth saves you: those 1-exalted
offers hold 59 units of 18,520, so a stock-weighted low quantile steps over
them and lands on the supported price.
"""

import httpx
import pytest

from sox.cache import Cache
from sox.ggg.exchange import ExchangeClient, Offer
from sox.ggg.governor import RateGovernor
from sox.ggg.session import GGGSession
from sox.valuation.exchange_pricer import QUANTILE, stock_weighted_quantile

STATIC = {"result": [
    {"id": "Currency", "label": "Currency", "entries": [
        {"id": "exalted", "text": "Exalted Orb"},
        {"id": "divine", "text": "Divine Orb"},
        {"id": "wisdom", "text": "Scroll of Wisdom"},
    ]},
    {"id": "Ritual", "label": "Ritual", "entries": [
        {"id": "omen-of-the-sovereign", "text": "Omen of the Sovereign"},
    ]},
    {"id": "Abyss", "label": "Abyss", "entries": [
        {"id": "preserved-cranium", "text": "Preserved Cranium"},
    ]},
]}


def offers(pairs):
    """pairs of (exalted paid, units received, stock)."""
    return {"total": len(pairs), "result": {
        str(n): {"listing": {"offers": [{
            "exchange": {"currency": "exalted", "amount": have},
            "item": {"currency": "x", "amount": want, "stock": stock},
        }]}}
        for n, (have, want, stock) in enumerate(pairs)
    }}


def build(routes, tmp_path):
    def handler(request):
        for fragment, payload in routes.items():
            if fragment in str(request.url):
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={})

    session = GGGSession(
        RateGovernor(clock=lambda: 0.0, sleeper=lambda s: None),
        httpx.Client(transport=httpx.MockTransport(handler)),
        user_agent="sox-test",
    )
    return ExchangeClient(session, Cache(tmp_path / "c.sqlite"), "Runes of Aldur")


def test_a_thin_junk_low_does_not_set_the_price():
    """The 1-exalted divine offers are 59 units of 18,520 — 0.3% of the book."""
    book = [Offer(1.0, 59), Offer(400.0, 5000), Offer(450.0, 13461)]
    assert stock_weighted_quantile(book, QUANTILE) == 400.0


def test_the_cheapest_offer_alone_would_price_a_divine_at_one_exalted():
    book = [Offer(1.0, 59), Offer(400.0, 5000), Offer(450.0, 13461)]
    assert min(o.ratio for o in book) == 1.0, "which is why min() is not used"


def test_an_empty_book_has_no_price():
    assert stock_weighted_quantile([], QUANTILE) is None


def test_a_book_of_one_offer_prices_at_that_offer():
    assert stock_weighted_quantile([Offer(7.5, 3)], QUANTILE) == 7.5


def test_a_bundle_offer_prices_below_one_exalted(tmp_path):
    """1 exalted for 40 wisdom scrolls is 0.025 each — the book expresses it."""
    client = build({"/data/static": STATIC,
                    "/exchange/": offers([(1, 40, 200), (1, 20, 100)])}, tmp_path)
    assert client.book("wisdom").offers[0].ratio == pytest.approx(0.025)


def test_the_ratio_is_exalted_paid_per_unit_received(tmp_path):
    client = build({"/data/static": STATIC,
                    "/exchange/": offers([(10, 1, 5)])}, tmp_path)
    assert client.book("omen-of-the-sovereign").offers == [Offer(10.0, 5)]


def test_ids_are_read_from_static_across_every_group(tmp_path):
    client = build({"/data/static": STATIC}, tmp_path)
    ids = client.ids()
    assert ids["Omen of the Sovereign"] == "omen-of-the-sovereign"
    assert ids["Divine Orb"] == "divine"


def test_an_item_the_exchange_does_not_carry_has_no_id(tmp_path):
    client = build({"/data/static": STATIC}, tmp_path)
    assert client.ids().get("Mageblood") is None


def test_the_book_is_read_with_status_any(tmp_path):
    """`online` returned 5 offers of 1303: PoE2 trade is asynchronous."""
    seen = {}

    def handler(request):
        if "/data/static" in str(request.url):
            return httpx.Response(200, json=STATIC)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=offers([(1, 1, 1)]))

    session = GGGSession(
        RateGovernor(clock=lambda: 0.0, sleeper=lambda s: None),
        httpx.Client(transport=httpx.MockTransport(handler)),
        user_agent="sox-test",
    )
    ExchangeClient(session, Cache(tmp_path / "c.sqlite"), "Runes of Aldur").book("divine")
    assert '"any"' in seen["body"]


def two_sided(tmp_path, ask_pairs, bid_pairs):
    """A handler that answers the two sides of one book differently.

    ask: have exalted, want the item — what it costs to buy.
    bid: have the item, want exalted — what someone will pay for it.
    """
    import json

    def handler(request):
        if "/data/static" in str(request.url):
            return httpx.Response(200, json=STATIC)
        query = json.loads(request.content.decode())["query"]
        pairs = ask_pairs if query["have"] == ["exalted"] else bid_pairs
        return httpx.Response(200, json=offers(pairs))

    session = GGGSession(
        RateGovernor(clock=lambda: 0.0, sleeper=lambda s: None),
        httpx.Client(transport=httpx.MockTransport(handler)),
        user_agent="sox-test",
    )
    return ExchangeClient(session, Cache(tmp_path / "c.sqlite"), "Runes of Aldur")


def test_the_price_is_the_midpoint_of_the_two_sides(tmp_path):
    """Divine measured live: ask 420, bid 301, mid 360.5 against a known 358.

    The ask alone is what it costs to buy and runs high; the bid alone is what
    someone will pay and runs low. Only the midpoint reproduced the rate every
    other source agreed on.
    """
    from sox.valuation.exchange_pricer import price_by_exchange

    # ask: 420 exalted buys one divine. bid: 1 divine buys 301 exalted.
    client = two_sided(tmp_path, ask_pairs=[(420, 1, 1000)],
                       bid_pairs=[(1, 301, 1000)])
    priced = price_by_exchange("Divine Orb", client)
    assert priced.price_ex == pytest.approx(360.5)
    assert (priced.ask_ex, priced.bid_ex) == (pytest.approx(420), pytest.approx(301))


def test_with_no_bids_the_ask_stands_alone(tmp_path):
    """1303 sellers and not one buyer is the shape of most cheap currency."""
    from sox.valuation.exchange_pricer import price_by_exchange

    client = two_sided(tmp_path, ask_pairs=[(1, 1, 276)], bid_pairs=[])
    priced = price_by_exchange("Omen of the Sovereign", client)
    assert priced.price_ex == 1.0
    assert priced.bid_ex is None


def test_exalted_is_one_exalted_without_asking_anyone(tmp_path):
    """The unit of account cannot be priced against itself."""
    from sox.valuation.exchange_pricer import price_by_exchange

    client = two_sided(tmp_path, ask_pairs=[(7, 1, 5)], bid_pairs=[(1, 7, 5)])
    assert price_by_exchange("Exalted Orb", client).price_ex == 1.0


def test_a_crossed_book_is_not_a_price(tmp_path):
    """Preserved Cranium: 100 bait asks at 1 ex under a 500 ex bid.

    Every offer on the cheapest page was "1 Exalted Orb for 1 Preserved
    Cranium" — 331 offers deep, all 148 visible units — so depth had nothing
    to step over TO and the ask came back 1. Against a real 500 ex bid the
    midpoint printed 250 ex for an item the index prices at 3,449.

    Bid above ask is an arbitrage that cannot exist: buy at 1, sell at 500,
    forever. One side is bait and the book does not say which, so there is no
    price here to report and the index answers instead.
    """
    from sox.valuation.exchange_pricer import price_by_exchange

    client = two_sided(tmp_path, ask_pairs=[(1, 1, 148)],
                       bid_pairs=[(1, 500, 6644)])
    assert price_by_exchange("Preserved Cranium", client) is None


def test_a_bid_under_the_ask_is_an_ordinary_spread(tmp_path):
    """Divine sat at ask 420, bid 301, and chaos at 40 against 25.

    A healthy book is one where buying costs more than selling. Only the
    crossed case is refused, so the ordinary spread still prices.
    """
    from sox.valuation.exchange_pricer import price_by_exchange

    client = two_sided(tmp_path, ask_pairs=[(40, 1, 6654)],
                       bid_pairs=[(1, 25, 57980)])
    assert price_by_exchange("Divine Orb", client).price_ex == pytest.approx(32.5)
