"""Human-readable price output.

An item we could not price is shown as unpriced, never as zero: a quiet
omission reads as "worth nothing", which is exactly the wrong impression.
"""

from __future__ import annotations

from dataclasses import dataclass

from sox.valuation.classify import ItemClass, display_name

# Mods that made it into the query are highlighted, so the score breakdown
# shows at a glance which mods the price actually rests on.
SEARCHED = "\033[36m"
# The market row is the answer; everything else on screen explains it.
MARKET = "\033[1;33m"
# Priced in divine, the same colour is worn inside out. A divine price is the
# one worth spotting while a watch session scrolls past, and inverting the row
# reads from across the room where a second hue would just be another colour.
MARKET_DIV = "\033[1;33;7m"
RESET = "\033[0m"


@dataclass(frozen=True)
class PricedItem:
    name: str
    item_class: ItemClass
    price_ex: float | None
    source: str            # index | exchange | trade | unpriced
    tag: str | None
    reason: str = ""
    listings: int = 0              # how many we priced
    matches: int = 0               # how many the search found
    rune_inflated: int = 0         # dropped: only met the floor via runes
    from_cache: bool = False
    median_ex: float | None = None
    p25_ex: float | None = None
    confidence: str = "firm"
    skewed: bool = False
    relax_used: int = 0
    score: int = 0
    breakdown: tuple[tuple[str, object], ...] = ()
    coherence_group: str | None = None
    coherence_count: int = 0
    coherence_bonus: int = 0
    suggested_ask_ex: float | None = None
    searches_used: int = 0
    quantity: int = 0
    offers: int = 0        # exchange: sellers in the book
    stock: int = 0         # exchange: units they hold
    ask_ex: float | None = None   # exchange: what one costs to buy
    bid_ex: float | None = None   # exchange: what someone will pay
    searched_group: str | None = None
    searched_stats: tuple[str, ...] = ()
    searched_texts: tuple[str, ...] = ()   # the ITEM's wording, for highlighting
    item_class_name: str = ""              # the game's own label, e.g. "Quarterstaves"
    category: str | None = None            # the trade category searched
    roll_pct: float | None = None
    best_roll_pct: float | None = None


# Exalted and divine only. Chaos does sit between them in PoE2 — about 33 ex
# against divine's 340 — but nobody quotes an item in chaos, so pricing into
# it is a conversion the reader has to undo.
PRICE_UNITS = (("divine", "div"),)


def fmt_price(ex: float | None, rates: dict[str, float]) -> str:
    """One currency, the largest that still reads as at least one of them.

    "1,600 ex (5.0 div)" made you convert in your head to compare against a
    market that quotes divine. The unit a price is quoted in IS information.
    """
    if ex is None:
        return "—"
    for code, label in PRICE_UNITS:
        rate = rates.get(code) or 0.0
        if rate > 0 and ex >= rate:
            return f"{_round(ex / rate)} {label}"
    return f"{_round(ex)} ex"


def fmt_row(values: list[float | None], rates: dict[str, float]) -> list[str]:
    """Several prices in ONE unit, chosen from the first of them.

    Per-value units made a row unreadable: "low 9 ex · 25th 32.23 ex · median
    3.5 chaos" is ascending, but you have to know chaos is 33 ex to see it.
    The row exists to be compared across, so it gets one scale.

    Anchored on the low rather than the largest value, because the largest
    pushes the low into fractions — "0.27 chaos · 0.96 chaos · 3.5 chaos"
    reads worse than "9 ex · 32.23 ex · 117 ex".
    """
    real = [v for v in values if v is not None]
    if not real:
        return ["—" for _ in values]
    anchor = real[0]
    for code, label in PRICE_UNITS:
        rate = rates.get(code) or 0.0
        if rate > 0 and anchor >= rate:
            return [f"{_round(v / rate)} {label}" if v is not None else "—"
                    for v in values]
    return [f"{_round(v)} ex" if v is not None else "—" for v in values]


def _round(value: float) -> str:
    if value >= 100:
        return f"{value:,.0f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")



