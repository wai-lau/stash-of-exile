"""Human-readable price output.

An item we could not price is shown as unpriced, never as zero: a quiet
omission reads as "worth nothing", which is exactly the wrong impression.
"""

from __future__ import annotations

from dataclasses import dataclass

from sox.valuation.classify import ItemClass, display_name


@dataclass(frozen=True)
class PricedItem:
    name: str
    item_class: ItemClass
    price_ex: float | None
    source: str            # index | trade | unpriced
    tag: str | None
    reason: str = ""
    listings: int = 0
    suggested_ask_ex: float | None = None
    searches_used: int = 0
    quantity: int = 0


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
        f"  class      {priced.item_class}"
        + (f"  ilvl {item['ilvl']}" if item.get("ilvl") else ""),
    ]

    if priced.price_ex is None:
        lines.append(f"  price      not priced ({priced.tag or 'unknown'})")
        if priced.tag == "unpriced:above-market":
            lines.append("             nothing at least as good is listed — "
                         "price this one by hand, it may be the good one")
    elif priced.source == "trade":
        lines.append(f"  ceiling    {fmt_price(priced.price_ex, divine_ratio)}"
                     f"   ({priced.listings} listings, {priced.tag})")
        lines.append(f"  ask        {fmt_price(priced.suggested_ask_ex, divine_ratio)}")
        lines.append("             cheapest listing at least as good as yours, "
                     "so treat it as a ceiling, not a comp")
    else:
        lines.append(f"  index      {fmt_price(priced.price_ex, divine_ratio)}"
                     + (f"   ({priced.quantity:,} listed)" if priced.quantity else ""))
        if priced.quantity and priced.quantity < 20:
            lines.append("             thin market — index price is weak evidence")

    if priced.reason:
        lines.append(f"  why        {priced.reason}")
    if priced.searches_used:
        lines.append(f"  cost       {priced.searches_used} search"
                     f"{'es' if priced.searches_used != 1 else ''}")
    return "\n".join(lines)
