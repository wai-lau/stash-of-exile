"""Bulk exchange pricing: what a currency actually costs to buy.

Currency never reaches the trade search, so nothing cross-checks the index
except this book — chaos was indexed at 17.6 ex against a book that says
32.5. The book has its own liars: the omen's celebrated "1,303 offers at
1 ex" turned out to be a wall of offline ghosts (161 sellers online, floored
at 2 ex), which is why the book is read from sellers who are present and the
game's own fills outrank it entirely.

Both ends of any book are junk. Divine's cheapest ask is one exalted for one
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
    {"id": "Runes", "label": "Runes", "entries": [
        {"id": "masterwork-rune", "text": "Masterwork Rune"},
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


def test_the_book_is_read_from_sellers_who_are_present(tmp_path):
    """Measured across all three statuses. "any" is where bait lives: the
    omen showed 8,417 listings at "any" — a wall of offline 1-ex ghosts —
    and 161 online, floored at 2 ex. "securable" empties the core books
    outright (the divine ask book held zero offers at instant buyout). At
    "onlineleague" every measured book was sane: an offer from a seller who
    is present is one you can actually take."""
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
    assert '"onlineleague"' in seen["body"]


def test_the_book_cache_is_keyed_on_the_status_it_was_read_at(tmp_path):
    """A book cached at "any" must not answer for another status — the "any"
    masterwork book was 748 offers of bait and the online one 9 real ones."""
    from sox.ggg import exchange as exchange_module

    client = build({"/data/static": STATIC,
                    "/exchange/": offers([(1, 1, 1)])}, tmp_path)
    client.book("divine")
    cached_keys = [row[0] for row in client._cache._conn.execute(
        "SELECT key FROM entries WHERE tbl = 'exchange_book'")]
    assert cached_keys, "expected the book to be cached"
    assert all(exchange_module.STATUS in key for key in cached_keys)


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


def four_sided(tmp_path, books):
    """A handler answering each (have, want) pair from its own book.

    `books` maps (have, want) -> pairs of (paid, received, stock). Anything
    not listed is an empty book.
    """
    import json

    def handler(request):
        if "/data/static" in str(request.url):
            return httpx.Response(200, json=STATIC)
        query = json.loads(request.content.decode())["query"]
        key = (query["have"][0], query["want"][0])
        return httpx.Response(200, json=offers(books.get(key, [])))

    session = GGGSession(
        RateGovernor(clock=lambda: 0.0, sleeper=lambda s: None),
        httpx.Client(transport=httpx.MockTransport(handler)),
        user_agent="sox-test",
    )
    return ExchangeClient(session, Cache(tmp_path / "c.sqlite"), "Runes of Aldur")


def test_a_thin_exalted_book_is_read_against_divine_instead(tmp_path):
    """Khatal's Rejuvenation: 8 offers, 9 units, priced at 10 ex — while the
    game's own currency exchange quoted it at 1:2.67 against divine, which at
    340 ex a divine is 908. Nobody trades this in exalted; the exalted book is
    a handful of stragglers, and the market is on the divine side.
    """
    from sox.valuation.exchange_pricer import price_by_exchange

    client = four_sided(tmp_path, {
        ("exalted", "omen-of-the-sovereign"): [(10, 1, 9)],
        ("divine", "omen-of-the-sovereign"): [(2.67, 1, 170), (2.67, 1, 170)],
    })
    priced = price_by_exchange("Omen of the Sovereign", client, divine_ex=340.0)
    assert priced.price_ex == pytest.approx(2.67 * 340)
    assert (priced.stock, priced.quoted) == (340, "divine")


def test_a_deep_exalted_book_still_answers(tmp_path):
    """Reading the divine book is not switching to it: when the exalted side
    is the real market, it is also the deeper one, and it keeps the answer."""
    from sox.valuation.exchange_pricer import price_by_exchange

    client = four_sided(tmp_path, {
        ("exalted", "omen-of-the-sovereign"): [(10, 1, 6654)],
        ("divine", "omen-of-the-sovereign"): [(2.67, 1, 340)],
    })
    priced = price_by_exchange("Omen of the Sovereign", client, divine_ex=340.0)
    assert priced.price_ex == 10.0
    assert priced.quoted == "exalted"


def test_a_deep_online_book_stands_without_a_divine_read(tmp_path):
    """The divine side of every cheap item is a wall of lazy one-divine asks
    — the omen's held 14 online offers against 12 real exalted ones, and any
    size comparison hands a ~6 ex omen to the wrong book at 362. A deep
    exalted book supports its own price, so the divine side is not even
    asked. (The bait class this gate once missed — Masterwork Rune's 745
    "1 Exalted for 1" ghosts — is priced by its fills before any book is
    opened, and the ghosts themselves are gone at online status.)"""
    from sox.valuation.exchange_pricer import price_by_exchange

    client = four_sided(tmp_path, {
        ("exalted", "omen-of-the-sovereign"): [(6, 1, 20), (6, 1, 14)],
        ("divine", "omen-of-the-sovereign"): [(1, 1, 200), (1, 1, 200), (1, 1, 200)],
    })
    priced = price_by_exchange("Omen of the Sovereign", client, divine_ex=340.0)
    assert (priced.price_ex, priced.quoted) == (6.0, "exalted")


def test_the_deeper_of_the_two_books_wins(tmp_path):
    """A thin exalted book is a reason to look, not a reason to switch: the
    divine side can be thinner still."""
    from sox.valuation.exchange_pricer import price_by_exchange

    client = four_sided(tmp_path, {
        ("exalted", "omen-of-the-sovereign"): [(10, 1, 5), (10, 1, 4)],
        ("divine", "omen-of-the-sovereign"): [(2.67, 1, 2)],
    })
    priced = price_by_exchange("Omen of the Sovereign", client, divine_ex=340.0)
    assert (priced.price_ex, priced.quoted) == (10.0, "exalted")


def test_a_broad_book_beats_a_fat_paged_one(tmp_path):
    """The omen, live: an exalted book 1,304 offers wide whose cheapest page
    held 274 units, against a divine book 1,123 wide whose page held 425
    units of 1-divine asks. Comparing page stock handed the omen to the
    divine book at 362 ex. Breadth — how many sellers quote the item in a
    currency — is what says which currency it trades in, and a page's stock
    is exactly what bait inflates most cheaply."""
    from sox.valuation.exchange_pricer import price_by_exchange

    client = four_sided(tmp_path, {
        ("exalted", "omen-of-the-sovereign"): [(6, 1, 10), (6, 1, 12), (7, 1, 12)],
        ("divine", "omen-of-the-sovereign"): [(1, 1, 425)],
    })
    priced = price_by_exchange("Omen of the Sovereign", client, divine_ex=340.0)
    assert (priced.price_ex, priced.quoted) == (6.0, "exalted")


def test_fills_outrank_every_book(tmp_path):
    """The game's own exchange is the instant market and its fills cannot be
    faked: Masterwork Rune's trade-site book was 748 one-unit bait listings
    at 1 ex while 38,000 ex of it actually changed hands near 260."""
    from sox.valuation.exchange_pricer import price_by_exchange

    client = four_sided(tmp_path, {
        ("exalted", "masterwork-rune"): [(1, 1, 184)],
    })
    priced = price_by_exchange("Masterwork Rune", client, divine_ex=340.0,
                               fills={"Masterwork Rune": (260.0, 38_000.0)})
    assert priced.price_ex == pytest.approx(260.0)
    assert priced.quoted == "fills"
    assert priced.traded_ex == pytest.approx(38_000.0)


def test_thin_fills_fall_through_to_the_book(tmp_path):
    """281 ex of an omen traded against a book 1,303 offers deep at 1 ex:
    the snapshot's 36 ex figure is pair noise, not a price."""
    from sox.valuation.exchange_pricer import price_by_exchange

    client = four_sided(tmp_path, {
        ("exalted", "omen-of-the-sovereign"): [(1, 1, 6654)],
    })
    priced = price_by_exchange("Omen of the Sovereign", client, divine_ex=340.0,
                               fills={"Omen of the Sovereign": (36.0, 281.0)})
    assert priced.price_ex == pytest.approx(1.0)
    assert priced.quoted == "exalted"


def test_divine_is_never_priced_against_itself(tmp_path):
    """Its own book is 1:1 and says nothing, exactly as exalted's does."""
    from sox.valuation.exchange_pricer import price_by_exchange

    client = four_sided(tmp_path, {
        ("exalted", "divine"): [(340, 1, 9)],
        ("divine", "divine"): [(1, 1, 90000)],
    })
    priced = price_by_exchange("Divine Orb", client, divine_ex=340.0)
    assert (priced.price_ex, priced.quoted) == (340.0, "exalted")
