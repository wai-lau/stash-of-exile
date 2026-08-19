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


@dataclass(frozen=True)
class BulkPrice:
    price_ex: float
    offers: int              # how many sellers, which is breadth
    stock: int               # how many units they hold, which is depth
    ask_ex: float | None = None   # what one costs to buy
    bid_ex: float | None = None   # what someone will pay for one


def price_by_exchange(name: str, exchange) -> BulkPrice | None:
    """What one unit of `name` is worth, read from both sides of its book.

    Neither side alone is a price. Measured live, divine asked 420 and bid
    301 while every other source said 358; the midpoint said 360.5. The ask
    is what it costs to buy and runs high, the bid is what someone will pay
    and runs low.

    Most cheap currency has no bid side at all — 1303 sellers of one omen and
    not a single buyer — and there the ask is the only evidence there is.

    None is the signal to fall back to the index: uniques, gear and jewels
    have no exchange book, and neither do items nobody is offering.
    """
    item_id = exchange.ids().get(name)
    if item_id is None:
        return None
    if item_id == UNIT:
        return BulkPrice(price_ex=1.0, offers=0, stock=0, ask_ex=1.0, bid_ex=1.0)

    asks = exchange.book(item_id)
    ask = stock_weighted_quantile(asks.offers, QUANTILE)
    if ask is None:
        return None

    # The bid book is the same market read the other way round: sellers of
    # exalted who want this item. Its ratio is units-per-exalted, so it
    # inverts into exalted-per-unit.
    bids = exchange.book(UNIT, have=item_id)
    per_exalted = stock_weighted_quantile(bids.offers, QUANTILE)
    bid = 1 / per_exalted if per_exalted else None

    return BulkPrice(
        price_ex=(ask + bid) / 2 if bid else ask,
        offers=asks.total,
        stock=sum(o.stock for o in asks.offers),
        ask_ex=ask,
        bid_ex=bid,
    )


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
        bulk = price_by_exchange(names[code], exchange)
        if bulk is not None:
            rates[code] = bulk.price_ex
    return rates
