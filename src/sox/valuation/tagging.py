"""Archetype tagging: which buyer a mod's wording serves.

One set of rules for the allowlist generator (scripts/resolve_allowlist.py,
which writes the tags into mod_allowlist.toml) and for the runtime, which
tags the mods GGG's table knows and the allowlist never named. They used to
live in the script alone, so a mod resolved at runtime carried no tags:
"24% increased Global Armour, Evasion and Energy Shield" was searchable but
cohered with nothing, sat in the unrelated bin beside the evasion roll it
belongs with, and roll percentile decided which of the two survived
widening.

Substring rules on the normalised wording. The item's own text works as
well as the template: numbers fold to `#` first.
"""

from __future__ import annotations

import re

_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")


def _normalize(text: str) -> str:
    """The generator's normalisation, plus numbers folded to `#` so an
    item's "37% increased Evasion Rating" tags like the template."""
    text = _NUMBER.sub("#", text)
    text = text.casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"(?<![a-z0-9])\+(?=#)", "", text)


# --- Archetype tagging -------------------------------------------------
#
# Coherence must measure "do these mods serve ONE buyer", which the allowlist
# categories cannot do: a real build's mods SPAN categories (projectile levels
# + attack speed + flat damage is a textbook bow item), while one category can
# hold mods for two different builds (+Melee Skills and +Spell Skills are both
# skill_levels and share no buyer at all).
#
# Delivery tags (attack / spell / minion) are the ones that define a buyer.
# Everything else describes the item and never triggers a conflict.
DELIVERY_TAGS = ("attack", "spell", "minion")

# (substring searched in the canonical text, tags). All matches union.
# Deliberately conservative: a mod that could belong to either delivery gets
# NO delivery tag rather than a guessed one.
TAG_RULES = [
    ("minion", ("minion",)),
    ("allies in your presence", ("minion",)),
    ("companion", ("minion", "companion")),
    ("spectre", ("minion",)),
    ("offering", ("minion",)),
    ("totem", ("totem",)),
    ("reservation efficiency", ("spirit",)),
    ("melee skills", ("attack", "melee")),
    ("attack skills", ("attack",)),
    ("to attacks", ("attack",)),
    ("attack speed", ("attack",)),
    ("attack damage", ("attack",)),
    ("accuracy", ("attack",)),
    ("elemental damage with attacks", ("attack", "elemental")),
    ("critical hit chance for attacks", ("attack", "crit")),
    ("spell skills", ("spell",)),
    ("spell damage", ("spell",)),
    ("cast speed", ("spell",)),
    ("critical hit chance for spells", ("spell", "crit")),
    ("projectile", ("projectile",)),
    ("chaos skills", ("chaos",)),
    ("fire skills", ("elemental",)),
    ("cold skills", ("elemental",)),
    ("lightning skills", ("elemental",)),
    ("physical spell skills", ("spell", "physical")),
    ("physical", ("physical",)),
    ("fire", ("elemental",)),
    ("cold", ("elemental",)),
    ("lightning", ("elemental",)),
    ("chaos", ("chaos",)),
    ("elemental", ("elemental",)),
    ("critical", ("crit",)),
    ("maximum life", ("life", "defence")),
    ("energy shield", ("es", "defence")),
    ("evasion", ("evasion", "defence")),
    ("armour", ("armour", "defence")),
    ("deflection", ("defence",)),
    ("resistance", ("resistance", "defence")),
    ("block", ("defence",)),
    ("runic ward", ("defence",)),
    ("spirit", ("spirit",)),
    ("movement speed", ("movement",)),
    ("rarity", ("rarity",)),
    ("mana", ("mana",)),
    ("poison", ("ailment",)),
    ("bleeding", ("ailment",)),
    ("ailments", ("ailment",)),
    ("freeze", ("ailment",)),
    ("shock", ("ailment",)),
    ("damage with bows", ("attack", "projectile")),
    ("damage with bow skills", ("attack", "projectile")),
    ("damage with crossbows", ("attack", "projectile")),
    ("damage with quarterstaves", ("attack", "melee")),
    ("damage with spears", ("attack", "melee")),
    ("damage with maces", ("attack", "melee")),
    ("area of effect", ("aoe",)),
    ("skill effect duration", ("duration",)),
    ("strength", ("attribute",)),
    ("dexterity", ("attribute",)),
    ("intelligence", ("attribute",)),
]


# Whose stat a mod modifies. This is what lets a mod keep BOTH "minion" and
# "attack": "Minions have increased Attack Speed" really is an attack-speed
# mod, it just belongs to the minion. Recording the subject preserves that
# fact while keeping it from stacking coherence with the player's own attack
# mods, which serve a different buyer.
SUBJECT_RULES = [
    ("companion", "companion"),
    ("minions", "minion"),
    ("allies in your presence", "minion"),
    ("spectre", "minion"),
    ("offering", "minion"),
    ("totem", "totem"),
]

