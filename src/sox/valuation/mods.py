"""Map item mod text onto allowlist entries.

Normalization mirrors scripts/resolve_allowlist.py so a mod matches whatever
its case, spacing, or leading plus. An unmatched mod is skipped — the tool
never guesses a stat id, because a wrong id silently skews the search built
from it.
"""

from __future__ import annotations

import re

from sox.valuation.allowlists import ModEntry

_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")

# Most a pile of weight-1 mods may contribute in total. Community pricing
# guidance is explicit that 4+ low-tier mods make an item worth LESS: they
# occupy affix slots a buyer would otherwise craft into.
SUPPORTING_CAP = 2

# Ceiling on the coherence bonus so a deep stack cannot dominate the score.
MAX_COHERENCE_BONUS = 3

# Companions ARE minions and share their buyers, so a companion mod counts
# toward both. Universal minion mods count toward every subtype.
MINION_SUBTYPES = ("attack", "caster", "companion")


def normalize_mod(text: str) -> str:
    text = _NUMBER.sub("#", text)
    text = text.casefold()
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<![a-z0-9])\+(?=#)", "", text)
    return text


def build_index(entries: list[ModEntry]) -> dict[str, ModEntry]:
    """Normalized text -> entry. A dict, because the allowlist is 400+ mods
    and a linear scan per item mod is needless work."""
    return {normalize_mod(e.text): e for e in entries}


def match_mod(text: str, index: dict[str, ModEntry]) -> ModEntry | None:
    return index.get(normalize_mod(text))


def matched(item_mods: list[str], index: dict[str, ModEntry]) -> list[ModEntry]:
    out = []
    for text in item_mods:
        entry = match_mod(text, index)
        if entry is not None:
            out.append(entry)
    return out


def explain_score(item_mods: list[str], index: dict[str, ModEntry]) -> list[tuple[str, int | None]]:
    """Every mod with the weight it contributed, or None if unrecognised.

    A verdict of junk is only trustworthy if you can see what produced it —
    and an unrecognised mod is as likely to be a gap in the allowlist as a
    worthless roll.
    """
    out: list[tuple[str, int | None]] = []
    supporting = 0
    for text in item_mods:
        entry = match_mod(text, index)
        if entry is None:
            out.append((text, None))
            continue
        weight = entry.weight
        if weight == 1:
            if supporting >= SUPPORTING_CAP:
                out.append((text, 0))  # capped: contributed nothing
                continue
            supporting += weight
        out.append((text, weight))
    return out


def score_mods(item_mods: list[str], index: dict[str, ModEntry]) -> tuple[int, dict[str, int]]:
    total = 0
    supporting = 0
    by_category: dict[str, int] = {}
    for entry in matched(item_mods, index):
        weight = entry.weight
        if weight == 1:
            if supporting >= SUPPORTING_CAP:
                continue
            supporting += weight
        total += weight
        by_category[entry.category] = by_category.get(entry.category, 0) + weight
    return total, by_category


def coherence_keys(entry: ModEntry) -> tuple[str, ...]:
    """The buyer groups a mod belongs to.

    Minion buyers are not interchangeable: universal minion mods serve every
    minion build, while subtype mods (attack / caster / companion) serve only
    theirs. Companions are minions, so they count toward both.
    """
    if entry.subject in ("minion", "companion"):
        if entry.minion_subtype:
            return (f"minion:{entry.minion_subtype}",)
        return ("minion", *(f"minion:{s}" for s in MINION_SUBTYPES))
    if entry.subject == "totem":
        return ("totem",)
    return tuple(entry.tags)


def dominant_archetype(entries: list[ModEntry]) -> tuple[str | None, int]:
    """The buyer group the most mods on this item serve.

    A group of one is not a cluster. With every archetype at a count of one
    the winner is whichever happened to be seen first, and ordering the query
    by it put an incidental mana roll ahead of the item's best mod.
    """
    counts: dict[str, int] = {}
    for entry in entries:
        for key in coherence_keys(entry):
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None, 0
    key, top = max(counts.items(), key=lambda kv: kv[1])
    if top < 2:
        return None, top
    return key, top


def select_synergistic(
    entries: list[ModEntry],
    limit: int,
    tiers: dict[str, int] | None = None,
    texts: dict[int, str] | None = None,
    rolls: dict[str, tuple[float, float, float]] | None = None,
) -> tuple[list[ModEntry], str]:
    """Order the mods for the query: cohering first, then the rest.

    Every allowlisted mod belongs in the search — a chaos resistance roll adds
    value whether or not it serves the item's main archetype. Coherence
    decides the ORDER, not membership, so when the ladder widens the mods that
    serve one buyer are the ones that survive.

    Within each group, roll quality leads and mod tier breaks ties.
    """
    if not entries:
        return [], ""

    key, _ = dominant_archetype(entries)

    def rank(entry: ModEntry) -> tuple[int, float, int]:
        text = (texts or {}).get(id(entry))
        tier = (tiers or {}).get(text or "", 99)
        # Roll quality decides between mods of equal tier. A weak roll is the
        # first thing a buyer stops filtering on: "Adds 6 to 102 Lightning"
        # sits near the floor of its range and says little about the item,
        # while a near-max roll is what makes it worth listing.
        percentile = 0.5
        span = (rolls or {}).get(text or "")
        if span:
            actual, low, high = span
            if high > low:
                percentile = min(max((actual - low) / (high - low), 0.0), 1.0)
        # Weight leads: a build-defining mod belongs in the query whatever it
        # rolled. Roll quality then separates mods of equal weight — "Adds 6
        # to 102 Lightning" at the floor of its range says less about the item
        # than a strong roll of the same weight — and tier breaks what is left.
        return (-entry.weight, -percentile, tier)

    if key is None:
        # No cluster: rank alone decides, and there is no archetype to name.
        ordered = sorted(entries, key=rank)
        return ordered[:limit], ""

    cohering = [e for e in entries if key in coherence_keys(e)]
    others = [e for e in entries if key not in coherence_keys(e)]
    cohering.sort(key=rank)
    others.sort(key=rank)
    return (cohering + others)[:limit], key


def coherence_bonus(item_mods: list[str], index: dict[str, ModEntry]) -> tuple[int, str]:
    """Reward many mods serving ONE archetype.

    Counted over archetype tags and subjects, not allowlist categories: a real
    build's mods span categories (projectile levels + attack speed + flat
    damage is a bow item), while one category can hold mods for two unrelated
    builds (+Melee Skills and +Spell Skills share no buyer).
    """
    entries = matched(item_mods, index)
    key, top = dominant_archetype(entries)
    if key is None:
        return 0, ""
    bonus = min(max(top - 1, 0), MAX_COHERENCE_BONUS)
    return bonus, (f"{key}x{top}" if bonus else "")
