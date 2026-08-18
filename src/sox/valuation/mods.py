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


def coherence_bonus(item_mods: list[str], index: dict[str, ModEntry]) -> tuple[int, str]:
    """Reward many mods serving ONE archetype.

    Counted over archetype tags and subjects, not allowlist categories: a real
    build's mods span categories (projectile levels + attack speed + flat
    damage is a bow item), while one category can hold mods for two unrelated
    builds (+Melee Skills and +Spell Skills share no buyer).

    Minion buyers are not interchangeable either. Universal minion mods serve
    every minion build; subtype mods (attack / caster / companion) serve only
    theirs.
    """
    counts: dict[str, int] = {}
    universal_minion = 0
    subtype_counts = dict.fromkeys(MINION_SUBTYPES, 0)

    for entry in matched(item_mods, index):
        if entry.subject in ("minion", "companion"):
            if entry.minion_subtype:
                subtype_counts[entry.minion_subtype] = (
                    subtype_counts.get(entry.minion_subtype, 0) + 1
                )
            else:
                universal_minion += 1
            continue
        if entry.subject == "totem":
            counts["totem"] = counts.get("totem", 0) + 1
            continue
        for tag in entry.tags:
            counts[tag] = counts.get(tag, 0) + 1

    if universal_minion or any(subtype_counts.values()):
        counts["minion"] = universal_minion
        for subtype, n in subtype_counts.items():
            counts[f"minion:{subtype}"] = universal_minion + n

    if not counts:
        return 0, ""
    tag, top = max(counts.items(), key=lambda kv: kv[1])
    bonus = min(max(top - 1, 0), MAX_COHERENCE_BONUS)
    return bonus, (f"{tag}x{top}" if bonus else "")
