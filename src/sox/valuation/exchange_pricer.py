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

@dataclass(frozen=True)
class BulkPrice:
    price_ex: float
    offers: int              # how many sellers, which is breadth
    stock: int               # how many units they hold, which is depth
    ask_ex: float | None = None   # what one costs to buy
    bid_ex: float | None = None   # what someone will pay for one
    quoted: str = UNIT            # the currency the book was read in


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


def price_by_exchange(name: str, exchange, divine_ex: float | None = None) -> BulkPrice | None:
    """What one unit of `name` is worth, read from both sides of its book.

    Neither side alone is a price. Measured live, divine asked 420 and bid
    301 while every other source said 358; the midpoint said 360.5. The ask
    is what it costs to buy and runs high, the bid is what someone will pay
    and runs low.

    Most cheap currency has no bid side at all — 1303 sellers of one omen and
    not a single buyer — and there the ask is the only evidence there is.

    The divine book is read EVERY time, not only when the exalted book is
    thin, because the unit an item trades in is a fact about the item: the
    dear ones are quoted in divine and their exalted book is whoever happened
    to list one — or worse, bait. Masterwork Rune's exalted book was 745
    offers and every one on the cheapest page was "1 Exalted for 1", one unit
    each; at 184 visible units it passed a thin-stock gate and priced a
    ~1 divine rune at 1 ex. Depth in a bait book is still bait, so no reading
    of one book alone can be trusted — the deeper of the two answers.

    None is the signal to fall back to the index: uniques, gear and jewels
    have no exchange book, neither do items nobody is offering, and neither
    does a book whose two sides cross.
    """
    item_id = exchange.ids().get(name)
    if item_id is None:
        return None
    if item_id == UNIT:
        return BulkPrice(price_ex=1.0, offers=0, stock=0, ask_ex=1.0, bid_ex=1.0)

    priced = _read_book(exchange, item_id, UNIT, 1.0)
    # Divine against itself is the same meaningless query exalted against
    # itself is, and without a rate there is nothing to convert the book with.
    if item_id == DIVINE or not divine_ex:
        return priced
    deeper = _read_book(exchange, item_id, DIVINE, divine_ex)
    if deeper is None:
        return priced
    if priced is None or deeper.stock > priced.stock:
        return deeper
    return priced


# Only the currencies listings are actually quoted in. Every trade price is
# named in exalted or divine, with chaos a distant third, so overriding those
# is two calls rather than eleven and leaves nothing meaningful on the old
# rate. Anything rarer keeps the index rate, where the amounts are too small
# for the difference to reach a reported price.
RATE_CURRENCIES = ("divine", "chaos")


def exchange_rates(exchange, index_rates: dict[str, float]) -> dict[str, float]:
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
        bulk = price_by_exchange(names[code], exchange, rates.get("divine"))
        if bulk is not None:
            rates[code] = bulk.price_ex
    return rates
