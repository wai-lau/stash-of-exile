"""Search, fetch, and turn listings into a price.

Zero results is information, not an error: nothing at least as good as our
item is currently listed. Those are the items most worth looking at by hand,
so they are flagged rather than dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

from sox.cache import TTL, Cache
from sox.valuation.allowlists import ModEntry
from sox.valuation.query import RELAX_STEPS, build_query, query_hash

SUGGESTED_ASK_FACTOR = 0.9
FETCH_LIMIT = 10  # one fetch call is enough to price the cheap end


@dataclass(frozen=True)
class TradeResult:
    ceiling_ex: float | None
    suggested_ask_ex: float | None
    tag: str
    listings: int
    searches_used: int


def price_by_search(
    item: dict,
    category: str,
    index: dict[str, ModEntry],
    notables: dict[str, str],
    trade,
    cache: Cache,
    rates: dict[str, float],
    status: str = "online",
    min_results: int = 1,
    max_searches: int = 4,
) -> TradeResult:
    searches = 0

    for step in range(min(len(RELAX_STEPS), max_searches)):
        query = build_query(item, category, index, notables, status=status, relax=step)
        key = query_hash(query)

        cached = cache.get("trade_price", key)
        if cached is not None:
            return TradeResult(**cached)

        query_id, hashes = trade.search(query)
        searches += 1
        # One genuine listing already establishes a ceiling. Demanding a
        # thicker market here was rejecting exactly the scarce items whose
        # price is hardest to guess — a Megalomaniac with a specific notable
        # has a handful of listings, not dozens.
        if len(hashes) < min_results:
            continue

        listings = trade.fetch(query_id, hashes[:FETCH_LIMIT])
        prices = [p for p in (l.to_exalted(rates) for l in listings) if p is not None]
        if not prices:
            continue

        cheapest = min(prices)
        result = TradeResult(
            ceiling_ex=round(cheapest, 2),
            suggested_ask_ex=round(cheapest * SUGGESTED_ASK_FACTOR, 2),
            tag="exact" if step == 0 else f"relaxed:{step}",
            listings=len(prices),
            searches_used=searches,
        )
        cache.put("trade_price", key, result.__dict__, ttl=TTL["trade_price"])
        return result

    return TradeResult(None, None, "unpriced:above-market", 0, searches)
