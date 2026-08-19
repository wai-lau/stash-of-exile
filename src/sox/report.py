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
RESET = "\033[0m"


@dataclass(frozen=True)
class PricedItem:
    name: str
    item_class: ItemClass
    price_ex: float | None
    source: str            # index | trade | unpriced
    tag: str | None
    reason: str = ""
    listings: int = 0
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
    searched_group: str | None = None
    searched_stats: tuple[str, ...] = ()
    searched_texts: tuple[str, ...] = ()   # the ITEM's wording, for highlighting
    item_class_name: str = ""              # the game's own label, e.g. "Quarterstaves"
    category: str | None = None            # the trade category searched
    roll_pct: float | None = None


def fmt_price(ex: float | None, divine_ratio: float) -> str:
    if ex is None:
        return "—"
    if divine_ratio > 0 and ex >= divine_ratio:
        return f"{ex:,.0f} ex ({ex / divine_ratio:,.1f} div)"
    return f"{ex:,.1f} ex"


def render(item: dict, priced: PricedItem, divine_ratio: float) -> str:
    lines = [
        f"{display_name(item)}"
        + (f"  [{item.get('baseType')}]" if item.get("name") else ""),
        f"  type       {priced.item_class_name or priced.item_class}"
        + (f" → {priced.category}" if priced.category else "")
        + (f"   ilvl {item['ilvl']}" if item.get("ilvl") else ""),
    ]

    if priced.price_ex is None:
        if priced.tag == "junk":
            # The generic advice about --force lives in the watch banner; on
            # every junk item it is noise. What is worth showing is what this
            # particular item scored, and why.
            # The score block below prints the same rows, so the verdict
            # only needs the number it failed against.
            lines.append(f"  verdict    JUNK  (score {priced.score}, needs 6)")
        elif priced.tag == "unpriced:above-market":
            lines.append("  price      no comparable listing")
            lines.append("             nothing at least as good is listed — "
                         "price this one by hand, it may be the good one")
        else:
            lines.append(f"  price      not priced ({priced.tag or 'unknown'})")
    elif priced.source == "trade":
        # Put a weak sample FIRST and in capitals. Buried under the numbers it
        # reads as a footnote, and the number gets believed anyway.
        if priced.confidence == "very-thin":
            lines.append(f"  !! GUESS   only {priced.listings} comparable listing"
                         f"{'s' if priced.listings != 1 else ''} exist — "
                         "this is NOT a price")
        elif priced.confidence == "thin":
            lines.append(f"  !! THIN    only {priced.listings} comparable listings — "
                         "treat the number as a rough bound")
        market = f"low {fmt_price(priced.price_ex, divine_ratio)}"
        if priced.p25_ex is not None:
            market += f"  ·  25th {fmt_price(priced.p25_ex, divine_ratio)}"
        if priced.median_ex is not None:
            market += f"  ·  median {fmt_price(priced.median_ex, divine_ratio)}"
        lines.append(f"  market     {market}")
        lines.append(f"             {priced.listings} listings, {priced.tag}")
        if priced.skewed:
            lines.append("             the low is far under the rest of the market — "
                         "someone is dumping, so the ask uses the 25th percentile")
        if priced.relax_used:
            lines.append("             found only after widening, so these comparables "
                         "are weaker than your item — read the price as a floor")

    else:
        lines.append(f"  index      {fmt_price(priced.price_ex, divine_ratio)}"
                     + (f"   ({priced.quantity:,} listed)" if priced.quantity else ""))
        if priced.quantity and priced.quantity < 20:
            lines.append("             thin market — index price is weak evidence")
        if priced.roll_pct is not None:
            band = ("well rolled" if priced.roll_pct >= 0.75
                    else "poorly rolled" if priced.roll_pct <= 0.25 else "average roll")
            lines.append(f"  rolls      {priced.roll_pct * 100:.0f}th percentile "
                         f"({band})")
            if priced.roll_pct >= 0.75:
                lines.append("             the index price is a floor across all "
                             "copies; yours is better than most")

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
                mark, note = "·", "(minor-mod cap reached)"
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
    if priced.from_cache:
        lines.append("  cost       cached (no API call)")
    elif priced.searches_used:
        lines.append(f"  cost       {priced.searches_used} search"
                     f"{'es' if priced.searches_used != 1 else ''}")
    return "\n".join(lines)
