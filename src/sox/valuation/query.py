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

from functools import lru_cache

from sox.valuation.allowlists import ModEntry, load_skills
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
    rune_texts = set(item.get("runeMods") or [])

    # Everything contributing to the displayed total, including runes...
    flat_actual = pct_actual = 0.0
    # ...but only the item's OWN mods are rebuilt at their floor rolls, so a
    # socketed rune's bonus is removed rather than searched for.
    flat_min = pct_min = 0.0
    for text, (actual, low, _high) in ranges.items():
        if flat.search(text):
            flat_actual += actual
            if text not in rune_texts:
                flat_min += low
        elif percent.search(text):
            pct_actual += actual
            if text not in rune_texts:
                pct_min += low
    for text in rune_texts:
        # A rune without a reported range still inflates the total.
        if text in ranges:
            continue
        values = parse_values(text)
        if not values:
            continue
        if flat.search(text):
            flat_actual += values[0]
        elif percent.search(text):
            pct_actual += values[0]

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


# Stats GGG aggregates for us. Searching the TOTAL is strictly better than
# searching one mod: an item carrying fire resistance on both a fire roll and
# an all-elemental roll satisfies a buyer looking for fire resistance, and a
# single-mod filter misses it. Measured on helmets: single-mod fire res >= 60
# returns 871 listings, the pseudo total returns 2,128.
#
# Each entry lists the mod patterns that feed the total. Values are taken at
# each mod's MINIMUM roll, for the same reason the equipment filters are —
# the identical item with worse rolls is still a comparable.
# (pseudo id, [(mod pattern, how many times it counts toward the total)])
#
# The ELEMENTAL total, not per-element totals. Demanding a distribution is far
# stricter than demanding the same sum: fire>=70 AND cold>=40 AND light>=40
# returns 0 listings while total elemental >= 150 returns 26. Buyers want
# total resistance, not particular elements. Chaos stays separate because it
# is a distinct need, not interchangeable with the elements.
#
# "+18% to all Elemental Resistances" adds 18 to each of the three, so it
# counts three times toward the elemental total.
PSEUDO_TOTALS = [
    ("pseudo.pseudo_total_life", [(re.compile(r"to maximum Life$", re.I), 1)]),
    ("pseudo.pseudo_total_mana", [(re.compile(r"to maximum Mana$", re.I), 1)]),
    ("pseudo.pseudo_total_elemental_resistance",
     [(re.compile(r"to (Fire|Cold|Lightning) Resistance$", re.I), 1),
      (re.compile(r"to all Elemental Resistances$", re.I), 3)]),
    ("pseudo.pseudo_total_chaos_resistance",
     [(re.compile(r"to Chaos Resistance$", re.I), 1)]),
    ("pseudo.pseudo_total_strength",
     [(re.compile(r"to Strength$", re.I), 1),
      (re.compile(r"to all Attributes$", re.I), 1)]),
    ("pseudo.pseudo_total_dexterity",
     [(re.compile(r"to Dexterity$", re.I), 1),
      (re.compile(r"to all Attributes$", re.I), 1)]),
    ("pseudo.pseudo_total_intelligence",
     [(re.compile(r"to Intelligence$", re.I), 1),
      (re.compile(r"to all Attributes$", re.I), 1)]),
]


def pseudo_totals(item: dict, index=None) -> list[tuple[str, int, list[str]]]:
    """(pseudo id, floor total, contributing mod texts), most important first.

    Ranked by the heaviest contributing mod, then by how many mods feed the
    total — a total assembled from two mods describes the item better than
    one scraped from a single weak roll.
    """
    ranges = item.get("modRanges") or {}
    # Every source the ITEM owns counts toward a total — explicit, desecrated,
    # fractured, the base's implicit. Runes do not: their bonus leaves with the
    # rune.
    mods = searchable_mods(item) + list(item.get("implicitMods") or [])

    out = []
    for pseudo_id, patterns in PSEUDO_TOTALS:
        total = 0.0
        used: list[str] = []
        for text in mods:
            multiplier = next((m for p, m in patterns if p.search(text)), 0)
            if not multiplier:
                continue
            values = parse_values(text)
            if not values:
                continue
            # Floor roll where the item reports one, else the value shown.
            floor = ranges.get(text, (values[0], values[0], values[0]))[1]
            total += floor * multiplier
            used.append(text)
        if total > 0:
            out.append((pseudo_id, int(round(total)), used))

    def rank(entry):
        _pid, _value, texts = entry
        weights = [0]
        if index is not None:
            from sox.valuation.mods import match_mod
            weights += [e.weight for t in texts
                        if (e := match_mod(t, index)) is not None]
        return (-max(weights), -len(texts))

    out.sort(key=rank)
    return out


