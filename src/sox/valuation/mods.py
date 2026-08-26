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

# "Area has patches of Shocked Ground — Unscalable Value". The game tags the
# map mods that nothing a player owns can scale. It is an annotation on the
# line, not part of the mod: the trade stats table carries the text without
# it, so a mod wearing one matched nothing at all.
_ANNOTATION = re.compile(r"\s*[—–-]\s*unscalable value\s*$", re.I)

# Most a pile of weight-1 mods may contribute in total. Community pricing
# guidance is explicit that 4+ low-tier mods make an item worth LESS: they
# occupy affix slots a buyer would otherwise craft into.
SUPPORTING_CAP = 2

# Ceiling on the coherence bonus so a deep stack cannot dominate the score.
MAX_COHERENCE_BONUS = 3

# Companions ARE minions and share their buyers, so a companion mod counts
# toward both. Universal minion mods count toward every subtype.
MINION_SUBTYPES = ("attack", "caster", "companion")


def strip_annotation(text: str) -> str:
    """The mod without the game's "Unscalable Value" tag, case intact.

    Needed where the WORDING is the key and not just a normalised form: a
    notable is looked up by name, and "The Soul Meridian — Unscalable Value"
    is not in the table while "The Soul Meridian" is.
    """
    return _ANNOTATION.sub("", text)


def normalize_mod(text: str) -> str:
    text = _NUMBER.sub("#", text)
    text = text.casefold()
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<![a-z0-9])\+(?=#)", "", text)
    return _ANNOTATION.sub("", text)


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


def dominant_archetype(
    entries: list[ModEntry], seed: dict[str, int] | None = None
) -> tuple[str | None, int]:
    """The buyer group the most mods on this item serve.

    A group of one is not a cluster. With every archetype at a count of one
    the winner is whichever happened to be seen first, and ordering the query
    by it put an incidental mana roll ahead of the item's best mod.

    Counts tie constantly — a hybrid weapon carrying two elemental mods and
    two physical ones is 2-2 — and taking the first was the same coin flip in
    a different place. The heavier group wins: two build-defining mods
    describe a buyer better than one build-defining and one supporting. If the
    weights tie too the item genuinely serves both, and claiming either is
    worse than admitting there is no cluster.
    """
    # The item's own defence type votes: an ES chest is an ES item before any
    # mod is read.
    counts: dict[str, int] = dict(seed or {})
    weights: dict[str, int] = {}
    for entry in entries:
        for key in coherence_keys(entry):
            counts[key] = counts.get(key, 0) + 1
            weights[key] = weights.get(key, 0) + entry.weight
    if not counts:
        return None, 0
    # Shortest key first among equals, so a tie inside one family is reported
    # as the family — "minion", not "minion:attack".
    ranked = sorted(counts.items(),
                    key=lambda kv: (-kv[1], -weights.get(kv[0], 0), len(kv[0])))
    key, top = ranked[0]
    if top < 2:
        return None, top
    # Only a DIFFERENT family can make an item ambiguous. A universal minion
    # mod votes for "minion" and for every subtype of it at once, so the
    # runner-up is routinely the same mods under another name — and the tie
    # rule read that as two buyers and gave up. A ring carrying minion damage,
    # minion crit and minion attack speed reported "none — the mods serve
    # different builds", scored no coherence at all, and then had its query
    # ranked by weight alone, which is how a search describes a buyer who does
    # not exist.
    rival = next(((k, c) for k, c in ranked[1:]
                  if k.split(":", 1)[0] != key.split(":", 1)[0]), None)
    if rival is not None:
        runner, second = rival
        if second == top and weights.get(runner, 0) == weights.get(key, 0):
            return None, top
    return key, top


def survival_class(entry: ModEntry, dominant: str | None) -> int:
    """How long a mod deserves to survive widening, smaller = longer.

    0 — serves the dominant archetype: what the item's buyer filters on.
    1 — generic value: defence-tagged mods (life, resistances) that every
        buyer pays for whatever their build.
    2 — unrelated: serves some OTHER buyer — an attack mod on a minion ring
        constrains the comparables while describing nobody who would buy it,
        so it is the first thing widening gives up.

    With no dominant archetype there is nobody to be unrelated to, and rank
    alone decides.
    """
    if dominant is None:
        return 0
    keys = coherence_keys(entry)
    if dominant in keys:
        return 0
    return 1 if "defence" in keys else 2


def select_synergistic(
    entries: list[ModEntry],
    limit: int,
    tiers: dict[str, int] | None = None,
    texts: dict[int, str] | None = None,
    rolls: dict[str, tuple[float, float, float]] | None = None,
    seed: dict[str, int] | None = None,
) -> tuple[list[ModEntry], str]:
    """Order the mods for the query: cohering, then generic, then unrelated.

    Every allowlisted mod belongs in the search — a chaos resistance roll adds
    value whether or not it serves the item's main archetype. Coherence
    decides the ORDER, not membership, so when the ladder widens the mods that
    serve one buyer are the ones that survive — and a mod serving a DIFFERENT
    buyer goes behind the generic value everyone pays for.

    Within each class, weight leads, then roll quality, and tier breaks ties.
    """
    if not entries:
        return [], ""

    key, _ = dominant_archetype(entries, seed=seed)

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

    ordered = sorted(entries, key=lambda e: (survival_class(e, key), *rank(e)))
    return ordered[:limit], key or ""


def coherence_bonus(
    item_mods: list[str], index: dict[str, ModEntry],
    seed: dict[str, int] | None = None,
) -> tuple[int, str]:
    """Reward many mods serving ONE archetype.

    Counted over archetype tags and subjects, not allowlist categories: a real
    build's mods span categories (projectile levels + attack speed + flat
    damage is a bow item), while one category can hold mods for two unrelated
    builds (+Melee Skills and +Spell Skills share no buyer).
    """
    entries = matched(item_mods, index)
    key, top = dominant_archetype(entries, seed=seed)
    if key is None:
        return 0, ""
    bonus = min(max(top - 1, 0), MAX_COHERENCE_BONUS)
    return bonus, (f"{key}x{top}" if bonus else "")