# Subjects that define a buyer largely on their own. "self" does not — a
# player wants a specific archetype, not "any mod about me".
BUYER_SUBJECTS = ("minion", "companion", "totem")

# Minion buyers are NOT interchangeable, so subject alone is too coarse.
# Verified against 0.5 build guides:
#   - Companion Spirit Walker wants "+Level of all Minion Skills" and "Allies
#     in your Presence deal increased Damage", but scales mainly off the
#     PLAYER's main-hand weapon via Catha's Balance.
#   - Spectre/Reaver builds prefer flat added physical on the sceptre over a
#     high-rolled "Allies have #% increased Damage".
#   - Attack minions (snipers, reavers, companions) and caster minions
#     (skeleton mages) do not share attack-speed / cast-speed mods.
#
# So a minion mod is either UNIVERSAL (every minion build wants it) or bound to
# a subtype. Universal mods count toward every subtype; subtype mods only
# toward their own.
MINION_SUBTYPES = ("attack", "caster", "companion")

_ATTACK_ONLY = re.compile(r"attack speed|accuracy|attack physical damage|melee", re.I)
_CASTER_ONLY = re.compile(r"cast speed|spell", re.I)


def minion_subtype(text, subject):
    """Which minion buyers a minion mod serves. None means universal."""
    if subject not in BUYER_SUBJECTS:
        return None
    if subject == "companion":
        return "companion"
    lowered = _normalize(text)
    # "Attack and Cast Speed" is one mod serving both, and neither pattern
    # below sees it as such because the words are split.
    if "attack and cast" in lowered:
        return None
    attack = bool(_ATTACK_ONLY.search(lowered))
    caster = bool(_CASTER_ONLY.search(lowered))
    if attack and caster:
        return None          # "Attack and Cast Speed" serves both
    if attack:
        return "attack"
    if caster:
        return "caster"
    return None


# Skills whose NAME does not describe what they are. Verified against the
# PoE2 wikis:
#   Armour Breaker         mace strike — Attack, AoE, Melee, Physical
#   Arctic Armour          a Spirit gem: a cold buff that freezes attackers
#   Armour Piercing Rounds crossbow ammunition — attack, projectile
SKILL_TAGS = {
    "armour breaker": ("attack", "melee", "physical", "aoe"),
    "arctic armour": ("spirit", "elemental"),
    "armour piercing rounds": ("attack", "projectile"),
}


def subject_for(text):
    lowered = _normalize(text)
    for needle, subject in SUBJECT_RULES:
        if needle in lowered:
            return subject
    return "self"


def tags_for(text):
    """Archetypes a mod serves. Empty is allowed and means 'describes nothing
    a buyer selects on' — such mods contribute score but never coherence.

    Tags are NOT stripped by subject. A minion attack-speed mod keeps both
    tags; the subject field is what stops it grouping with the player's own
    attack mods when coherence is computed.
    """
    lowered = _normalize(text)
    tags = set()
    for needle, applied in TAG_RULES:
        if needle in lowered:
            tags.update(applied)

    # Penetration is OFFENSIVE: your damage cutting through the enemy's
    # resistance, not resistance on you. Matching the word "Resistance" filed
    # it as a defence and clustered it with life and resistance rolls.
    if "penetrat" in lowered:
        tags -= {"defence", "resistance"}

    # A skill name is not a defence. "+to Level of all Armour Breaker Skills"
    # and "Arctic Armour Skills" were tagged armour and defence purely for
    # containing the word. Named skills whose name does not describe them get
    # their real tags from SKILL_TAGS.
    if "level of all" in lowered:
        tags -= {"defence", "armour", "es", "evasion", "resistance"}
        for name, applied in SKILL_TAGS.items():
            if name in lowered:
                tags.update(applied)

    # A minion, companion or totem mod is filed under that subject. Carrying
    # the player's defensive tags too put minion life beside your own life
    # when coherence was computed.
    if subject_for(text) != "self":
        tags -= {"defence", "life", "es", "armour", "evasion", "resistance"}
    return sorted(tags)


# Mods that can never be a tradeable affix on a player's item. Excluded before
# any pattern runs, so map/monster/debuff text cannot leak into the allowlist.
NOT_PLAYER_GEAR = re.compile(
    r"^(Allocates|Small Passive|Notable Passive|Players |Player |Monsters? |"
    r"Rare Monsters|Magic Monsters|Map |Area |Waystone|Unique Boss|Enemies |"
    r"Your Maps?|League |Strongbox|Shrines|Chests)|"
    r"( in Map$| per Level$|Monster |to Monsters)",
    re.I,
)
