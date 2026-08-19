"""Index lookup: which name an item is filed under."""

from sox.scout import IndexEntry
from sox.valuation.index_pricer import index_key, index_price_for

INDEX = {
    "Mageblood": IndexEntry("Mageblood", 135416.55, 5808, {}),
    "Uncut Skill Gem (Level 20)": IndexEntry("Uncut Skill Gem (Level 20)", 1595.5, 385, {}),
    "Uncut Skill Gem (Level 4)": IndexEntry("Uncut Skill Gem (Level 4)", 199.8, 19, {}),
    "Exalted Orb": IndexEntry("Exalted Orb", 1.0, 99999, {}),
}


def test_a_unique_is_filed_under_its_name_not_its_base():
    item = {"name": "Mageblood", "baseType": "Utility Belt", "frameType": 3}
    assert index_price_for(item, INDEX).price_ex == 135416.55


def test_gems_are_filed_by_level():
    """L20 and L4 share a name and differ ~8x in price."""
    high = {"typeLine": "Uncut Skill Gem (Level 20)", "baseType": "Uncut Skill Gem (Level 20)",
            "itemClass": "Uncut Skill Gems", "frameType": 5, "gemLevel": 20}
    low = {"typeLine": "Uncut Skill Gem (Level 4)", "baseType": "Uncut Skill Gem (Level 4)",
           "itemClass": "Uncut Skill Gems", "frameType": 5, "gemLevel": 4}
    assert index_price_for(high, INDEX).price_ex == 1595.5
    assert index_price_for(low, INDEX).price_ex == 199.8


def test_the_key_is_the_clipboard_base_name():
    """The game names an uncut gem exactly as the index files it.

    "Uncut Skill Gem (Level 19)" is both the clipboard base and the index
    key, so no reconstruction from properties is needed.
    """
    item = {"typeLine": "Uncut Skill Gem (Level 20)",
            "baseType": "Uncut Skill Gem (Level 20)", "frameType": 5}
    assert index_key(item) == "Uncut Skill Gem (Level 20)"


def test_currency_is_filed_under_its_base():
    item = {"typeLine": "Exalted Orb", "baseType": "Exalted Orb", "frameType": 5}
    assert index_price_for(item, INDEX).price_ex == 1.0


def test_an_unknown_item_returns_none_rather_than_a_wrong_price():
    assert index_price_for({"name": "Nonesuch", "frameType": 3}, INDEX) is None
