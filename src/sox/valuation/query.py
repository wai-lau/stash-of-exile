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
import re

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

# Clipboard property name -> (equipment_filters id, regex matching the flat
# mods that feed it). Verified against /api/trade2/data/filters.
#
# Local defences are searched HERE, not as stats. The displayed total already
# includes every flat and percent modifier on the item, so the total is the
# honest measure of the item and needs no stat filter beside it.
#
# damage, aps, crit and rune_sockets are deliberately absent: a weapon's
# damage is dominated by its base and quality, so constraining it excludes
# comparable items for no gain.
# property -> (filter id, flat-mod pattern, percent-mod pattern)
DEFENCE_PROPERTIES = {
    "Energy Shield": ("es", re.compile(r"to maximum Energy Shield$", re.I),
                      re.compile(r"increased .*Energy Shield$", re.I)),
    "Armour": ("ar", re.compile(r"to Armour$", re.I),
               re.compile(r"increased Armour\b.*$", re.I)),
    "Evasion Rating": ("ev", re.compile(r"to Evasion Rating$", re.I),
                       re.compile(r"increased (Evasion Rating|.*and Evasion)$", re.I)),
    "Evasion": ("ev", re.compile(r"to Evasion Rating$", re.I),
                re.compile(r"increased (Evasion Rating|.*and Evasion)$", re.I)),
    "Runic Ward": ("ward", re.compile(r"to maximum Runic Ward$", re.I),
                   re.compile(r"increased maximum Runic Ward$", re.I)),
    "Spirit": ("spirit", re.compile(r"to Spirit$", re.I),
               re.compile(r"increased Spirit$", re.I)),
    "Block chance": ("block", re.compile(r"(?!)", re.I),
                     re.compile(r"increased Block chance$", re.I)),
}


def equipment_minimum(item: dict, property_name: str, flat, percent) -> int | None:
    """The item's total for this defence, normalised to its worst rolls.

    An item showing 485 Evasion whose "+145 to Evasion Rating" could have
    rolled as low as 117 is really a 457-Evasion item that got lucky. Asking
    for 485 would exclude the identical item with a worse roll, which is
    exactly a comparable.

    Flat mods subtract directly; percent mods are multiplicative on the base,
    so the base is recovered first and rebuilt at the minimum rolls:

        base      = total / (1 + pct_actual) - flat_actual
        minimum   = (base + flat_min) * (1 + pct_min)
    """
    total = _property(item, property_name)
    if total is None:
        return None

    ranges = item.get("modRanges") or {}
    flat_actual = flat_min = 0.0
    pct_actual = pct_min = 0.0
    for text, (actual, low, _high) in ranges.items():
        if flat.search(text):
            flat_actual += actual
            flat_min += low
        elif percent.search(text):
            pct_actual += actual
            pct_min += low

    base = total / (1 + pct_actual / 100) - flat_actual
    minimum = (base + flat_min) * (1 + pct_min / 100)
    return max(int(round(minimum)), 1)

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


# Categories where a defence/speed mod belongs to the item itself, so the
# LOCAL stat id applies. On jewellery and jewels the same text is global.
LOCAL_CATEGORY_PREFIXES = ("armour.", "weapon.")


def stat_ids_for(entry: ModEntry, category: str) -> tuple[str, ...]:
    """The ids to search, choosing the local twin where the item provides it.

    "+145 to Evasion Rating" reads identically on a helmet and an amulet, but
    they are different stats to the trade API. Searching the global id for a
    helmet mod matches nothing.
    """
    if entry.local_ids and category.startswith(LOCAL_CATEGORY_PREFIXES):
        return entry.local_ids
    return tuple(entry.ids)


# "Adds 121 to 183 Cold Damage" is filtered by the AVERAGE of the two
# numbers, verified live: min=121 returns 29 results, min=152 (the average)
# returns 8. Passing the low roll asks for items at least as good as the
# BOTTOM of the range, which is not the same question.
ADDED_RANGE = re.compile(r"^Adds # to #", re.I)


def filter_value(entry_text: str, values: list[float]) -> float:
    """The number the trade API compares this mod against."""
    if ADDED_RANGE.match(entry_text) and len(values) >= 2:
        return (values[0] + values[1]) / 2
    return values[0]


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
    status: str = "any",
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
    for prop_name, (filter_id, flat, percent) in DEFENCE_PROPERTIES.items():
        value = equipment_minimum(item, prop_name, flat, percent)
        if value:
            equipment[filter_id] = {"min": value}

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
    # A mod already covered by an equipment filter must not also be a stat
    # filter: the item's total includes it, so constraining both asks for it
    # twice. Spirit is the case that matters — local on a sceptre, where the
    # filter covers it, but global on an amulet, where it must stay a stat.
    covered = set(defence_mod_texts(item))

    scored: list[tuple[int, str, object]] = []
    for text in all_mods:
        if text in covered:
            continue
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
        minimum = round(filter_value(entry.text, parse_values(text)) * scale, 2)
        ids = stat_ids_for(entry, category)
        if len(ids) > 1:
            or_groups.append({
                "type": "count",
                "value": {"min": 1},
                "filters": [
                    {"id": stat_id, "value": {"min": minimum}} for stat_id in ids
                ],
            })
        else:
            and_filters.append({"id": ids[0], "value": {"min": minimum}})

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


def defence_mod_texts(item: dict) -> list[str]:
    """Item mods that feed an equipment filter rather than a stat filter.

    They are not in the allowlist and score nothing, but they ARE part of the
    search — through the item's displayed total — so the breakdown must not
    show them as ignored.
    """
    out = []
    for prop_name, (_id, flat, percent) in DEFENCE_PROPERTIES.items():
        if _property(item, prop_name) is None:
            continue
        for text in (
            list(item.get("explicitMods") or [])
            + list(item.get("fracturedMods") or [])
            + list(item.get("runeMods") or [])
        ):
            if (flat.search(text) or percent.search(text)) and text not in out:
                out.append(text)
    return out


def searched_item_texts(item: dict, index, notables, relax: int = 0) -> list[str]:
    """The item's OWN wording for the mods that went into the query.

    explain_selection returns the allowlist's canonical text ("# to
    Dexterity"); highlighting the breakdown needs the item's ("+31 to
    Dexterity").
    """
    from sox.valuation.mods import normalize_mod

    _, canonical = explain_selection(item, index, notables, relax=relax)
    wanted = {normalize_mod(text) for text in canonical}
    out = []
    for text in (
        list(item.get("explicitMods") or [])
        + list(item.get("fracturedMods") or [])
        + list(item.get("runeMods") or [])
    ):
        if normalize_mod(text) in wanted:
            out.append(text)
    return out
