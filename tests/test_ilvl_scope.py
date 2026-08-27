"""Item level constrains a craft base and nothing else — and as a floor.

A normal or magic item IS its item level: an ilvl 82 base rolls tiers an
ilvl 79 one cannot, and that is the whole of what a buyer is paying for. A
higher level rolls everything a lower one can and more, so it is a floor,
not a pin: a Bloodstone Amulet with Spirit and +1 minion skills pinned to
ilvl 36 exactly found nothing at four rungs while every ilvl-40 copy was at
least as good. A rare is bought on the mods it already rolled and a unique
on its roll, so on those the item level is a constraint that excludes
comparables while describing nothing anyone shops for.
"""

from pathlib import Path

from sox import itemtext
from sox.valuation.allowlists import load_mods, load_notables
from sox.valuation.mods import build_index
from sox.valuation.query import build_query, category_for

MODS = build_index(load_mods())
NOTABLES = load_notables()
FIXTURES = Path(__file__).parent / "fixtures" / "items"


def type_filters(name):
    item = itemtext.parse((FIXTURES / name).read_text())
    query = build_query(item, category_for(item), MODS, NOTABLES)
    return item, query["query"]["filters"]["type_filters"]["filters"]


def test_a_normal_base_is_floored_at_its_item_level():
    item, filters = type_filters("NormalItem.txt")
    assert filters["ilvl"] == {"min": item["ilvl"]}


def test_a_magic_base_is_floored_at_its_item_level():
    item, filters = type_filters("MagicItem.txt")
    assert filters["ilvl"] == {"min": item["ilvl"]}


def test_a_rare_is_not_constrained_by_item_level():
    _item, filters = type_filters("RareItem.txt")
    assert "ilvl" not in filters


def test_a_unique_is_not_constrained_by_item_level():
    _item, filters = type_filters("UniqueItem.txt")
    assert "ilvl" not in filters
