"""Turning an offer book into one number.

Both ends of every book are junk. Divine's cheapest ask is one exalted for
one divine and its dearest is eleven thousand; neither is a price. What
separates them is depth — the trap offers held 59 units against 18,520 — so
the statistic is weighted by stock rather than counting listings.
"""

from __future__ import annotations

from dataclasses import dataclass

from sox.ggg.exchange import Offer

# Low enough to track real supply, high enough to step over a thin trap. The
# 1-exalted divine offers are 0.3% of that book, so a tenth percentile clears
# them and lands on 400 where the market actually is.
QUANTILE = 0.10


def stock_weighted_quantile(offers: list[Offer], quantile: float) -> float | None:
    """The ratio at which cumulative stock first reaches `quantile` of the book.

    Counting listings instead of units would let a hundred one-unit dumps
    outvote a seller holding ten thousand.
    """
    if not offers:
        return None
    ordered = sorted(offers, key=lambda o: o.ratio)
    total = sum(o.stock for o in ordered)
    if total <= 0:
        return ordered[0].ratio
    seen = 0
    for offer in ordered:
        seen += offer.stock
        if seen >= quantile * total:
            return offer.ratio
    return ordered[-1].ratio


# The unit of account. Priced against itself the query is meaningless, and
# whatever it returns is noise.
UNIT = "exalted"

# The other currency a book can be quoted in. Anything dear enough is traded
# against divine and hardly at all against exalted: Khatal's Rejuvenation held
# 8 offers and 9 units in exalted, priced at 10 ex, while the game's own
# exchange quoted it 1:2.67 against divine — 908 ex. The exalted side was not
# the market, it was the stragglers.
DIVINE = "divine"

# Below this much turnover a fills figure is pair noise, not a price: the
# game's exchange traded 281 ex of an omen and the snapshot put it at 36,
# where its online book floors at 2. Masterwork's 38,000 ex of fills is a
# market.
FILL_FLOOR_EX = 5_000.0

# Below this many units an exalted book cannot support a price and the
# divine book is read as well. Above it the exalted answer stands: the
# divine side of every cheap item is a wall of lazy one-divine asks from
# sellers who never expect a fill — the omen's held 14 online offers against
# 12 exalted ones, and any size comparison hands a ~6 ex omen to the wrong
# book at 362. The gate was unsound only while the books were read at "any",
# where bait made the exalted side look deep; online books are not bait-deep,
# and the bait class the gate once missed (Masterwork Rune) is priced by its
# fills before any book is opened.
THIN_STOCK = 20

@dataclass(frozen=True)
class BulkPrice:
    price_ex: float
    offers: int              # how many sellers, which is breadth
    stock: int               # how many units they hold, which is depth
    ask_ex: float | None = None   # what one costs to buy
    bid_ex: float | None = None   # what someone will pay for one
    quoted: str = UNIT            # the currency the book was read in; "fills"
                                  # when the game's own exchange answered
    traded_ex: float = 0.0        # fills only: exalted actually exchanged


def _read_book(exchange, item_id: str, unit: str, unit_ex: float) -> BulkPrice | None:
    """Both sides of one book, converted into exalted.

    `unit_ex` is what one of `unit` costs, so a book quoted in divine comes
    back in the same currency as one quoted in exalted and the two are
    comparable.
    """
    asks = exchange.book(item_id, have=unit)
    ask = stock_weighted_quantile(asks.offers, QUANTILE)
    if ask is None:
        return None

    # The bid book is the same market read the other way round: sellers of the
    # unit who want this item. Its ratio is units-per-unit-of-account, so it
    # inverts into unit-of-account-per-item.
    bids = exchange.book(unit, have=item_id)
    per_unit = stock_weighted_quantile(bids.offers, QUANTILE)
    bid = 1 / per_unit if per_unit else None

    # A book that crosses is not a market. Depth only steps over the bait at
    # the bottom of a book if the page it reads reaches past it, and for
    # Preserved Cranium it did not: all 100 offers on the cheapest page, every
    # visible unit of the 331-deep book, were "1 Exalted Orb for 1 Preserved
    # Cranium". The ask came back 1 against a real 500 ex bid, and the
    # midpoint printed 250 ex for an item the index prices at 3,449.
    #
    # Buy at the ask, sell at the bid, forever: no market survives that, so
    # one of the two sides is bait. The book does not say which, and guessing
    # is how the 250 got printed — so the exchange declines and the index,
    # which is a wholly separate measurement, answers instead.
    if bid is not None and bid > ask:
        return None

    return BulkPrice(
        price_ex=((ask + bid) / 2 if bid else ask) * unit_ex,
        offers=asks.total,
        stock=sum(o.stock for o in asks.offers),
        ask_ex=ask * unit_ex,
        bid_ex=bid * unit_ex if bid is not None else None,
        quoted=unit,
    )