def _price_lines(priced: PricedItem, rates: dict[str, float]) -> list[str]:
    """The market or index block.

    Rendered last: the score and coherence explain how the number was
    arrived at, and the number is what you read off the end.
    """
    out: list[str] = []
    if priced.price_ex is None:
        if priced.tag == "junk":
            # The generic advice about --force lives in the watch banner; on
            # every junk item it is noise. What is worth showing is what this
            # particular item scored, and why.
            # The score block below prints the same rows, so the verdict
            # only needs the number it failed against.
            out.append(f"  verdict    JUNK  (score {priced.score}, needs 6)")
        elif priced.tag == "unpriced:above-market":
            out.append("  price      no comparable listing")
            out.append("             nothing at least as good is listed — "
                         "price this one by hand, it may be the good one")
        else:
            out.append(f"  price      not priced ({priced.tag or 'unknown'})")
    elif priced.source == "trade":
        # Put a weak sample FIRST and in capitals. Buried under the numbers it
        # reads as a footnote, and the number gets believed anyway.
        if priced.confidence == "very-thin":
            out.append(f"  !! GUESS   only {priced.listings} comparable listing"
                         f"{'s' if priced.listings != 1 else ''} exist — "
                         "this is NOT a price")
        elif priced.confidence == "thin":
            out.append(f"  !! THIN    only {priced.listings} comparable listings — "
                         "treat the number as a rough bound")
        low, p25, median = fmt_row(
            [priced.price_ex, priced.p25_ex, priced.median_ex], rates)
        # Only the amounts are lit. "low", "25th" and "median" are labels, and
        # a row lit end to end makes them compete with the numbers they name —
        # the answer is three prices, and the words are how to read them.
        #
        # fmt_row quotes the whole row in one unit, chosen from the low, so
        # the low is what says which unit this price is in.
        colour = MARKET_DIV if low.endswith(" div") else MARKET
        market = f"low {colour}{low}{RESET}"
        if priced.p25_ex is not None:
            market += f"  ·  25th {colour}{p25}{RESET}"
        if priced.median_ex is not None:
            market += f"  ·  median {colour}{median}{RESET}"
        out.append(f"  market     {market}")
        # `listings` is only ever the cheapest handful — one fetch call is
        # enough to find the low end, so it reads 10 for anything with a real
        # market. The match count is the number that says whether the price is
        # supported, so lead with it.
        if priced.matches and priced.matches > priced.listings:
            found = (f"cheapest {priced.listings} of {priced.matches:,} "
                     f"listings, {priced.tag}")
        else:
            found = f"{priced.listings} listings, {priced.tag}"
        # A replayed price cost no API call and can be stale, which is worth
        # a word — but not a row of its own.
        if priced.from_cache:
            found += ", cached"
        out.append(f"             {found}")
        if priced.rune_inflated:
            out.append(f"             {priced.rune_inflated} skipped — met your "
                         "numbers only with their runes")
        if priced.relax_used:
            out.append("             found only after widening, so these comparables "
                         "are weaker than your item — read the price as a floor")

    elif priced.source == "exchange":
        # The deep book. Depth is the evidence here, not listing count: the
        # junk end of every book is thin, and stock is what steps over it.
        out.append(f"  exchange   {fmt_price(priced.price_ex, rates)}"
                     + (f"   ({priced.offers:,} offers, {priced.stock:,} in stock)"
                        if priced.offers else ""))
        # The spread is the honest width of the answer. A price quoted without
        # it reads as one number when the market is two.
        if priced.bid_ex is not None and priced.ask_ex is not None:
            bid, ask = fmt_row([priced.bid_ex, priced.ask_ex], rates)
            out.append(f"             bid {bid}  ·  ask {ask}")
        elif priced.ask_ex is not None and priced.offers:
            out.append("             ask only — nobody is bidding for these")
        if priced.stock and priced.stock < 20:
            out.append("             thin book — few units are actually offered")

    else:
        out.append(f"  index      {fmt_price(priced.price_ex, rates)}"
                     + (f"   ({priced.quantity:,} listed)" if priced.quantity else ""))
        if priced.quantity and priced.quantity < 20:
            out.append("             thin market — index price is weak evidence")
        if priced.roll_pct is not None:
            # The mean alone reads as a verdict on the item, but escalation
            # turns on the BEST roll — a copy with one near-perfect
            # build-defining roll beside poor filler is what the market pays
            # for, and an average of 47% hides it completely.
            best = priced.best_roll_pct
            band = ("well rolled" if priced.roll_pct >= 0.75
                    else "poorly rolled" if priced.roll_pct <= 0.25 else "average roll")
            if best is not None and best >= 0.75 and priced.roll_pct < 0.75:
                band = f"average overall, best {best * 100:.0f}th"
            out.append(f"  rolls      {priced.roll_pct * 100:.0f}th percentile "
                         f"({band})")
            if priced.roll_pct >= 0.75:
                out.append("             the index price is a floor across all "
                             "copies; yours is better than most")

    return out


def render(item: dict, priced: PricedItem, rates: dict[str, float]) -> str:
    lines = [
        f"{display_name(item)}"
        + (f"  [{item.get('baseType')}]" if item.get("name") else ""),
        f"  type       {priced.item_class_name or priced.item_class}"
        + (f" → {priced.category}" if priced.category else "")
        + (f"   ilvl {item['ilvl']}" if item.get("ilvl") else ""),
    ]

    if priced.searched_stats and priced.searched_group:
        # The mods themselves are highlighted in the score breakdown, so
        # listing them again here would say the same thing twice.
        lines.append(f"  searched   as {priced.searched_group}")
    if priced.breakdown:
        lines.append(f"  score      {priced.score}")
        width = max((len(str(t)) for t, _, _ in priced.breakdown), default=0)
        for text, weight, tag in priced.breakdown:
            if weight is None:
                # +0 says it all when the mod is simply unknown — but a
                # defence mod scores nothing while still driving the search
                # through the item's total, and that must not read as ignored.
                mark, note = "+0", f"({tag})" if tag else ""
            elif weight == 0:
                mark, note = "·", "(mod cap)"
            else:
                mark, note = f"+{weight}", f"({tag})" if tag else ""
            row = f"{mark:<3} {str(text):<{width}}  {note}".rstrip()
            if text in priced.searched_texts:
                row = f"{SEARCHED}{row}{RESET}"
            lines.append(f"             {row}")
        if priced.coherence_group and priced.coherence_count > 1:
            gain = f"  +{priced.coherence_bonus}" if priced.coherence_bonus else ""
            lines.append(f"  coherence  {priced.coherence_count} mods cluster on "
                         f"{priced.coherence_group}{gain}")
        elif priced.breakdown:
            lines.append("  coherence  none — the mods serve different builds")

    # Last: the score and coherence explain how the number was arrived at, and
    # the number is what you read off the end.
    lines += _price_lines(priced, rates)
    return "\n".join(lines)
