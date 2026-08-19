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
from sox.valuation.mods import match_mod, select_synergistic
from sox.valuation.rolls import parse_values

# Widening ladder: how many cohering stats to keep at each rung.
#
# Minimums are NEVER lowered. Searching below your own values asks "what are
# worse items worth", which answers a different question and drags the price
# down. Widening instead drops the weakest mod — by the game's own tier where
# the item reports one — so every rung still describes an item at least as
# good as yours on the stats that remain.
RELAX_STEPS = (4, 3, 2, 1)

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
    max_stats = RELAX_STEPS[min(relax, len(RELAX_STEPS) - 1)]
    scale = 1.0  # minimums are never lowered; see RELAX_STEPS

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

    # Choose stats that SYNERGIZE, not merely the heaviest ones. Selecting by
    # weight alone can mix archetypes and describe a buyer who does not exist.
    notable_items = [(w, t, e) for w, t, e in scored if isinstance(e, _Notable)]
    mod_items = [(w, t, e) for w, t, e in scored if not isinstance(e, _Notable)]

    tiers = item.get("modTiers") or {}
    texts = {id(e): t for _, t, e in mod_items}
    chosen_entries, _group = select_synergistic(
        [e for _, _, e in mod_items],
        max(max_stats - len(notable_items), 0),
        tiers=tiers,
        texts=texts,
    )
    by_entry = {id(e): t for _, t, e in mod_items}
    selected = notable_items[:max_stats] + [
        (0, by_entry[id(e)], e) for e in chosen_entries
    ]

    for _, text, entry in selected[:max_stats]:
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


def explain_selection(
    item: dict,
    index: dict[str, ModEntry],
    notables: dict[str, str],
    relax: int = 0,
) -> tuple[str | None, list[str]]:
    """Which stats the query will search on, and the buyer group behind them.

    Surfaced in the output because this is the judgement the tool exists to
    make. A price-check overlay leaves it to the player, whose knowledge of
    which stats synergize comes from having played the archetype.
    """
    from sox.valuation.mods import matched, select_synergistic

    max_stats = RELAX_STEPS[min(relax, len(RELAX_STEPS) - 1)]

    all_mods = (
        list(item.get("explicitMods") or [])
        + list(item.get("fracturedMods") or [])
        + list(item.get("runeMods") or [])
    )
    notable_texts = [
        t for t in all_mods
        if t.startswith("Allocates ") and notables.get(t[len("Allocates "):].strip())
    ]
    if notable_texts:
        return "notable", notable_texts[:max_stats]

    plain = [t for t in all_mods if not t.startswith("Allocates ")]
    entries = matched(plain, index)
    texts = {}
    for text in plain:
        entry = match_mod(text, index)
        if entry is not None:
            texts.setdefault(id(entry), text)
    chosen, group = select_synergistic(
        entries, max_stats, tiers=item.get("modTiers") or {}, texts=texts
    )
    return (group or None), [e.text for e in chosen]
