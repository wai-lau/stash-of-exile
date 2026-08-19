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

# A handful of listings is not a market. With three results the cheapest can
# easily be a mispriced outlier or a far better item, which is how a
# quarterstaff worth ~3ex once reported 320ex. Keep relaxing until the sample
# is big enough to mean something, and label the confidence when it is not.
MIN_SAMPLE = 8
THIN_SAMPLE = 3


@dataclass(frozen=True)
class TradeResult:
    ceiling_ex: float | None      # cheapest comparable listing
    median_ex: float | None       # middle of the sample; outlier-resistant
    suggested_ask_ex: float | None
    tag: str
    listings: int
    searches_used: int
    confidence: str = "firm"      # firm | thin | very-thin
    relax_used: int = 0           # which ladder rung produced this
    p25_ex: float | None = None   # lower quartile; the ask is based on this
    skewed: bool = False          # low is far below the body of the market
    from_cache: bool = False      # replayed, so it cost nothing this time


def _confidence(count: int) -> str:
    if count >= MIN_SAMPLE:
        return "firm"
    return "thin" if count >= THIN_SAMPLE else "very-thin"


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(int(len(ordered) * fraction), len(ordered) - 1)
    return ordered[index]


# A low this far under the median is a dump listing, not the market price.
SKEW_RATIO = 10.0


def price_by_search(
    item: dict,
    category: str,
    index: dict[str, ModEntry],
    notables: dict[str, str],
    trade,
    cache: Cache,
    rates: dict[str, float],
    status: str = "any",
    min_results: int = 1,
    max_searches: int = 4,
) -> TradeResult:
    searches = 0
    best: TradeResult | None = None
    best_key: str | None = None
    rungs = min(len(RELAX_STEPS), max_searches)

    for step in range(rungs):
        query = build_query(item, category, index, notables, status=status, relax=step)
        key = query_hash(query)

        cached = cache.get("trade_price", key)
        if cached is not None:
            # Replaying a stored result costs no API call, so the search count
            # recorded when it was first computed must not be reported again.
            return TradeResult(**{**cached, "searches_used": 0, "from_cache": True})

        query_id, hashes = trade.search(query)
        searches += 1
        if len(hashes) < min_results:
            continue

        listings = trade.fetch(query_id, hashes[:FETCH_LIMIT])
        prices = [p for p in (l.to_exalted(rates) for l in listings) if p is not None]
        if not prices:
            continue

        cheapest = min(prices)
        middle = _median(prices)
        quartile = _percentile(prices, 0.25)
        skewed = middle > 0 and cheapest * SKEW_RATIO < middle

        # Base the ask on the lower quartile, not the single cheapest listing.
        # One person dumping an item at 0.2ex does not make it worth 0.2ex,
        # and an ask derived from that tells you to give the item away.
        basis = quartile if skewed else cheapest
        result = TradeResult(
            ceiling_ex=round(cheapest, 2),
            median_ex=round(middle, 2),
            p25_ex=round(quartile, 2),
            suggested_ask_ex=round(basis * SUGGESTED_ASK_FACTOR, 2),
            tag="exact" if step == 0 else f"relaxed:{step}",
            listings=len(prices),
            searches_used=searches,
            confidence=_confidence(len(hashes)),
            relax_used=step,
            skewed=skewed,
        )
        # Keep the first usable answer, but keep relaxing while the sample is
        # too small to trust — a wider search finds the ordinary listings that
        # actually set the price.
        if best is None:
            best, best_key = result, key
        if result.confidence == "firm":
            best, best_key = result, key
            break

    if best is None:
        return TradeResult(None, None, None, "unpriced:above-market", 0, searches)

    best = TradeResult(**{**best.__dict__, "searches_used": searches})
    if best_key:
        cache.put("trade_price", best_key, best.__dict__, ttl=TTL["trade_price"])
    return best
