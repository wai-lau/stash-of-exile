"""Rendering must survive every price outcome.

A leftover two-value unpack in the unpriced branch crashed on any item that
came back without a price, while every priced item rendered fine.
"""

import pytest

from sox.report import PricedItem, render
from sox.valuation.classify import ItemClass

ITEM = {"name": "Doom Shield", "baseType": "Tower Shield", "ilvl": 70}
ROWS = (
    ("+96 to maximum Life", 3, "defence"),
    ("+145 to Evasion Rating", None, "equipment filter"),
    ("+8% to Fire Resistance", 0, ""),
)


@pytest.mark.parametrize("tag,price", [
    ("junk", None),
    ("unpriced:no-index", None),
    ("unpriced:unknown-class", None),
    ("unpriced:above-market", None),
    ("exact", 12.5),
    ("relaxed:2", 12.5),
])
def test_render_handles_every_outcome(tag, price):
    priced = PricedItem(
        name="Doom Shield", item_class=ItemClass.GEAR, price_ex=price,
        source="trade" if price else "unpriced", tag=tag, reason="score 4",
        score=4, breakdown=ROWS, listings=3 if price else 0,
        median_ex=price, p25_ex=price, item_class_name="Shields",
        category="armour.shield",
    )
    text = render(ITEM, priced, divine_ratio=320.0)
    assert "Doom Shield" in text
    assert "armour.shield" in text


def test_equipment_filter_mods_are_marked_not_dismissed():
    priced = PricedItem(
        name="Doom Shield", item_class=ItemClass.GEAR, price_ex=12.5,
        source="trade", tag="exact", reason="score 4", score=4,
        breakdown=ROWS, listings=9, median_ex=12.5, p25_ex=12.5,
    )
    text = render(ITEM, priced, divine_ratio=320.0)
    assert "(equipment filter)" in text, "a mod driving the search must not read as ignored"
    assert "+0  +145 to Evasion Rating" in text