# The item's own defence type is itself an archetype. A recharge-rate mod on
# an Energy Shield chest serves the same buyer the base does; the same mod on
# a ring serves nobody in particular. Seeding the coherence count with what
# the item IS lets its defensive mods cluster with it.
DEFENCE_TAG_BY_PROPERTY = {
    "Energy Shield": "es",
    "Armour": "armour",
    "Evasion Rating": "evasion",
    "Evasion": "evasion",
}


def defence_seed(item: dict) -> dict[str, int]:
    """One coherence vote per defence the item actually provides."""
    seed: dict[str, int] = {}
    for prop_name, tag in DEFENCE_TAG_BY_PROPERTY.items():
        if _property(item, prop_name):
            seed[tag] = 1
    # Deliberately NOT seeding the umbrella "defence" tag. Nearly every
    # defensive mod carries it, so seeding it lets the catch-all outvote the
    # specific archetype and report "defence" for an item that is plainly an
    # armour item.
    return seed


def searchable_mods(item: dict) -> list[str]:
    """Every mod that can go into a query, from every source.

    Rune mods are excluded: a socketed rune belongs to the socket, not the
    item. The buyer sockets their own, and the rune has its own price — so
    searching on its bonus prices an item the seller is not selling.
    """
    return (
        list(item.get("explicitMods") or [])
        + list(item.get("fracturedMods") or [])
        + list(item.get("desecratedMods") or [])
        + list(item.get("enchantMods") or [])
    )


# Fallback when the Item Class is unrecognised. The endgame item classes are
# many and irregular — "Inscribed Ultimatum" is its own Item Class — but the
# base name carries the family reliably.
BASE_NAME_CATEGORIES = (
    ("waystone", "map.waystone"),
    ("tablet", "map.tablet"),
    ("barya", "map.barya"),
    ("ultimatum", "map.ultimatum"),
    ("breachstone", "map.breachstone"),
    ("logbook", "map.logbook"),
    ("fragment", "map.fragment"),
    ("simulacrum", "map.fragment"),
    ("relic", "sanctum.relic"),
    ("charm", "flask.charm"),
    ("jewel", "jewel"),
)


def category_for(item: dict) -> str | None:
    by_class = ITEM_CLASS_CATEGORIES.get((item.get("itemClass") or "").casefold())
    if by_class:
        return by_class
    haystack = f"{item.get('itemClass') or ''} {item.get('baseType') or ''}".casefold()
    for needle, category in BASE_NAME_CATEGORIES:
        if needle in haystack:
            return category
    return None


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


# `Grants Skill: Level 20 Chaos Bolt`. The level is a rolled value and a real
# search axis — a buyer filtering for the skill will not take a lower level of
# it — so it is always searched at our level as the minimum.
GRANTED_SKILL = re.compile(r"^Level (\d+) (.+)$")


@lru_cache(maxsize=1)
def _skill_ids() -> dict[str, str]:
    return {_fold(name): stat_id for name, stat_id in load_skills().items()}


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def granted_skill_filter(item: dict) -> dict | None:
    """The item's granted skill, floored at the level it actually grants.

    Returns None for an unlevelled grant like a shield's `Grants Skill: Raise
    Shield`: that is intrinsic to every shield base, so it constrains nothing
    and carries no level to floor.
    """
    for prop in item.get("properties") or []:
        if prop.get("name") != "Grants Skill":
            continue
        values = prop.get("values") or []
        if not values or not values[0]:
            return None
        match = GRANTED_SKILL.match(str(values[0][0]).strip())
        if not match:
            return None
        stat_id = _skill_ids().get(_fold(match.group(2)))
        if stat_id is None:
            return None
        return {"id": stat_id, "value": {"min": int(match.group(1))}}
    return None


