"""Build a trade2 query.

The search is NOT "find my item". It is "find the cheapest item at least as
good as mine", so every constraint is a minimum at our item's value and there
are no maximums. Every listing returned is therefore >= ours on every
constrained axis, which makes the cheapest one a CEILING on our ask rather
than a comparable sale.
"""

from __future__ import annotations

import hashlib
import json

from sox.valuation.allowlists import ModEntry
from sox.valuation.classify import ItemClass, Rarity, classify, rarity_of
from sox.valuation.mods import match_mod
from sox.valuation.rolls import parse_values

# Relaxation ladder: (scale applied to every minimum, how many stats to keep).
# Constraining on EVERY matched mod is what a naive implementation does and it
# returns nothing: demanding fire AND cold AND lightning AND accuracy at once
# describes one item in the world. Buyers search on the few mods that define
# the item, so we keep the highest-weight ones and loosen from there.
RELAX_STEPS = ((1.0, 3), (0.9, 3), (0.75, 2), (0.75, 1))

MAX_STATS_DEFAULT = 3

# Clipboard property name -> equipment_filters id, verified against
# /api/trade2/data/filters.
DEFENCE_PROPERTIES = {
    "Energy Shield": "es",
    "Armour": "ar",
    "Evasion Rating": "ev",
    "Evasion": "ev",
    "Runic Ward": "ward",
    "Spirit": "spirit",
}

# Item Class -> trade category. Item Class comes straight from the clipboard.
ITEM_CLASS_CATEGORIES = {
    "body armours": "armour.chest", "helmets": "armour.helmet",
    "gloves": "armour.gloves", "boots": "armour.boots",
    "shields": "armour.shield", "foci": "armour.focus",
    "quivers": "armour.quiver", "bucklers": "armour.buckler",
    "amulets": "accessory.amulet", "rings": "accessory.ring",
    "belts": "accessory.belt",
    "bows": "weapon.bow", "crossbows": "weapon.crossbow",
    "wands": "weapon.wand", "sceptres": "weapon.sceptre",
    "staves": "weapon.staff", "quarterstaves": "weapon.warstaff",
    "spears": "weapon.spear", "one hand maces": "weapon.onemace",
    "two hand maces": "weapon.twomace", "flails": "weapon.flail",
    "daggers": "weapon.dagger", "claws": "weapon.claw",
    "jewels": "jewel",
    "waystones": "map.waystone", "tablet": "map.tablet",
    "relics": "sanctum.relic", "charms": "flask.charm",
    "life flasks": "flask.life", "mana flasks": "flask.mana",
}


class _Notable:
    """A notable allocation, which has an exact id and no numeric minimum."""

    __slots__ = ("stat_id",)

    def __init__(self, stat_id: str) -> None:
        self.stat_id = stat_id


def category_for(item: dict) -> str | None:
    return ITEM_CLASS_CATEGORIES.get((item.get("itemClass") or "").casefold())


def _property(item: dict, name: str) -> int | None:
    for prop in item.get("properties") or []:
        if prop.get("name") == name:
            values = prop.get("values") or []
            if values and values[0]:
                try:
                    return int(str(values[0][0]).split()[0].rstrip("%"))
                except (ValueError, IndexError):
                    return None
    return None


def build_query(
    item: dict,
    category: str,
    index: dict[str, ModEntry],
    notables: dict[str, str],
    status: str = "online",
    relax: int = 0,
) -> dict:
    scale, max_stats = RELAX_STEPS[min(relax, len(RELAX_STEPS) - 1)]

    type_filters: dict = {"category": {"option": category}}
    rarity = rarity_of(item)
    if classify(item) is ItemClass.UNIQUE:
        # A unique is identified by name; its base alone would match rares.
        type_filters["rarity"] = {"option": "unique"}
    elif rarity is not None:
        type_filters["rarity"] = {"option": "nonunique"}

    ilvl = int(item.get("ilvl") or 0)
    if ilvl:
        type_filters["ilvl"] = {"min": int(ilvl * scale)}

    equipment: dict = {}
    for prop_name, filter_id in DEFENCE_PROPERTIES.items():
        value = _property(item, prop_name)
        if value:
            equipment[filter_id] = {"min": int(value * scale)}

    and_filters: list[dict] = []
    or_groups: list[dict] = []

    all_mods = (
        list(item.get("explicitMods") or [])
        + list(item.get("fracturedMods") or [])
        + list(item.get("runeMods") or [])
    )

    # Notables ARE the value of a Megalomaniac, so they outrank every mod and
    # are trimmed last. They still take part in the ladder: an exact pair is
    # often unlisted, while "a Megalomaniac with this one notable" usually is,
    # and that single notable is what the item is really worth.
    NOTABLE_WEIGHT = 99
    scored: list[tuple[int, str, object]] = []
    for text in all_mods:
        if text.startswith("Allocates "):
            stat_id = notables.get(text[len("Allocates "):].strip())
            if stat_id:
                scored.append((NOTABLE_WEIGHT, text, _Notable(stat_id)))
            continue
        entry = match_mod(text, index)
        if entry is None:
            continue  # never guess a stat id
        if not parse_values(text):
            continue
        scored.append((entry.weight, text, entry))

    # Highest-weight mods first; those are the ones a buyer searches on.
    scored.sort(key=lambda t: -t[0])
    for _, text, entry in scored[:max_stats]:
        if isinstance(entry, _Notable):
            and_filters.append({"id": entry.stat_id, "value": {}})
            continue
        minimum = round(parse_values(text)[0] * scale, 2)
        if entry.ambiguous:
            or_groups.append({
                "type": "count",
                "value": {"min": 1},
                "filters": [
                    {"id": stat_id, "value": {"min": minimum}} for stat_id in entry.ids
                ],
            })
        else:
            and_filters.append({"id": entry.ids[0], "value": {"min": minimum}})

    query: dict = {
        "query": {
            "status": {"option": status},
            "filters": {"type_filters": {"filters": type_filters}},
            "stats": [{"type": "and", "filters": and_filters}, *or_groups],
        },
        "sort": {"price": "asc"},
    }
    if equipment:
        query["query"]["filters"]["equipment_filters"] = {"filters": equipment}
    if item.get("name") and classify(item) is ItemClass.UNIQUE:
        query["query"]["name"] = item["name"]
    return query


def query_hash(query: dict) -> str:
    return hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()[:16]
