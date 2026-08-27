"""Build a trade2 query.

The search is NOT "find my item". It is "find the cheapest item at least as
good as mine", so every constraint is a minimum and there are no maximums —
each mod's minimum sitting at the floor of its roll's own range, because a
roll one point under ours is the same good at the same tier. Every listing
returned is therefore at least our item's tier on every constrained axis,
which makes the cheapest one a CEILING on our ask rather than a comparable
sale.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace

from functools import lru_cache

from sox.valuation.allowlists import (
    ModEntry,
    load_base_types,
    load_flags,
    load_skills,
)
from sox.valuation.classify import ItemClass, Rarity, classify, rarity_of
from sox.valuation.mods import (
    coherence_keys,
    match_mod,
    select_synergistic,
    survival_class,
)
from sox.valuation.rolls import parse_values

# Widening ladder: how many cohering stats to keep at each rung.
#
# Minimums are NEVER lowered below the roll's own tier. Searching under the
# tier asks "what are worse items worth", which answers a different question
# and drags the price down. Widening instead drops the weakest mod — by the
# game's own tier where the item reports one — so every rung still describes
# an item of at least your item's tiers on the stats that remain.
#
# The last rung keeps NO mods. What is left is still a real search — category,
# rarity, item level, requirements, and the totals in the equipment filters —
# and on a weapon those totals are most of the item: a mace priced on DPS and
# requirements alone is exactly how you would search for one by hand. Without
# this rung a weapon whose exact mods nobody else rolled came back unpriced
# while comparable maces were listed at 10 divine.
#
# No item carries this many searchable stats, so the first rung's cap is never
# reached: it asks for the WHOLE item.
ALL_STATS = 99

# The ladder starts from the whole item and only then begins trimming. The
# first search is the only one that describes the item exactly, and when it
# does return a firm sample it is the truest ceiling there is — a five-mod
# rare priced on its best four was priced as a different, lesser item while
# the real one was listed. Starting wide costs nothing when it is too narrow:
# a rung with too few matches is kept as a fallback and the ladder widens, so
# the exact search is at worst one extra API call and at best the answer.
RELAX_STEPS = (ALL_STATS, 4, 3, 2, 1, 0)

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
# The percent patterns must catch hybrid mods wherever the defence sits in the
# list. "increased Evasion and Energy Shield" ends on Energy Shield, so an
# Evasion pattern anchored to the end of the line missed it entirely — that
# mod adjusted the ES total and left Evasion untouched on the same item.
# The lookaheads keep the recharge and armour-break stats out, which is what
# the end-anchor used to do.
DEFENCE_PROPERTIES = {
    "Energy Shield": ("es", re.compile(r"to maximum Energy Shield$", re.I),
                      re.compile(r"increased\b.*\bEnergy Shield\b"
                                 r"(?!\s*(Recharge|Regeneration))", re.I)),
    "Armour": ("ar", re.compile(r"to Armour$", re.I),
               re.compile(r"increased\b.*\bArmour\b(?!\s*Break)", re.I)),
    "Evasion Rating": ("ev", re.compile(r"to Evasion Rating$", re.I),
                       re.compile(r"increased\b.*\bEvasion\b", re.I)),
    "Evasion": ("ev", re.compile(r"to Evasion Rating$", re.I),
                re.compile(r"increased\b.*\bEvasion\b", re.I)),
    "Runic Ward": ("ward", re.compile(r"to maximum Runic Ward$", re.I),
                   re.compile(r"increased maximum Runic Ward$", re.I)),
    "Spirit": ("spirit", re.compile(r"to Spirit$", re.I),
               re.compile(r"increased Spirit$", re.I)),
    "Block chance": ("block", re.compile(r"(?!)", re.I),
                     re.compile(r"increased Block chance$", re.I)),
}


# The trade filters do not compare the number an item shows. Every defence in
# a listing's `extended` block — the field ar/es/ev actually read — is
# normalised to 20% quality: a 0-quality boot showing 78 Armour is filed at
# 94, and one at 15% quality showing 95 is filed at 99.
#
#     filed = shown * 1.2 / (1 + quality/100)
#
# Our floor came off the clipboard un-normalised. A search for "at least my 94
# Armour" therefore asked for items FILED at 94 — a fifth weaker than ours —
# and the rune check then threw every one of them out for a reason that had
# nothing to do with runes: of 30 listings dropped as rune-inflated on one
# pair of boots, 29 carried no rune at all.
QUALITY_BASELINE = 20

# Only the three, and it was measured rather than assumed. `extended` reports
# ar, es and ev and nothing else: a sceptre's block carries no derived value
# at all, and `spirit` pinned to exactly 100 returns sceptres SHOWING 100 at
# +10%, +11%, +12%, +13% and +14% quality alike — a filed filter would have
# excluded every one of them. Spirit, Runic Ward and Block chance are compared
# at face value.
QUALITY_SCALED = ("ar", "es", "ev")


def quality_percent(item: dict) -> int:
    """The item's quality, from the clipboard or from a listing payload."""
    for prop in item.get("properties") or []:
        if clean_markup(prop.get("name", "")) != "Quality":
            continue
        values = prop.get("values") or []
        if values and values[0]:
            try:
                return int(str(values[0][0]).split()[0].strip("+").rstrip("%"))
            except (ValueError, IndexError):
                return 0
    return 0


def filed_at_baseline_quality(value: float, item: dict) -> float:
    """A total as the trade filters file it: normalised to 20% quality."""
    return value * (1 + QUALITY_BASELINE / 100) / (1 + quality_percent(item) / 100)


def at_baseline_quality(value: float, item: dict, property_name: str) -> float:
    """The same, for the defences the filters are known to normalise."""
    filter_id = (DEFENCE_PROPERTIES.get(property_name) or (None,))[0]
    if filter_id not in QUALITY_SCALED:
        return value
    return filed_at_baseline_quality(value, item)


