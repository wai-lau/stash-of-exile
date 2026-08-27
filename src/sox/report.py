"""Human-readable price output.

An item we could not price is shown as unpriced, never as zero: a quiet
omission reads as "worth nothing", which is exactly the wrong impression.
"""

from __future__ import annotations

from dataclasses import dataclass

from sox.valuation.classify import (
    ItemClass,
    Rarity,
    classify,
    display_name,
    rarity_of,
)

# The market row is the answer; everything else on screen explains it.
# The query the price rests on is blue; the mods it does not rest on are dim.
BLUE = "\033[94m"
MARKET = "\033[1;33m"
# Priced in divine, the same colour is worn inside out. A divine price is the
# one worth spotting while a watch session scrolls past, and inverting the row
# reads from across the room where a second hue would just be another colour.
MARKET_DIV = "\033[1;33;7m"
# The dump listing at the bottom of a skewed book. Lit like the other two it
# reads as a price; dimmed, it reads as what it is — the number the row has to
# carry because the session total counts it, and the one not to trade on.
DIM = "\033[2m"
RESET = "\033[0m"
# The mods the price does NOT account for are the reason to doubt the
# number, so they are lit, not dimmed into the margin. Bright red.
UNSEARCHED = "\033[91m"
# A waystone's killer mods: deadly in the same red as the mods a price
# does not account for, risky in yellow.
RISKY = "\033[33m"
# A waystone's loot score wears its band as a colour, bold: grey reroll,
# blue run it, yellow juice it, green chase. The word would be the colour
# said twice.
LOOT_COLOURS = {
    "reroll": "\033[1;90m",
    "run it": "\033[1;94m",
    "juice it": "\033[1;33m",
    "chase": "\033[1;92m",
}

# The game's own rarity hues, as the tooltip paints an item's header —
# yellow rare, blue magic, orange unique, white normal — and the name and
# base share the one colour there, so they do here. 24-bit, because the
# nearest of the terminal's sixteen is a different yellow; a terminal
# without it rounds to its own palette. Brighter than the tooltip's own
# values: those sit on a near-black panel, and its unique orange
# (175, 96, 37) read as brown on a terminal.
RARITY_RGB = {
    Rarity.NORMAL: (255, 255, 255),
    Rarity.MAGIC: (160, 160, 255),
    Rarity.RARE: (255, 255, 119),
    Rarity.UNIQUE: (255, 140, 0),
}
# A gem or a currency has no rarity; the game paints those by what they are.
CLASS_RGB = {
    ItemClass.GEM: (64, 224, 208),
    ItemClass.CURRENCY: (222, 205, 160),
}


def title_colour(item: dict) -> str:
    rgb = RARITY_RGB.get(rarity_of(item)) or CLASS_RGB.get(classify(item))
    return "\033[38;2;%d;%d;%dm" % rgb if rgb else ""


def title(item: dict) -> str:
    """The item's name and base, in the colour the game gives its rarity."""
    text = display_name(item) + (
        f"  [{item.get('baseType')}]" if item.get("name") else "")
    colour = title_colour(item)
    return f"{colour}{text}{RESET}" if colour else text


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
    quoted: str = ""            # exchange: the currency the book was read in
    traded_ex: float = 0.0        # exchange fills: exalted actually exchanged
    searched_group: str | None = None
    searched_stats: tuple[str, ...] = ()
    query_stats: tuple[str, ...] = ()      # each filter sent, with its floor
    unsearched: tuple[tuple[str, str], ...] = ()   # (mod text, why not)
    map_stats: tuple[str, ...] = ()        # waystone minimums the search sent
    loot: tuple[float, str] | None = None  # waystone loot score and verdict
    instill: object | None = None          # waystone Instillation path
    item_class_name: str = ""            # the game's own label, e.g. "Quarterstaves"
    category: str | None = None            # the trade category searched
    roll_pct: float | None = None
    best_roll_pct: float | None = None