def price_by_exchange(
    name: str,
    exchange,
    divine_ex: float | None = None,
    fills: dict[str, tuple[float, float]] | None = None,
) -> BulkPrice | None:
    """What one unit of `name` is worth — by its fills, then by its book.

    The game's own Currency Exchange answers first when it traded enough of
    the item: a fill cannot be faked, where a listing costs nothing to type.
    Masterwork Rune's trade-site book was 748 one-unit "1 Exalted" bait
    listings while 38,000 ex of the rune actually changed hands near 260 —
    and that IS the instant-buyout market, since every fill there is an
    instant trade. Below FILL_FLOOR_EX the figure is pair noise and the
    books answer as before.

    Neither side alone is a price. Measured live, divine asked 420 and bid
    301 while every other source said 358; the midpoint said 360.5. The ask
    is what it costs to buy and runs high, the bid is what someone will pay
    and runs low.

    Most cheap currency has no bid side at all — sellers by the dozen and
    not a single buyer — and there the ask is the only evidence there is.

    A book too thin to price is read AGAIN against divine, because the unit
    an item trades in is a fact about the item: the dear ones are quoted in
    divine and their exalted book is whoever happened to list one — Khatal's
    Rejuvenation held 9 exalted-book units at 10 ex while the divine side
    said 908. The gate is only sound because of what sits in front of it:
    fills catch the items whose exalted book is deep BAIT (Masterwork Rune,
    745 offers of "1 Exalted for 1"), and reading from online sellers keeps
    the ghosts out of the depth the gate measures.

    None is the signal to fall back to the index: uniques, gear and jewels
    have no exchange book, neither do items nobody is offering, and neither
    does a book whose two sides cross.
    """
    item_id = exchange.ids().get(name)
    if item_id is None:
        return None
    if item_id == UNIT:
        return BulkPrice(price_ex=1.0, offers=0, stock=0, ask_ex=1.0, bid_ex=1.0)

    if fills:
        price_ex, traded = fills.get(name, (0.0, 0.0))
        if traded >= FILL_FLOOR_EX:
            return BulkPrice(price_ex=price_ex, offers=0, stock=0,
                             quoted="fills", traded_ex=traded)

    priced = _read_book(exchange, item_id, UNIT, 1.0)
    if priced is not None and priced.stock >= THIN_STOCK:
        return priced
    # Divine against itself is the same meaningless query exalted against
    # itself is, and without a rate there is nothing to convert the book with.
    if item_id == DIVINE or not divine_ex:
        return priced
    deeper = _read_book(exchange, item_id, DIVINE, divine_ex)
    if deeper is None:
        return priced
    # Breadth, not page stock: the page's stock is what a wall of lazy asks
    # inflates most cheaply. How many sellers quote the item in a currency is
    # what says which currency it trades in.
    if priced is None or deeper.offers > priced.offers:
        return deeper
    return priced


# Only the currencies listings are actually quoted in. Every trade price is
# named in exalted or divine, with chaos a distant third, so overriding those
# is two calls rather than eleven and leaves nothing meaningful on the old
# rate. Anything rarer keeps the index rate, where the amounts are too small
# for the difference to reach a reported price.
RATE_CURRENCIES = ("divine", "chaos")


def exchange_rates(
    exchange,
    index_rates: dict[str, float],
    fills: dict[str, tuple[float, float]] | None = None,
) -> dict[str, float]:
    """The index rate table with the quoted currencies repriced in bulk.

    Prices and the rate that renders them must come from ONE book. Reading
    the price of a Divine Orb off the exchange while converting it with the
    index's rate printed it as "1.12 div" — the same orb measured twice.
    """
    rates = dict(index_rates)
    names = {"divine": "Divine Orb", "chaos": "Chaos Orb"}
    for code in RATE_CURRENCIES:
        # Divine is priced first and against exalted, so by the time anything
        # else is read there is a rate to quote a divine book in.
        bulk = price_by_exchange(names[code], exchange, rates.get("divine"),
                                 fills=fills)
        if bulk is not None:
            rates[code] = bulk.price_ex
    return rates