def equipment_minimum(item: dict, property_name: str, flat, percent) -> int | None:
    """The item's total for this defence, normalised to its worst rolls.

    An item showing 485 Evasion whose "+145 to Evasion Rating" could have
    rolled as low as 117 is really a 457-Evasion item that got lucky. Asking
    for 485 would exclude the identical item with a worse roll, which is
    exactly a comparable.

    NOT for uniques. Every copy carries the same mods, so the roll is the only
    thing separating them and is precisely what is being priced. Rebuilding a
    Forgotten Warden's (200-300)% hybrid at 200% turned 414 Energy Shield into
    312 and 1355 Evasion into 1021 — a search for the worst copy of the item
    in hand.

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
    # ...but only the item's OWN mods are kept, so a socketed rune's bonus is
    # removed rather than searched for.
    flat_min = pct_min = 0.0
    keep_rolls = classify(item) is ItemClass.UNIQUE
    for text, (actual, low, _high) in ranges.items():
        kept = actual if keep_rolls else low
        if flat.search(text):
            flat_actual += actual
            if text not in rune_texts:
                flat_min += kept
        elif percent.search(text):
            pct_actual += actual
            if text not in rune_texts:
                pct_min += kept
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
    return max(int(round(at_baseline_quality(minimum, item, property_name))), 1)

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
    # A talisman is filed under WEAPONS, not accessories. An unmapped class
    # cannot be searched at all: a rare Alpha Talisman scoring 10 came back
    # unpriced, and the row above it said only "Talismans" with no category
    # after it, which is the whole tell.
    "talismans": "weapon.talisman",
    # The rest of the melee families the trade filters carry. Named from the
    # convention the maces above already set: no PoE2 sword or axe has been
    # seen on the clipboard here, and a key that never matches costs nothing,
    # while a missing one costs the whole price.
    "one hand swords": "weapon.onesword", "two hand swords": "weapon.twosword",
    "one hand axes": "weapon.oneaxe", "two hand axes": "weapon.twoaxe",
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


class _Flag:
    """A unique's value-less mod: an exact id, and no minimum to ask for."""

    __slots__ = ("stat_id",)

    def __init__(self, stat_id: str) -> None:
        self.stat_id = stat_id


# Categories where a defence/speed mod belongs to the item itself, so the
# LOCAL stat id applies. On jewellery and jewels the same text is global.
LOCAL_CATEGORY_PREFIXES = ("armour.", "weapon.")


# Listing payloads wrap every game term in markup: "[Evasion|Evasion Rating]"
# renders as "Evasion Rating", "[Quality]" as "Quality".
_MARKUP_ALIAS = re.compile(r"\[[^\]|]*\|([^\]]*)\]")
_MARKUP_PLAIN = re.compile(r"\[([^\]]*)\]")


def clean_markup(text: str) -> str:
    return _MARKUP_PLAIN.sub(r"\1", _MARKUP_ALIAS.sub(r"\1", text or ""))


def _listing_texts(item: dict, key: str) -> list[str]:
    """Mod descriptions under `key`, with their markup stripped.

    Each carries its ACTUAL roll inline — "292% increased Evasion and Energy
    Shield" — which is what a defence total has to be unwound with.
    """
    out = []
    for mod in item.get(key) or []:
        text = mod.get("description") if isinstance(mod, dict) else mod
        if text:
            out.append(clean_markup(text))
    return out


def rune_free_defence(item: dict, property_name: str, flat, percent) -> float | None:
    """A listed item's defence total with its socketed runes removed.

    A listing can clear our floor purely on runes the buyer supplies
    themselves, and it is then not a comparable at all — it is a worse item
    wearing our defences. Live, the cheapest match for a 1294-Evasion
    Forgotten Warden showed 1376 and was 1260 once its rune came off.

        total = (base + flat_own + flat_rune) * (1 + pct_own + pct_rune)
        want  = (total / (1 + pct_own + pct_rune) - flat_rune) * (1 + pct_own)
    """
    total = None
    for prop in item.get("properties") or []:
        if clean_markup(prop.get("name", "")) != property_name:
            continue
        values = prop.get("values") or []
        if values and values[0]:
            try:
                total = float(str(values[0][0]).split()[0].rstrip("%"))
            except (ValueError, IndexError):
                return None
    if total is None:
        return None

    rune_texts = _listing_texts(item, "runeMods")
    own_texts = sum((_listing_texts(item, k) for k in
                     ("explicitMods", "implicitMods", "enchantMods")), [])

    def totals(texts):
        flat_sum = pct_sum = 0.0
        for text in texts:
            values = parse_values(text)
            if not values:
                continue
            if flat.search(text):
                flat_sum += values[0]
            elif percent.search(text):
                pct_sum += values[0]
        return flat_sum, pct_sum

    flat_rune, pct_rune = totals(rune_texts)
    _flat_own, pct_own = totals(own_texts)

    base = total / (1 + (pct_own + pct_rune) / 100) - flat_rune
    # Filed as the filter files it, so the two numbers being compared are the
    # same measurement of the same thing.
    return at_baseline_quality(max(base * (1 + pct_own / 100), 0.0), item,
                               property_name)


def meets_without_runes(item: dict, required: dict) -> bool:
    """Whether a listing still clears every floor without its runes.

    Covers DPS as well as the defences: a weapon reaching our damage only
    because of a socketed rune is a worse weapon wearing our numbers.
    """
    # Nothing to take off, and the search already applied every floor: an
    # item with no runes cannot be rune-inflated. Recomputing it here only
    # created a second chance to disagree with the API, and it took it.
    if not item.get("runeMods"):
        return True

    by_id = {fid: (name, flat, pct)
             for name, (fid, flat, pct) in DEFENCE_PROPERTIES.items()}
    for filter_id, bound in (required or {}).items():
        minimum = (bound or {}).get("min")
        if minimum is None:
            continue
        if filter_id == "dps":
            actual = rune_free_dps(item)
        else:
            spec = by_id.get(filter_id)
            if spec is None:
                continue
            actual = rune_free_defence(item, spec[0], spec[1], spec[2])
        if actual is not None and actual < minimum:
            return False
    return True


