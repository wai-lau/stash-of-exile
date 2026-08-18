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
    """The buyer group the most mods on this item serve."""
    counts: dict[str, int] = {}
    for entry in entries:
        for key in coherence_keys(entry):
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None, 0
    key, top = max(counts.items(), key=lambda kv: kv[1])
    return key, top


def select_synergistic(
    entries: list[ModEntry], limit: int
) -> tuple[list[ModEntry], str]:
    """Pick the mods a real buyer would search on, together.

    This is the judgement a price-check overlay leaves to the player: which
    stats belong to one build. Picking the heaviest mods regardless of
    archetype produces a query no single buyer wants — +Melee Skills AND
    +Spell Skills describes nobody. So mods serving the item's dominant
    archetype come first, and only then the heaviest leftovers.
    """
    if not entries:
        return [], ""

    key, top = dominant_archetype(entries)
    if key is None or top < 2:
        chosen = sorted(entries, key=lambda e: -e.weight)[:limit]
        return chosen, "top-weight"

    in_group = [e for e in entries if key in coherence_keys(e)]
    outside = [e for e in entries if key not in coherence_keys(e)]
    in_group.sort(key=lambda e: -e.weight)
    outside.sort(key=lambda e: -e.weight)

    chosen = (in_group + outside)[:limit]
    return chosen, key


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
