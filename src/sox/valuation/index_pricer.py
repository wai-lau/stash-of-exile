"""Look an item up in the index. No API calls happen here."""

from __future__ import annotations

from sox.scout import IndexEntry
from sox.valuation.classify import ItemClass, classify, display_name


def index_key(item: dict) -> str:
    """The name the index files this item under.

    Gems price by level and the clipboard already names them exactly as the
    index does — "Uncut Skill Gem (Level 19)" — so no reconstruction needed.
    """
    if classify(item) is ItemClass.UNIQUE:
        return item.get("name") or display_name(item)
    return item.get("baseType") or display_name(item)


def index_price_for(item: dict, index: dict[str, IndexEntry]) -> IndexEntry | None:
    for key in (index_key(item), display_name(item), item.get("baseType") or ""):
        if key and key in index:
            return index[key]
    return None
