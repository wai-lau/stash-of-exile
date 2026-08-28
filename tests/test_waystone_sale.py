"""Which waystones are for sale.

Measured 2026-08-28 (docs/waystones.md): a fifteen is 1 ex until one
total nears the top of its range, and then it is a divine whatever the
total does for loot — item rarity 80 sells for 260 ex and scores 40. The
owner runs the stones worth running and sells the ones that look good
and are not, so the search opens on the second kind only.
"""

from pathlib import Path

from sox import itemtext
from sox.valuation.query import (RUN_LOOT, SALE_CLIFFS, for_sale, loot_score, sale_texts,
                                 waystone_filters, waystone_tier)

FIXTURES = Path(__file__).parent / "fixtures" / "items"


def load(name):
    return itemtext.parse((FIXTURES / f"{name}.txt").read_text())


def test_a_stone_that_looks_good_but_is_not_is_for_sale():
    item = load("SellableMap")
    assert loot_score(item) == (67, "run it")
    assert for_sale(item) == ("Item Rarity",)
    assert sale_texts(item) == ("item rarity 84% clears 80",)


def test_a_sale_is_searched_on_the_totals_that_sell_it():
    """Measured: item rarity 80 alone is 148 listings at 260 ex; the same
    stone floored on all five totals at once matches nothing, and the
    market prices the one total anyway."""
    item = load("SellableMap")
    assert waystone_filters(item, "map.waystone", only=for_sale(item)) == {
        "map_tier": {"min": 15}, "map_iir": {"min": 84}}
    assert waystone_filters(item, "map.waystone", only=()) == {"map_tier": {"min": 15}}
    assert len(waystone_filters(item, "map.waystone")) == 6


def test_a_stone_worth_running_is_not_for_sale_however_it_rolled():
    """Monster rarity 70 clears its cliff, and the stone scores 110: run it."""
    item = load("JuicyMap")
    assert loot_score(item)[0] >= RUN_LOOT
    assert for_sale(item) == ()


def test_a_stone_under_every_cliff_is_the_books_to_price():
    assert for_sale(load("GhostExpedition")) == ()
    assert for_sale(load("RareMap")) == ()


def test_gear_is_never_for_sale_here():
    assert for_sale(load("RareItem")) == ()


def test_the_cliffs_are_the_measured_ones():
    assert SALE_CLIFFS == {"Monster Rarity": 60, "Pack Size": 35, "Item Rarity": 80,
                           "Monster Effectiveness": 50, "Waystone Drop Chance": 120}
    assert RUN_LOOT == 70


def test_the_tier_is_read_off_the_base():
    assert waystone_tier(load("SellableMap")) == 15
    assert waystone_tier(load("RareMapFakeAllProps")) == 16
    assert waystone_tier(load("RareItem")) is None