def granted_skill_text(item: dict) -> list[str]:
    """The granted-skill line as the item words it, when it is searched."""
    if granted_skill_filter(item) is None:
        return []
    for prop in item.get("properties") or []:
        if prop.get("name") == "Grants Skill":
            return [f"Grants Skill: {str(prop['values'][0][0]).strip()}"]
    return []


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

    all_mods = searchable_mods(item)

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

    # Stats with a pseudo total are searched as that total instead, so their
    # mods must not also appear individually.
    # Pseudo totals obey the same rule as mods: only what coheres. An
    # Intelligence total on an elemental weapon constrains the search to items
    # carrying Intelligence, which most comparables do not — that alone moved
    # a 3ex quarterstaff to a 50ex median.
    from sox.valuation.mods import coherence_keys, dominant_archetype, matched

    dominant, _count = dominant_archetype(matched(all_mods, index),
                                          seed=defence_seed(item))
    totals = []
    for pseudo_id, value, used in pseudo_totals(item, index):
        if dominant is not None:
            entries = [e for t in used if (e := match_mod(t, index)) is not None]
            if not any(dominant in coherence_keys(e) for e in entries):
                continue
        totals.append((pseudo_id, value, used))
    for _pseudo_id, _value, used in totals:
        covered.update(used)

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

    chosen_entries, _group = select_synergistic(
        [e for _, _, e in mod_items], max_stats,
        tiers=item.get("modTiers") or {},
        texts={id(e): t for _, t, e in mod_items},
        rolls=item.get("modRanges") or {},
        seed=defence_seed(item),
    )
    by_entry = {id(e): t for _, t, e in mod_items}

    # Three sources of stat filters, in the order they survive widening:
    # notables identify the item outright, pseudo totals describe it more
    # faithfully than any single mod, and individual mods come last.
    notable_filters = [{"id": e.stat_id, "value": {}} for _, _, e in notable_items]

    pseudo_filters = [{"id": pid, "value": {"min": value}}
                      for pid, value, _ in totals]

    mod_filters = []
    for entry in chosen_entries:
        text = by_entry[id(entry)]
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
            mod_filters.append({"id": ids[0], "value": {"min": minimum}})

    # The granted skill is exempt from the widening cap. Dropping it would not
    # widen the search, it would change what is being searched for: a Level 20
    # Chaos Bolt wand priced without the Chaos Bolt is priced as a bare wand.
    skill_filters = [f for f in (granted_skill_filter(item),) if f]
    and_filters = skill_filters + (notable_filters + pseudo_filters + mod_filters)[:max_stats]

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

    # A corrupted or sanctified listing is not "at least as good" as an
    # untouched copy — corruption closes off every further craft, and a
    # sanctified copy carries a bonus ours does not. Leaving them in the
    # results lets one drag the cheapest match below our item's real floor.
    #
    # Pinned only when ours is neither. Once the item has been touched at all
    # the whole market is comparable again, so both stay unconstrained rather
    # than pinning the one flag we happen not to carry.
    if not item.get("corrupted") and not item.get("sanctified"):
        query["query"]["filters"]["misc_filters"] = {"filters": {
            "corrupted": {"option": "false"},
            "sanctified": {"option": "false"},
        }}
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

    all_mods = searchable_mods(item)
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
        entries, max_stats, tiers=item.get("modTiers") or {}, texts=texts,
        rolls=item.get("modRanges") or {}, seed=defence_seed(item),
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
        for text in searchable_mods(item):
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
    for text in searchable_mods(item):
        if normalize_mod(text) in wanted:
            out.append(text)
    return out
