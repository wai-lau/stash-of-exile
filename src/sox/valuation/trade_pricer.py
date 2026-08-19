"""Search, fetch, and turn listings into a price.

Zero results is information, not an error: nothing at least as good as our
item is currently listed. Those are the items most worth looking at by hand,
so they are flagged rather than dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

from sox.cache import TTL, Cache
from sox.valuation.allowlists import ModEntry
from sox.valuation.query import (
    RELAX_STEPS,
    build_query,
    meets_without_runes,
    query_hash,
)

SUGGESTED_ASK_FACTOR = 0.9
FETCH_LIMIT = 10   # the fetch endpoint takes at most 10 hashes per call
FETCH_CEILING = 30  # at most three calls when rune-inflated listings thin it

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
    listings: int                 # how many we priced: the cheapest FETCH_LIMIT
    searches_used: int
    matches: int = 0              # how many the search actually found
    rune_inflated: int = 0        # dropped: only cleared the floor via runes
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
    max_searches: int = 5,
) -> TradeResult:
    searches = 0
    best: TradeResult | None = None
    rungs = min(len(RELAX_STEPS), max_searches)

    for step in range(rungs):
        query = build_query(item, category, index, notables, status=status, relax=step)
        key = query_hash(query)

        # EVERY rung is cached, not only the winning one. Storing the winner
        # alone meant a second copy of the same item re-ran every rung before
        # it, live: a spear that had relaxed to the last rung paid four
        # searches to arrive back at the answer already on disk, and then
        # reported the whole replay as costing nothing.
        cached = cache.get("trade_price", key)
        if cached is not None:
            if cached.get("empty"):
                # Searched before and it answered nothing. Widen, as the first
                # walk did — re-asking cannot produce a different rung.
                continue
            result = TradeResult(**{**cached, "from_cache": True})
            if best is None:
                best = result
            if result.confidence == "firm":
                best = result
                break
            continue

        query_id, hashes, matches = trade.search(query)
        searches += 1
        if matches < min_results:
            cache.put("trade_price", key, {"empty": True}, ttl=TTL["trade_price"])
            continue

        # A listing can clear our defence floor purely on its socketed runes,
        # and it is then not a comparable — it is a worse item wearing our
        # defences. The buyer supplies their own runes, so those come off
        # before the listing counts. Live, all four cheapest matches for a
        # 1294-Evasion Forgotten Warden were rune-inflated: 1376 showing,
        # 1260 without.
        required = (query["query"]["filters"]
                    .get("equipment_filters", {}).get("filters", {}))
        listings, inflated = [], 0
        # Dropping them thins the sample, so read deeper rather than pricing
        # off whatever the first page happened to leave.
        for start in range(0, min(len(hashes), FETCH_CEILING), FETCH_LIMIT):
            batch = trade.fetch(query_id, hashes[start : start + FETCH_LIMIT])
            for listing in batch:
                if required and not meets_without_runes(listing.item, required):
                    inflated += 1
                    continue
                listings.append(listing)
            if len(listings) >= FETCH_LIMIT or not batch:
                break

        prices = [p for p in (l.to_exalted(rates) for l in listings) if p is not None]
        if not prices:
            cache.put("trade_price", key, {"empty": True}, ttl=TTL["trade_price"])
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
            matches=matches,
            rune_inflated=inflated,
            searches_used=searches,
            confidence=_confidence(matches),
            relax_used=step,
            skewed=skewed,
        )
        cache.put("trade_price", key, result.__dict__, ttl=TTL["trade_price"])
        # Keep the first usable answer, but keep relaxing while the sample is
        # too small to trust — a wider search finds the ordinary listings that
        # actually set the price.
        if best is None:
            best = result
        if result.confidence == "firm":
            best = result
            break

    if best is None:
        return TradeResult(None, None, None, "unpriced:above-market", 0, searches)

    # What this run actually spent, which on a full replay is nothing.
    return TradeResult(**{**best.__dict__, "searches_used": searches})
