"""Decide how an item should be priced, and why."""

from __future__ import annotations

from dataclasses import dataclass

from sox.scout import IndexEntry
from sox.valuation.allowlists import BaseRules, ModEntry, UniqueRules
from sox.valuation.classify import ItemClass, Rarity, classify, display_name, rarity_of
from sox.valuation.mods import coherence_bonus, match_mod, score_mods
from sox.valuation.rolls import roll_score, spread_of

AVOID_PENALTY = 3

# Affix capacity by rarity: rare is 3 prefixes + 3 suffixes.
AFFIX_CAPACITY = {Rarity.RARE: 6, Rarity.MAGIC: 2, Rarity.NORMAL: 0}
MAX_OPEN_BONUS_PREMIUM = 3
MAX_OPEN_BONUS_ORDINARY = 1


@dataclass(frozen=True)
class Verdict:
    should_search: bool
    score: int
    reason: str


def item_mods(item: dict) -> list[str]:
    """Every mod that can be scored and searched on.

    Excludes unrevealed desecrated modifiers: their stat is unknown until
    revealed, so they are worth nothing and cannot be searched.
    """
    return (
        list(item.get("explicitMods") or [])
        + list(item.get("fracturedMods") or [])
        + list(item.get("runeMods") or [])
        + list(item.get("desecratedMods") or [])
    )


def used_affixes(item: dict) -> int:
    """Affix slots consumed, INCLUDING unrevealed ones.

    An unrevealed modifier is useless but not free: it holds a slot a buyer
    would otherwise craft into, so it must reduce the open-affix bonus exactly
    as a junk mod does.
    """
    return len(set(item_mods(item))) + len(item.get("unrevealedMods") or [])


def open_affix_bonus(item: dict, mod_score: int, has_premium: bool) -> tuple[int, str]:
    """Room left to craft is part of what a buyer pays for.

    Corrupted and mirrored items score nothing here: neither can be modified
    again, so their empty slots are permanently empty. Treating one as a craft
    base is not a preference, it is simply wrong.
    """
    if item.get("corrupted") or item.get("mirrored"):
        return 0, ""

    rarity = rarity_of(item)
    # Normal items are excluded: their value IS open affix space, which the
    # base score (ilvl + base type + rune family) already measures.
    if rarity not in (Rarity.RARE, Rarity.MAGIC):
        return 0, ""

    open_slots = AFFIX_CAPACITY[rarity] - used_affixes(item)
    if open_slots <= 0:
        return 0, ""

    if has_premium:
        bonus = min(open_slots, MAX_OPEN_BONUS_PREMIUM)
    elif mod_score >= 4:
        bonus = min(open_slots, MAX_OPEN_BONUS_ORDINARY)
    else:
        return 0, ""  # a blank rare also has open slots and is not worth a search
    return bonus, f"open{open_slots}"


def _base_name(item: dict) -> str:
    return item.get("baseType") or item.get("typeLine") or ""


def score_gear(item: dict, index: dict[str, ModEntry], base_rules: BaseRules) -> tuple[int, str]:
    ilvl = int(item.get("ilvl") or 0)
    base = _base_name(item)
    mods = item_mods(item)
    reasons = []

    mod_score, _ = score_mods(mods, index)
    if mod_score:
        reasons.append(f"coherence={mod_score}")

    bonus, why = coherence_bonus(mods, index)
    if bonus:
        mod_score += bonus
        reasons.append(why)

    has_premium = any(
        (entry := match_mod(text, index)) is not None and entry.weight >= 3
        for text in mods
    )
    open_bonus, open_why = open_affix_bonus(item, mod_score, has_premium)
    if open_bonus:
        mod_score += open_bonus
        reasons.append(open_why)

    base_score = 0
    for min_ilvl, weight in base_rules.ilvl_tiers:
        if ilvl >= min_ilvl:
            base_score += weight
            reasons.append(f"ilvl{min_ilvl}+")
            break

    named = base_rules.named.get(base)
    if named:
        base_score += named
        reasons.append("named-base")

    for prefix, extra in base_rules.rune_prefixes.items():
        if base.startswith(prefix + " "):
            base_score += extra
            reasons.append(prefix.lower())
            break

    if base in base_rules.avoid:
        base_score -= AVOID_PENALTY
        reasons.append("avoid-base")

    rarity = rarity_of(item)
    if rarity is Rarity.RARE:
        total = mod_score + (1 if base_score >= 4 else 0)
    else:
        total = base_score + mod_score
    return total, ",".join(reasons) or "none"


def qualifies(item: dict, score: int) -> bool:
    ilvl = int(item.get("ilvl") or 0)
    if rarity_of(item) is Rarity.RARE:
        return score >= 6 or (score >= 4 and ilvl >= 80)
    return score >= 4


def has_notable(item: dict) -> bool:
    return any(m.startswith("Allocates ") for m in item_mods(item))


def should_search_unique(item: dict, entry: IndexEntry | None, rules: UniqueRules) -> str | None:
    """Return the escalation reason, or None to take the index price."""
    if has_notable(item):
        # Megalomaniac-class: value is WHICH notables, which the index cannot
        # express. It reports 1ex across ~25,000 listings for all of them.
        return "notable"
    if item.get("corrupted"):
        return "corrupted"
    if entry is None:
        # Not in the index at all. We know its name, so a search can still
        # price it — leaving it unpriced would be giving up with a usable
        # option in hand.
        return "not-indexed"

    # If the index has no mods for this unique but our copy does, the index is
    # not describing our item and its price is not evidence about it.
    if not (entry.metadata.get("explicit_mods") or entry.metadata.get("implicit_mods")):
        if item_mods(item):
            return "index-cannot-describe"

    if entry.price_ex >= rules.thresholds.get("chase_price_ex", 5000):
        return "chase-price"

    if spread_of(entry.metadata) < rules.thresholds.get("swing_ratio", 2.0):
        return None
    # A perfect copy of a worthless item is still worthless: Thunderfist
    # spreads x111 at ~3ex and would otherwise satisfy every clause above.
    if entry.price_ex < rules.thresholds.get("min_escalation_price_ex", 50):
        return None

    score = roll_score(item_mods(item), entry.metadata)
    if score is None:
        return None
    if score >= rules.thresholds.get("roll_score_percentile", 0.75):
        return "swingy-good-roll"
    return None


def assess(
    item: dict,
    index_entry: IndexEntry | None,
    mod_index: dict[str, ModEntry],
    base_rules: BaseRules,
    unique_rules: UniqueRules,
) -> Verdict:
    """Whether this item needs a live search, and why."""
    item_class = classify(item)

    if item_class is ItemClass.UNKNOWN:
        return Verdict(False, 0, "unknown-class")
    if item_class in (ItemClass.CURRENCY, ItemClass.GEM):
        return Verdict(False, 0, "index")
    if item_class is ItemClass.ENDGAME:
        # No index covers these at all, so every one is worth a search.
        return Verdict(True, 0, "no-index")
    if item_class is ItemClass.UNIQUE:
        reason = should_search_unique(item, index_entry, unique_rules)
        return Verdict(bool(reason), 0, reason or "index")

    if item_class is ItemClass.JEWEL and has_notable(item):
        return Verdict(True, 0, "notable")

    score, reason = score_gear(item, mod_index, base_rules)
    return Verdict(qualifies(item, score), score, reason)