# The trade engine never reports more matches than this, and past it the
# match set is truncated before the price sort runs.
SEARCH_CAP = 10_000

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
        # A low more than ten times under the median is one seller dumping.
        # The row still leads with it — it is the number the session total
        # counts — but marked, so the row itself says which of its three
        # prices to read rather than leaving that to a line above it.
        market = (f"low {DIM}{low} (dump){RESET}" if priced.skewed
                  else f"low {colour}{low}{RESET}")
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
        # Past the cap the trade engine truncates BEFORE sorting. Measured
        # live: tier 15+ alone floored at 3 ex while the strictly narrower
        # tier 15+, rarity 24+ floored at 1 — impossible in one market unless
        # each "cheapest" is the cheapest of a different sample. It is.
        if priced.matches >= SEARCH_CAP:
            out.append("             every search past the cap reads a sample — "
                         "the low is its floor, not the market's")
        if priced.rune_inflated:
            out.append(f"             {priced.rune_inflated} skipped — met your "
                         "numbers only with their runes")
        if priced.relax_used:
            out.append("             found only after widening, so these comparables "
                         "are weaker than your item — read the price as a floor")
        # Last, with the other caveats: the numbers are the answer, and this
        # says how to read them. Live, a ring whose comparables ran 1 / 20 /
        # 45 ex was reported as a 1 ex item, and four items priced that way
        # totalled 4 ex for the session.
        if priced.skewed and priced.price_ex and priced.median_ex:
            times = priced.median_ex / priced.price_ex
            out.append(f"             the low is {times:,.0f}x under the median — "
                         "read the 25th as the price")

    elif priced.source == "exchange":
        # The deep book. Depth is the evidence here, not listing count: the
        # junk end of every book is thin, and stock is what steps over it.
        #
        # Dressed like the market row: the amount is lit, and a divine price
        # wears the colour inside out. The unit alone also says which book the
        # number came off — an item dear enough trades against divine and
        # hardly at all against exalted, and a price reading "div" IS that
        # fact, so it is not repeated in words below.
        price = fmt_price(priced.price_ex, rates)
        colour = MARKET_DIV if price.endswith(" div") else MARKET
        out.append(f"  exchange   {colour}{price}{RESET}"
                     + (f"   ({priced.offers:,} offers, {priced.stock:,} in stock)"
                        if priced.offers else ""))
        # The spread is the honest width of the answer. A price quoted without
        # it reads as one number when the market is two.
        if priced.bid_ex is not None and priced.ask_ex is not None:
            bid, ask = fmt_row([priced.bid_ex, priced.ask_ex], rates)
            colour = MARKET_DIV if bid.endswith(" div") else MARKET
            out.append(f"             bid {colour}{bid}{RESET}"
                       f"  ·  ask {colour}{ask}{RESET}")
        elif priced.ask_ex is not None and priced.offers:
            out.append("             ask only — nobody is bidding for these")
        if priced.stock and priced.stock < 20:
            out.append("             thin book — few units are actually offered")
        # A fills price rests on completed trades, not listings — the one
        # measurement bait cannot touch — and the row says which it is.
        if priced.quoted == "fills":
            out.append(f"             the game's own exchange — "
                         f"{priced.traded_ex:,.0f} ex of it traded in the "
                         "last snapshot")
        # Rerouted here from a search that hit the 10,000 cap, where the sort
        # reads only a kept sample. Said out loud, or the exchange row makes
        # the item look like it was never searchable at all.
        # A stone worth juicing whose search found nothing: what is listed
        # is all worse than it on some total, so the book by tier is a
        # floor, not a price.
        if priced.tag == "waystone-floor":
            out.append("             no stone at least as good on every total is "
                       "listed — the tier's book is the floor, yours is worth more")
        if priced.tag == "capped-search":
            out.append("             the item search capped at 10,000 and reads "
                         "a sample — this is the bulk book instead")

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
    # The rarity in words as well as in the title's colour: colour does not
    # grep, and a junk rare is never searched, so the search pin that names
    # the rarity never printed and nothing said the talisman was rare.
    rarity = rarity_of(item)
    lines = [
        title(item),
        "  type       "
        + (f"{rarity.value} · " if rarity else "")
        + f"{priced.item_class_name or priced.item_class}"
        + (f" → {priced.category}" if priced.category else "")
        + (f"   ilvl {item['ilvl']}" if item.get("ilvl") else ""),
    ]

    if priced.searched_stats and priced.searched_group:
        # The mods themselves are highlighted in the score breakdown, so
        # listing them again here would say the same thing twice. The query
        # lines are different information: the floors it asked at, and the
        # pseudo sums, which appear nowhere else.
        lines.append(f"  searched   as {priced.searched_group}")
        for line in priced.query_stats:
            lines.append(f"             {BLUE}{line}{RESET}")
    elif priced.query_stats:
        # No dominant buyer, no "as" — but the query still went out and its
        # floors still need stating.
        head, *rest = priced.query_stats
        lines.append(f"  searched   {BLUE}{head}{RESET}")
        lines += [f"             {BLUE}{line}{RESET}" for line in rest]
    if priced.map_stats:
        # Spelled out in full, unlike the mods above: a waystone's stats are
        # properties, not mods, so they appear nowhere in the breakdown and
        # this line is their only mention.
        lines.append("  searched   " + "  ·  ".join(priced.map_stats))
    if priced.loot:
        # The tooltip shows the totals and no opinion; this is the opinion.
        score, verdict = priced.loot
        lines.append(f"  loot       {LOOT_COLOURS.get(verdict, '')}{score}{RESET}")
    if priced.instill:
        # The emotion to instill, and the score and band one of it buys.
        path = priced.instill
        if path.blocked:
            lines.append("  instill    " + {
                "corrupted": "corrupted — cannot be instilled",
                "instilled": "already instilled",
            }.get(path.blocked, path.blocked))
        else:
            colour = LOOT_COLOURS.get(path.verdict, "")
            lines.append(f"  instill    {path.emotion} → {colour}{path.score}{RESET}"
                         f"  ·  {path.delirious}% delirious")
    if priced.loot:
        # The mods that kill the player, deadly red and risky yellow: the
        # score says nothing about whether the map can be run, and nothing
        # else on a stone's report shows its mods.
        from sox.valuation.danger import dangers

        flagged = dangers(item)
        if flagged:
            tint = {"deadly": UNSEARCHED, "risky": RISKY}
            lines.append("  danger     " + "  ·  ".join(
                f"{tint[tier]}{text}{RESET}" for text, tier in flagged))
    if priced.unsearched:
        # The query lines above say what the price rests on; this is the
        # rest of the item — dropped by widening, or never searchable — so
        # the reader knows what the number does NOT account for.
        width = max(len(text) for text, _ in priced.unsearched)
        rows = [f"{UNSEARCHED}{text:<{width}}  ({why}){RESET}"
                for text, why in priced.unsearched]
        lines.append(f"  unsearched {rows[0]}")
        lines += [f"             {row}" for row in rows[1:]]
    # Not for a waystone: its mods are difficulty, and a cluster of them
    # serves no buyer.
    if priced.breakdown and not priced.loot:
        if priced.coherence_group and priced.coherence_count > 1:
            gain = f"  +{priced.coherence_bonus}" if priced.coherence_bonus else ""
            lines.append(f"  coherence  {priced.coherence_count} mods cluster on "
                         f"{priced.coherence_group}{gain}")
        else:
            lines.append("  coherence  none — the mods serve different builds")

    # Last: the rows above explain how the number was arrived at, and the
    # number is what you read off the end.
    lines += _price_lines(priced, rates)
    return "\n".join(lines)
