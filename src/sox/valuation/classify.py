"""Sort an item into the class that decides how it gets priced.

These are pricing paths, not game categories:
  CURRENCY / GEM / UNIQUE -> the index prices these for free
  JEWEL                   -> pure mod item; the index is often wrong
  GEAR                    -> trade search, scored by mods and base
  ENDGAME                 -> trade search; NO index covers these at all
  UNKNOWN                 -> never priced, never zeroed, always reported
"""

from __future__ import annotations

from enum import StrEnum

FRAME_NORMAL, FRAME_MAGIC, FRAME_RARE, FRAME_UNIQUE, FRAME_GEM, FRAME_CURRENCY = range(6)

# Classes with no index coverage — docs/research/2026-08-17-coverage-audit.md.
ENDGAME_MARKERS = (
    "Waystone", "Tablet", "Relic", "Charm", "Ultimatum", "Barya",
    "Breachstone", "Logbook", "Fragment", "Simulacrum",
)
UNKNOWN_MARKERS = ("Wombgift",)
JEWEL_MARKERS = ("Jewel",)

# Item Class values that identify a jewel regardless of name.
JEWEL_CLASSES = {"jewels", "jewel"}
GEM_CLASSES = {
    "uncut skill gems", "uncut spirit gems", "uncut support gems",
    "skill gems", "support gems", "meta gems",
}


class ItemClass(StrEnum):
    CURRENCY = "currency"
    GEM = "gem"
    UNIQUE = "unique"
    JEWEL = "jewel"
    GEAR = "gear"
    ENDGAME = "endgame"
    UNKNOWN = "unknown"


class Rarity(StrEnum):
    NORMAL = "normal"
    MAGIC = "magic"
    RARE = "rare"
    UNIQUE = "unique"


_FRAME_TO_RARITY = {
    FRAME_NORMAL: Rarity.NORMAL,
    FRAME_MAGIC: Rarity.MAGIC,
    FRAME_RARE: Rarity.RARE,
    FRAME_UNIQUE: Rarity.UNIQUE,
}


def display_name(item: dict) -> str:
    return item.get("name") or item.get("typeLine") or item.get("baseType") or "<unnamed>"


def rarity_of(item: dict) -> Rarity | None:
    frame = item.get("frameType")
    if isinstance(frame, int) and frame in _FRAME_TO_RARITY:
        return _FRAME_TO_RARITY[frame]
    raw = item.get("rarity")
    if isinstance(raw, str):
        try:
            return Rarity(raw.casefold())
        except ValueError:
            return None
    return None


def classify(item: dict) -> ItemClass:
    name = display_name(item)
    base = item.get("baseType") or ""
    item_class = (item.get("itemClass") or "").casefold()

    if any(marker in name for marker in UNKNOWN_MARKERS):
        return ItemClass.UNKNOWN

    if item_class in GEM_CLASSES or item.get("frameType") == FRAME_GEM:
        return ItemClass.GEM
    # Uncut gems report Rarity: Currency but are gems by item class.
    if item_class.startswith("uncut"):
        return ItemClass.GEM

    if item.get("frameType") == FRAME_CURRENCY and not item_class.startswith("uncut"):
        return ItemClass.CURRENCY

    # Endgame markers win over rarity: a rare Waystone is still a waystone,
    # and pricing it down the gear path would search the wrong category.
    if item_class in {"waystones", "tablet", "relics", "charms"} or any(
        marker in name or marker in base for marker in ENDGAME_MARKERS
    ):
        return ItemClass.ENDGAME

    rarity = rarity_of(item)
    if item_class in JEWEL_CLASSES or any(m in base for m in JEWEL_MARKERS):
        # A unique jewel still prices off the index first, so keep UNIQUE.
        return ItemClass.UNIQUE if rarity is Rarity.UNIQUE else ItemClass.JEWEL

    if rarity is Rarity.UNIQUE:
        return ItemClass.UNIQUE
    if rarity in (Rarity.NORMAL, Rarity.MAGIC, Rarity.RARE):
        return ItemClass.GEAR
    return ItemClass.UNKNOWN