def regroup(ids: tuple[str, ...], group: str) -> tuple[str, ...]:
    """The same numeric stats, read from another group's table.

    Group and stat id are independent: `explicit.stat_1315743832` and
    `enchant.stat_1315743832` are the same stat sourced two ways, and asking
    the wrong one returns nothing rather than erroring. The allowlist resolves
    every id from the explicit table, so a mod that reached the item by
    another route has to be re-pointed.
    """
    return tuple(f"{group}.{i.split('.', 1)[-1]}" for i in ids)


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
    # rune. searchable_mods carries the implicits now; adding them again here
    # counted the base's implicit twice.
    mods = searchable_mods(item)

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

    Implicits ARE included. They occupy no affix slot, so they do not count
    against the room left to craft, but they are real stats on the item and a
    buyer filters on them like any other — 40 of the allowlisted mods can be
    rolled as an implicit.
    """
    return (
        list(item.get("explicitMods") or [])
        + list(item.get("fracturedMods") or [])
        + list(item.get("desecratedMods") or [])
        + list(item.get("enchantMods") or [])
        + list(item.get("implicitMods") or [])
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


# Weapons state each damage type as a range and a rate: "Physical Damage:
# 74-231", "Lightning Damage: 10-273 (lightning)", "Attacks per Second: 1.89".
# DPS is the axis a weapon is actually shopped on — a buyer compares numbers
# the tooltip already worked out, not the mods behind them.
DAMAGE_PROPERTIES = {
    "physical": ("Physical Damage",),
    "elemental": ("Fire Damage", "Cold Damage", "Lightning Damage"),
    "chaos": ("Chaos Damage",),
}
DAMAGE_RANGE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*-\s*(\d[\d,]*(?:\.\d+)?)")
# Weapon-local damage mods, which the tooltip has already folded into the
# damage ranges the DPS filters are computed from. "to Attacks" is the global
# form worn on a ring or gloves and is NOT local, so it stays a stat filter.
LOCAL_DAMAGE_MODS = (
    re.compile(r"^adds \d[\d.]* to \d[\d.]* "
               r"(physical|fire|cold|lightning|chaos) damage$", re.I),
    re.compile(r"^\d[\d.]*% increased physical damage$", re.I),
)


def _property_texts(item: dict, name: str) -> list[str]:
    """Every value under a property, matched through its markup.

    Both halves cost real damage. A listing names the line
    "[Physical] Damage", so an exact-name lookup found no physical damage on
    ANY listing and computed a mace's dps from its elemental alone — 124.7
    against the 216.78 the API had already worked out.

    And a weapon carrying two elemental types stops printing them by name:
    both collapse into one "Elemental Damage" line holding a range apiece,
    "9-13, 6-86", which read at its first value alone loses the rest. The two
    of them are 88.35 edps, and the first is 17.
    """
    for prop in item.get("properties") or []:
        if clean_markup(prop.get("name", "")) == name:
            return [str(value[0]) for value in (prop.get("values") or []) if value]
    return []


def _property_text(item: dict, name: str) -> str | None:
    values = _property_texts(item, name)
    return values[0] if values else None


def _average_range(text: str | None) -> float:
    """The midpoint of "74-231", summed over every range on the line.

    The clipboard writes a weapon's two elemental types as one line —
    "Elemental Damage: 43-56, 2-27" — so reading the first range alone lost
    the second: 49.5 of the 64 the weapon actually adds.
    """
    if not text:
        return 0.0
    return sum((float(low.replace(",", "")) + float(high.replace(",", ""))) / 2
               for low, high in DAMAGE_RANGE.findall(text))


# Local damage mods: the tooltip has already folded these into the damage
# ranges DPS is computed from, so a rune's share must come back out the same
# way it went in. NOTHING matches the never-regex; elemental and chaos runes
# add flat damage and no weapon-local percent exists for them.
NEVER = re.compile(r"(?!)")
_FLAT = r"^adds \d[\d.,]* to \d[\d.,]* {} damage$"
PHYSICAL_DAMAGE = ("Physical Damage",)
# One elemental group, not three. A weapon with a single elemental type names
# it — "Fire Damage: 66-106" — and one with two stops: they become an
# "Elemental Damage" line carrying a range each. Reading only the named lines
# lost every multi-element weapon's elemental damage entirely, which is how
# exalting a lightning roll onto a fire mace DROPPED its dps floor and moved
# the search from 2,659 listings to 5,083 cheaper ones.
#
# The three share a flat pattern and have no weapon-local percent between
# them, so merging them changes nothing else. A weapon never prints both a
# named line and the combined one.
ELEMENTAL_DAMAGE = ("Fire Damage", "Cold Damage", "Lightning Damage",
                    "Elemental Damage")
RUNE_DAMAGE_TYPES = (
    (PHYSICAL_DAMAGE, re.compile(_FLAT.format("physical"), re.I),
     re.compile(r"^\d[\d.]*% increased physical damage$", re.I)),
    (ELEMENTAL_DAMAGE,
     re.compile(_FLAT.format("(?:fire|cold|lightning)"), re.I), NEVER),
    (("Chaos Damage",), re.compile(_FLAT.format("chaos"), re.I), NEVER),
)
ATTACK_SPEED_PCT = re.compile(r"^\d[\d.]*% increased attack speed$", re.I)


def _own_and_rune_totals(item: dict, flat: re.Pattern, percent: re.Pattern):
    """(flat_rune, pct_rune, pct_own) for one local damage axis.

    Flat rune adds are averaged, because the tooltip range they inflated is
    itself read as an average. The item's own flat adds are not needed: they
    stay, so they can sit inside the recovered base untouched.
    """
    def totals(texts):
        flat_sum = pct_sum = 0.0
        for text in texts:
            values = parse_values(text)
            if not values:
                continue
            if flat.search(text):
                flat_sum += (values[0] + values[1]) / 2 if len(values) > 1 else values[0]
            elif percent.search(text):
                pct_sum += values[0]
        return flat_sum, pct_sum

    flat_rune, pct_rune = totals(_listing_texts(item, "runeMods"))
    own = sum((_listing_texts(item, k)
               for k in ("explicitMods", "implicitMods", "enchantMods")), [])
    _flat_own, pct_own = totals(own)
    return flat_rune, pct_rune, pct_own


def _strip_runes(shown: float, flat_rune: float, pct_rune: float, pct_own: float) -> float:
    """The same algebra rune_free_defence uses, on a damage average.

        shown = (base + flat_own + flat_rune) * (1 + pct_own + pct_rune)
        want  = (shown / (1 + pct_own + pct_rune) - flat_rune) * (1 + pct_own)
    """
    if shown <= 0:
        return 0.0
    base = shown / (1 + (pct_own + pct_rune) / 100) - flat_rune
    return max(base * (1 + pct_own / 100), 0.0)


def rune_free_aps(item: dict) -> float:
    """Attacks per Second with any rune-granted attack speed removed."""
    shown = _average_range(_property_text(item, "Attacks per Second"))
    if not shown:
        try:
            shown = float((_property_text(item, "Attacks per Second") or "0").replace(",", ""))
        except ValueError:
            return 0.0
    _flat, pct_rune, pct_own = _own_and_rune_totals(item, NEVER, ATTACK_SPEED_PCT)
    return _strip_runes(shown, 0.0, pct_rune, pct_own)


def rune_free_dps(item: dict) -> float | None:
    """Total DPS with the socketed runes taken back out.

    Works on our own item and on a fetched listing alike: both carry the
    damage as tooltip ranges and the runes as `runeMods`.
    """
    aps = rune_free_aps(item)
    if not aps:
        return None
    combined = 0.0
    for names, flat, percent in RUNE_DAMAGE_TYPES:
        shown = sum(_average_range(text)
                    for n in names for text in _property_texts(item, n))
        if shown <= 0:
            continue
        free = _strip_runes(shown, *_own_and_rune_totals(item, flat, percent))
        # Quality raises physical damage and nothing else, and the dps the
        # filter compares against is filed at 20% of it. Measured on four
        # listed maces: a +17% mace showing 112-160 at 1.10 aps is filed at
        # 153.45 pdps, which is the average rebuilt at 20% quality — while
        # its 1-7 lightning is filed at 4.4, exactly as shown.
        if names == PHYSICAL_DAMAGE:
            free = filed_at_baseline_quality(free, item)
        combined += free
    if combined <= 0:
        return None
    return combined * aps


def damage_filters(item: dict) -> dict[str, dict]:
    """Total DPS, with the socketed runes taken back out.

    Runes come off here for the same reason they come off the defences: the
    buyer sockets their own, and the rune has its own price. Both sides are
    stripped — the floor is built rune-free, and `meets_without_runes` then
    recomputes each listing rune-free and discards the ones that only cleared
    it while wearing a rune.

    Stripping only ONE side is what breaks. A rune-free floor against
    rune-inclusive listings admits every weapon between our real DPS and our
    stripped one: on one mace that gap was 448 against 482 and the cheapest
    match fell from 1 divine to 29 exalted, a weaker weapon that qualified
    only because the floor had dropped. That is an argument for stripping the
    listings too, which is now done, not for leaving our own runes in.

    Total DPS only, not pdps and edps alongside it. Splitting it pins the
    SOURCE of the damage: a weapon reaching the same DPS through fire instead
    of cold is a comparable, and constraining each component excludes it.
    Measured live on one mace — 65 matches with all three filters, 995 with
    DPS alone.

    Attacks per Second and Critical Chance stay unfiltered. Both are traded
    off against damage rather than added to it — a slower, harder-hitting
    weapon is not a worse one — so a minimum on either excludes comparables
    rather than weak items.
    """
    dps = rune_free_dps(item)
    return {} if dps is None else {"dps": {"min": round(dps, 1)}}


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


# Clipboard property name -> map_filters id. Verified against
# /api/trade2/data/filters -> map_filters ("Endgame Filters").
#
# A waystone is bought on the totals at the top of the item, not on the mods
# that produced them — and of those totals, ONLY the ones a buyer pays for.
# Measured live on securable T16 rares, one stat at a time: item rarity 57+
# alone moved the floor from 95 ex to 280, while drop chance 95+ and monster
# rarity 41+ left it standing. High rolls of those are table stakes on any
# listed T16, so constraining them shrinks the comparables without describing
# anything a buyer shops by — the same reason an off-archetype total goes to
# the back of the ladder. (Measured against SECURABLE listings; at status
# "any" the offline ghosts put every one of these floors at 1 ex and the
# differences vanish into the junk.)
WAYSTONE_PROPERTIES = {
    "Item Rarity": "map_iir",
}

# The tier lives in the base name — "Waystone (Tier 14)" — not in the
# property block.
_WAYSTONE_TIER = re.compile(r"\(Tier (\d+)\)")


def waystone_filters(item: dict, category: str) -> dict:
    """The endgame totals the item is bought on, as trade minimums.

    Like a defence total, these are the honest measure of the item — the
    displayed number already includes every mod that feeds it — so they sit
    beside the stat ladder rather than in it and survive every rung. The tier
    is a floor like everything else: a higher tier is at least as good, and
    the search has no maximums. Zero constrains nothing and is not sent.
    """
    if not category.startswith("map."):
        return {}
    out: dict = {}
    tier = _WAYSTONE_TIER.search(item.get("baseType") or "")
    if tier:
        out["map_tier"] = {"min": int(tier.group(1))}
    for prop_name, filter_id in WAYSTONE_PROPERTIES.items():
        value = _property(item, prop_name)
        if value is not None and value > 0:
            out[filter_id] = {"min": value}
    return out


# How the report words each filter. The tier is a count and carries no unit.
_MAP_FILTER_TEXT = {
    "map_tier": ("tier", ""),
    "map_iir": ("item rarity", "%"),
}


def waystone_stat_texts(item: dict, category: str) -> list[str]:
    """The endgame minimums as the report words them.

    Read off the same filters the query sends, so the two cannot drift —
    every mod on a waystone scores +0, and without this the market row reads
    as resting on nothing at all.
    """
    return [
        f"{_MAP_FILTER_TEXT[fid][0]} {value['min']}{_MAP_FILTER_TEXT[fid][1]}+"
        for fid, value in waystone_filters(item, category).items()
    ]


# `Grants Skill: Level 20 Chaos Bolt`. The level is a rolled value and a real
# search axis — a buyer filtering for the skill will not take a lower level of
# it — so it is always searched at our level as the minimum.
GRANTED_SKILL = re.compile(r"^Level (\d+) (.+)$")


@lru_cache(maxsize=1)
def _skill_ids() -> dict[str, tuple[str, ...]]:
    return {_fold(name): tuple(ids) for name, ids in load_skills().items()}


@lru_cache(maxsize=1)
def _base_types() -> tuple[str, ...]:
    return tuple(load_base_types())


def base_type(item: dict) -> str | None:
    """The base a normal, magic or unique item is bought as.

    The clipboard does not always give it cleanly. A magic item wraps its
    base in affixes and a normal one with quality prefixes "Superior", so the
    longest known base the line contains is the base:

        Crackling Temple Maul of the Brute   ->  Temple Maul
        Superior Divine Crown                ->  Divine Crown

    None when nothing matches, and then the search stays on its category
    rather than pinning a base that was guessed at.
    """
    line = " ".join((item.get("baseType") or item.get("typeLine") or "").split())
    if not line:
        return None
    padded = f" {line} "
    for base in _base_types():
        if f" {base} " in padded:
            return base
    return None


@lru_cache(maxsize=1)
def _flag_ids() -> dict[str, dict[str, str]]:
    from sox.valuation.mods import normalize_mod

    return {group: {normalize_mod(text): stat_id for text, stat_id in entries.items()}
            for group, entries in load_flags().items()}


def flag_stat_id(item: dict, text: str) -> str | None:
    """The stat id for a mod that carries no number, or None.

    A flag has no roll to compare, so the ordinary path — match the
    allowlist, read the minimum off the item — has nothing to work with and
    drops it. That is right for gear, where a mod is worth something or it is
    not; it is wrong for a unique, where the flag is not a value on the item
    but the identity of it. Six Mastered Domain tablets share a name, a base
    and an index price of 1 exalted, and the Forest one sells for 55.

    An IMPLICIT flag is the identity of the BASE, so it searches at any
    rarity: a rare's search is deliberately not base-pinned, and "Grants 1
    additional Skill Slot" is what keeps an Unset Ring's comparables Unset
    Rings. The number in that wording is fixed text, not a roll, so the
    flag table is consulted before the value gate rather than behind it.
    """
    from sox.valuation.mods import normalize_mod

    if text in (item.get("implicitMods") or []):
        return _flag_ids()["implicit"].get(normalize_mod(text))
    if rarity_of(item) is not Rarity.UNIQUE or parse_values(text):
        return None
    return _flag_ids()["explicit"].get(normalize_mod(text))


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
        ids = _skill_ids().get(_fold(match.group(2)))
        if not ids:
            return None
        level = int(match.group(1))
        if len(ids) == 1:
            return {"id": ids[0], "value": {"min": level}}
        # Two ids for one name, and the item text cannot say which. Corpsewade
        # Iron Greaves grants the TRIGGERED Decompose, so asking for
        # skill.corpse_cloud alone matched 0 of the 1,806 listings that exist
        # and the boots were reported as having no comparable listing at all.
        return {
            "type": "count",
            "value": {"min": 1},
            "filters": [{"id": stat_id, "value": {"min": level}} for stat_id in ids],
        }
    return None


def ordered_pseudo_totals(
    item: dict, index: dict[str, ModEntry]
) -> tuple[list[tuple[str, int, list[str]]], list[tuple[str, int, list[str]]]]:
    """Every pseudo total, split into the ones that cohere and the rest.

    A stat GGG totals for us is searched as that total, always: two filters
    ANDed are STRICTER than the sum they add up to, so dropping a total and
    searching its mods individually narrows the search the pseudo exists to
    widen. +23% cold and +16% to all elemental is 71 total resistance and any
    distribution of it is a comparable; cold >= 23 AND all-elemental >= 16
    demands that exact pair.
    """
    from sox.valuation.mods import coherence_keys, dominant_archetype, matched

    dominant, _count = dominant_archetype(matched(searchable_mods(item), index),
                                          seed=defence_seed(item))
    cohering, other = [], []
    for pseudo_id, value, used in pseudo_totals(item, index):
        entries = [e for t in used if (e := match_mod(t, index)) is not None]
        if dominant is None or any(dominant in coherence_keys(e) for e in entries):
            cohering.append((pseudo_id, value, used))
        else:
            other.append((pseudo_id, value, used))
    return cohering, other


def pseudo_mod_texts(item: dict, index: dict[str, ModEntry]) -> list[str]:
    """Item mods the query adds up into a pseudo total instead of searching.

    They ARE searched — through the total — but not under their own stat id,
    and the breakdown has to say so or the row reads as a stat filter that is
    not in the query.
    """
    cohering, other = ordered_pseudo_totals(item, index)
    return [text for _id, _value, used in cohering + other for text in used]


def searchable_implicits(item: dict, index) -> list[str]:
    """Implicits the search can actually use.

    An implicit occupies no affix slot, so it does not score and does not eat
    into the room left to craft — but it is a real stat a buyer filters on,
    and 40 of the allowlisted mods can be rolled as one.
    """
    from sox.valuation.mods import match_mod

    out = []
    for text in item.get("implicitMods") or []:
        entry = match_mod(text, index)
        if entry is not None and entry.implicit_ids:
            out.append(text)
    return out


def granted_skill_text(item: dict) -> list[str]:
    """The granted-skill line as the item words it, when it is searched."""
    if granted_skill_filter(item) is None:
        return []
    for prop in item.get("properties") or []:
        if prop.get("name") == "Grants Skill":
            return [f"Grants Skill: {str(prop['values'][0][0]).strip()}"]
    return []


def _build(
    item: dict,
    category: str,
    index: dict[str, ModEntry],
    notables: dict[str, str],
    status: str = "any",
    relax: int = 0,
) -> tuple[dict, str, list[str]]:
    """The query, the buyer group behind it, and the stats it asks for.

    One function, because the report is evidence about the price and the two
    used to be derived separately from the same item.
    """
    max_stats = RELAX_STEPS[min(relax, len(RELAX_STEPS) - 1)]
    scale = 1.0  # minimums are never lowered; see RELAX_STEPS

    type_filters: dict = {"category": {"option": category}}
    rarity = rarity_of(item)
    if classify(item) is ItemClass.UNIQUE:
        # A unique is identified by name; its base alone would match rares.
        type_filters["rarity"] = {"option": "unique"}
    elif rarity is not None:
        # The rarity itself, not "nonunique", which spans all three at once.
        # A rare, a magic and a normal of the same base are different goods:
        # the normal is bought as a craft base and priced on its ilvl, the
        # magic on its two mods and the room to regal it, the rare on its
        # mods. Lumping them together priced each against the others' market.
        type_filters["rarity"] = {"option": rarity.value}

    # Item level constrains a CRAFT BASE and nothing else. A normal or magic
    # item is bought for the tiers its level can roll, so the level is the
    # good itself and is pinned exactly — an ilvl 79 base is a different
    # product from an ilvl 82 one, not a cheaper copy of it. A rare is bought
    # on the mods it already rolled and a unique on its roll; there the level
    # excludes comparables while describing nothing a buyer shops for.
    ilvl = int(item.get("ilvl") or 0)
    if ilvl and rarity in (Rarity.NORMAL, Rarity.MAGIC):
        pinned = int(ilvl * scale)
        type_filters["ilvl"] = {"min": pinned, "max": pinned}

    # Quality only on gems. Everywhere else currency takes an item to 20%, so
    # pinning it excludes cheaper copies a buyer would happily quality up
    # themselves; on a gem the quality IS part of what is being bought.
    if classify(item) is ItemClass.GEM:
        quality = _property(item, "Quality")
        if quality:
            type_filters["quality"] = {"min": quality}

    # Requirements are a COST, not a benefit, so they take a max where every
    # other filter takes a min. An item demanding less than ours is strictly
    # easier to equip and is a comparable; one demanding more is not, and it
    # is also what separates a Bandit Mace from the whole one-handed category.
    requirements = {
        key: {"max": value}
        for key, value in (item.get("requirements") or {}).items()
    }

    equipment: dict = dict(damage_filters(item))
    for prop_name, (filter_id, flat, percent) in DEFENCE_PROPERTIES.items():
        value = equipment_minimum(item, prop_name, flat, percent)
        if value:
            equipment[filter_id] = {"min": value}

    all_mods = searchable_mods(item)

    # Notables ARE the value of a Megalomaniac, so they outrank every mod and
    # are trimmed last. They still take part in the ladder: an exact pair is
    # often unlisted, while "a Megalomaniac with this one notable" usually is,
    # and that single notable is what the item is really worth.
    NOTABLE_WEIGHT = 99
    # Ranked with the notables and for the same reason: a flag is what the
    # item IS, not what it is worth, so widening must drop mods around it
    # rather than dropping it.
    FLAG_WEIGHT = 99
    # A mod already covered by an equipment filter must not also be a stat
    # filter: the item's total includes it, so constraining both asks for it
    # twice. Spirit is the case that matters — local on a sceptre, where the
    # filter covers it, but global on an amulet, where it must stay a stat.
    covered = set(defence_mod_texts(item))

    # Stats with a pseudo total are searched as that total instead, so their
    # mods must not also appear individually.
    #
    # A total that does not serve the item's archetype is still searched — the
    # resistances on an attack ring are worth something — but it goes in
    # BEHIND the mods that do, so widening drops it first. Ahead of them, an
    # Intelligence total on an elemental weapon constrained the search to
    # items carrying Intelligence, which most comparables do not: that alone
    # moved a 3ex quarterstaff to a 50ex median.
    totals, off_archetype = ordered_pseudo_totals(item, index)
    for _pseudo_id, _value, used in totals + off_archetype:
        covered.update(used)

    scored: list[tuple[int, str, object]] = []
    for text in all_mods:
        if text in covered:
            continue
        if text.startswith("Allocates "):
            # Through the annotation: the amulet says "Allocates The Soul
            # Meridian — Unscalable Value", and the tag is not part of the
            # notable's name. Left on, the lookup missed and the item's most
            # valuable line was neither scored nor searched.
            from sox.valuation.mods import strip_annotation

            stat_id = notables.get(
                strip_annotation(text[len("Allocates "):]).strip())
            if stat_id:
                scored.append((NOTABLE_WEIGHT, text, _Notable(stat_id)))
            continue
        # A flag is checked before the allowlist because the allowlist has
        # nothing to say about it: there is no roll to weigh, so it never
        # earned an entry, and the value-less test below would drop it anyway.
        stat_id = flag_stat_id(item, text)
        if stat_id is not None:
            scored.append((FLAG_WEIGHT, text, _Flag(stat_id)))
            continue
        entry = match_mod(text, index)
        if entry is None:
            continue  # never guess a stat id
        if not parse_values(text):
            continue
        # A COPY per occurrence, because the allowlist hands back one shared
        # entry per wording and an item can carry that wording twice: an Iron
        # Ring's implicit and its Flaring prefix are both "Adds # to #
        # Physical Damage to Attacks". The maps below are keyed on entry
        # identity, so one shared object made the two mods one — the Tier-1
        # prefix, 12 to 31, was asked for as the implicit's floor of 2.5,
        # twice, and never as itself. The search then described any Iron Ring
        # with the base implicit, and priced a 1-divine ring at 1 exalted.
        scored.append((entry.weight, text, replace(entry)))

    # Choose stats that SYNERGIZE, not merely the heaviest ones. Selecting by
    # weight alone can mix archetypes and describe a buyer who does not exist.
    notable_items = [(w, t, e) for w, t, e in scored if isinstance(e, _Notable)]
    flag_items = [(w, t, e) for w, t, e in scored if isinstance(e, _Flag)]
    mod_items = [(w, t, e) for w, t, e in scored
                 if not isinstance(e, (_Notable, _Flag))]

    chosen_entries, order_key = select_synergistic(
        [e for _, _, e in mod_items], max_stats,
        tiers=item.get("modTiers") or {},
        texts={id(e): t for _, t, e in mod_items},
        rolls=item.get("modRanges") or {},
        seed=defence_seed(item),
    )
    by_entry = {id(e): t for _, t, e in mod_items}

    # Every stat filter travels with the wording the report will show it as.
    # The two were derived twice from the same item and drifted: the report
    # named Spirit as searched at one rung and dropped it at the next while
    # the query carried it at every rung, so the breakdown said a mod had been
    # ignored that the price in fact rested on.
    #
    # The order everything survives widening, front kept longest:
    #
    #   1. identity — unique flags, and notables the item ROLLED (a
    #      Megalomaniac's cannot be changed; drop one and the search
    #      describes a different item)
    #   2. archetype — the pseudo totals and mods the dominant buyer
    #      filters on
    #   3. anointed notables — an anoint can be re-anointed, so it is a mod
    #      of the amulet, not its identity
    #   4. generic value — defence totals and mods (life, resistances)
    #      every buyer pays for whatever their build
    #   5. unrelated — mods and totals serving some OTHER buyer. An attack
    #      mod on a minion ring constrains the comparables while describing
    #      nobody who would buy it; it outlived a 36% chaos-res total, and
    #      the rung that priced the ring searched a buyer who does not exist.
    ranked: list[tuple[dict, list[str]]] = []

    # A Corruption Enhancement reads like an explicit mod but is filed under
    # the enchant group; searching it as explicit returned 0 listings where
    # enchant returned thousands.
    enchant_texts = set(item.get("enchantMods") or [])
    implicit_texts = set(item.get("implicitMods") or [])

    # An enchant-sourced "Allocates" IS an anoint; a rolled one is identity.
    rolled_notables = [(w, t, e) for w, t, e in notable_items
                       if t not in enchant_texts]
    anointed_notables = [(w, t, e) for w, t, e in notable_items
                         if t in enchant_texts]

    ranked += [({"id": e.stat_id, "value": {}}, [text])
               for _, text, e in rolled_notables]

    # No minimum, because there is no number to put one on. The id alone is
    # the whole filter: the listing either counts as a Forest Map or it does
    # not.
    ranked += [({"id": e.stat_id, "value": {}}, [text])
               for _, text, e in flag_items]

    for pseudo_id, value, used in totals:
        labels = [e.text if (e := match_mod(t, index)) else t for t in used]
        ranked.append(({"id": pseudo_id, "value": {"min": value}}, labels))

    cohering_rows: list[tuple[dict, list[str]]] = []
    generic_rows: list[tuple[dict, list[str]]] = []
    unrelated_rows: list[tuple[dict, list[str]]] = []
    buckets = (cohering_rows, generic_rows, unrelated_rows)
    for entry in chosen_entries:
        text = by_entry[id(entry)]
        values = parse_values(text)
        minimum = round(filter_value(entry.text, values) * scale, 2)
        # A roll one point under ours is the same good at the same tier, so
        # the floor of the roll's own range is the honest minimum — exactly
        # as implicits already search. Gale Nail, live: five minimums at the
        # exact rolls matched 0 listings, because every same-tier near-copy
        # rolled a point under on some axis (leech 7.76 against 7.81), and
        # the ladder, able only to drop whole mods, then priced the ring at
        # the 1 ex junk floor while the same-tier market sat at 3-20 ex.
        # Single-value mods only: an added-damage filter compares the
        # AVERAGE of its two numbers, and modRanges holds one roll's range,
        # not the average's — flooring it would compare the wrong quantity.
        span = (item.get("modRanges") or {}).get(text)
        if span and len(values) == 1:
            minimum = min(minimum, round(span[1] * scale, 2))
        ids = stat_ids_for(entry, category)
        if text in enchant_texts:
            ids = regroup(ids, "enchant")
        elif text in implicit_texts:
            # The implicit twin is a different stat id, not a prefix swap on
            # the same one, so it comes from the allowlist rather than
            # regroup(). Without a twin the mod cannot be searched at all —
            # asking the explicit table about an implicit returns nothing, so
            # omitting it beats sending a filter that matches no listing.
            if not entry.implicit_ids:
                continue
            ids = entry.implicit_ids
            # An implicit comes with the base rather than being rolled onto
            # it, so the buyer is shopping for the base and will take any roll
            # of it. Filtering at ours would drop the same base over a
            # difference nobody is paying for, so the floor of the range is
            # the honest minimum.
            span = (item.get("modRanges") or {}).get(text)
            if span:
                minimum = round(span[1] * scale, 2)
        # One mod, several ids the listing might carry it under — Spirit is
        # local on a sceptre and global on an amulet — so it is asked for as
        # "at least one of these". It is still ONE mod and takes one place in
        # the ladder: kept outside it, an or-group could not be widened away
        # and rungs 0 and 1 built the identical query.
        if len(ids) > 1:
            row = ({
                "type": "count",
                "value": {"min": 1},
                "filters": [
                    {"id": stat_id, "value": {"min": minimum}} for stat_id in ids
                ],
            }, [entry.text])
        else:
            row = ({"id": ids[0], "value": {"min": minimum}}, [entry.text])
        buckets[survival_class(entry, order_key or None)].append(row)

    ranked += cohering_rows
    ranked += [({"id": e.stat_id, "value": {}}, [text])
               for _, text, e in anointed_notables]

    # A total is generic value when any mod feeding it carries the defence
    # tag — resistances and life are bought by every build — and unrelated
    # otherwise: an Intelligence total on an elemental weapon constrained the
    # search to items most comparables do not carry, which alone moved a 3ex
    # quarterstaff to a 50ex median. Unrelated totals sit at the very back.
    generic_totals: list[tuple[dict, list[str]]] = []
    unrelated_totals: list[tuple[dict, list[str]]] = []
    for pseudo_id, value, used in off_archetype:
        labels = [e.text if (e := match_mod(t, index)) else t for t in used]
        row = ({"id": pseudo_id, "value": {"min": value}}, labels)
        feeders = [e for t in used if (e := match_mod(t, index)) is not None]
        if any("defence" in coherence_keys(e) for e in feeders):
            generic_totals.append(row)
        else:
            unrelated_totals.append(row)

    ranked += generic_totals + generic_rows
    ranked += unrelated_rows + unrelated_totals

    # The granted skill is exempt from the widening cap. Dropping it would not
    # widen the search, it would change what is being searched for: a Level 20
    # Chaos Bolt wand priced without the Chaos Bolt is priced as a bare wand.
    skill = granted_skill_filter(item)
    # A count group is a stat group of its own, never a member of the `and`.
    skill_filters = [skill] if skill and "type" not in skill else []
    skill_groups = [skill] if skill and "type" in skill else []
    # The no-mods rung still keeps one notable. A notable-granting jewel IS
    # its notables — priced without them it is one of ~25,000 Megalomaniacs
    # the index already reports as worth 1 exalted. A unique's flag is the
    # same kind of thing: drop the biome and a Mastered Domain tablet is one
    # of 26,357 the index also reports as worth 1 exalted.
    keep = max(max_stats, 1) if rolled_notables or flag_items else max_stats
    kept = ranked[:keep]
    and_filters = skill_filters + [f for f, _ in kept if "type" not in f]
    or_groups = skill_groups + [f for f, _ in kept if "type" in f]
    searched = [text for _, labels in kept for text in labels]
    # The query as sent, one line per stat filter with the floor it asks at.
    # The skill line already carries its level in its own wording.
    shown = granted_skill_text(item) + [_query_line(f, labels)
                                        for f, labels in kept]

    # Named from the ITEM's mods, not from what a rung kept. The archetype is
    # the judge that ordered the whole ladder — cohering mods survive, the
    # rest drop first — so the label and the judge must come from the same
    # derivation. Read off the surviving filters instead, the name drifted or
    # vanished exactly where the reader needs it most: a minion ring widened
    # down to minion crit alone reported no archetype at all.
    from sox.valuation.mods import dominant_archetype, matched

    group = dominant_archetype(matched(all_mods, index),
                               seed=defence_seed(item))[0] or ""
    if notable_items:
        group = "notable"
    elif flag_items and not group:
        # A unique tablet IS its flag. A rare carrying an implicit flag —
        # an Unset Ring's skill slot — is still bought on its mods, and its
        # archetype keeps the name.
        group = "variant"

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
    if requirements:
        query["query"]["filters"]["req_filters"] = {"filters": requirements}
    endgame = waystone_filters(item, category)
    if endgame:
        query["query"]["filters"]["map_filters"] = {"filters": endgame}

    # A corrupted or sanctified listing is not "at least as good" as an
    # untouched copy — corruption closes off every further craft, and a
    # sanctified copy carries a bonus ours does not. Leaving them in the
    # results lets one drag the cheapest match below our item's real floor.
    #
    # Pinned only when ours is neither. Once the item has been touched at all
    # the whole market is comparable again, so both stay unconstrained rather
    # than pinning the one flag we happen not to carry.
    # Twice corrupted is deliberately NOT pinned. It reads as scarcer, and the
    # narrowed search does return dearer listings, but a second corruption is
    # as likely to have ruined the item as improved it — the dearer listings
    # are ones that survived it, not proof that ours did.
    if not item.get("corrupted") and not item.get("sanctified"):
        query["query"]["filters"]["misc_filters"] = {"filters": {
            "corrupted": {"option": "false"},
            "sanctified": {"option": "false"},
        }}
    # Keyed on the item's RARITY, not on its pricing class. A unique Tablet,
    # Waystone or Relic classifies as ENDGAME — the class decides which
    # market prices it, and those have no index — so gating the name on
    # ItemClass.UNIQUE left it off and searched the whole category: every
    # unique tablet in the game, priced at whichever was cheapest.
    if item.get("name") and rarity is Rarity.UNIQUE:
        query["query"]["name"] = item["name"]

    # A normal, magic or unique item is bought as a BASE, so the base is what
    # the search asks for. Without it the query describes a CATEGORY: an
    # ilvl 81 Heavy Belt matched 5,595 belts and the cheapest were a Double
    # Belt, a Mail Belt and a Wide Belt at 1 exalted, none of them the item in
    # hand. Pinned, the same search matched 4,896 Heavy Belts from 14.
    #
    # A RARE is deliberately left out. It is bought on the mods it rolled, and
    # its base is already bounded by the requirements, which are searched as a
    # cap — that is what separates a Bandit Mace from every one-hander.
    if rarity in (Rarity.NORMAL, Rarity.MAGIC, Rarity.UNIQUE):
        base = base_type(item)
        if base:
            query["query"]["type"] = base
    return query, group, searched, shown


def _query_line(f: dict, labels: list[str]) -> str:
    """One stat filter as the report shows it: its wording and its floor."""
    def fmt(value) -> str:
        return str(int(value)) if float(value).is_integer() else str(value)

    if "type" in f:
        # An or-group is one mod asked for under several ids; the ids share
        # one minimum and the wording is what the reader needs.
        return f"{labels[0]} ≥ {fmt(f['filters'][0]['value']['min'])}"
    fid = f.get("id", "")
    minimum = (f.get("value") or {}).get("min")
    if fid.startswith("pseudo."):
        name = fid.removeprefix("pseudo.pseudo_total_").replace("_", " ")
        return f"total {name} ≥ {fmt(minimum)}"
    if minimum is None:
        # A notable or a flag: the id alone is the whole filter.
        return labels[0]
    return f"{labels[0]} ≥ {fmt(minimum)}"


def build_query(
    item: dict,
    category: str,
    index: dict[str, ModEntry],
    notables: dict[str, str],
    status: str = "any",
    relax: int = 0,
) -> dict:
    return _build(item, category, index, notables, status, relax)[0]


def explain_query(
    item: dict,
    index: dict[str, ModEntry],
    notables: dict[str, str],
    relax: int = 0,
) -> list[str]:
    """The stat filters a rung sends, each with the floor it asks at.

    The breakdown highlights which mods drove the search; these lines show
    the search itself — the pseudo sums and the floors — which nothing else
    in the output states.
    """
    return _build(item, category_for(item) or "", index, notables,
                  relax=relax)[3]


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

    Read off the query itself rather than worked out again. Derived twice it
    drifted: a +61 Spirit roll went into every rung of a chest's search as an
    or-group over its two stat ids, while this reported it at rung 0 and
    dropped it at rung 2 — so the breakdown showed the item's best mod as one
    the price did not rest on.
    """
    _, group, searched, _ = _build(
        item, category_for(item) or "", index, notables, relax=relax)
    return (group or None), searched


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

    # A weapon's damage mods are covered by dps/pdps/edps the same way. Left
    # as stat filters too they would be asked for twice — and worse, they pin
    # the SOURCE: a weapon with the same elemental DPS rolled as fire instead
    # of cold is a comparable, and searching "Adds # to # Cold Damage"
    # excludes it.
    if damage_filters(item):
        for text in searchable_mods(item):
            if any(p.search(text) for p in LOCAL_DAMAGE_MODS) and text not in out:
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
    # Mods riding an equipment filter and the granted skill survive every
    # rung, so they are always lit. Appended here rather than in the caller:
    # cli adding implicits on its own lit the ring's implicit at a rung that
    # had dropped the chaos total it rides in.
    for text in defence_mod_texts(item) + granted_skill_text(item):
        if text not in out:
            out.append(text)
    return out
