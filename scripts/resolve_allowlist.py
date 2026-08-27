#!/usr/bin/env python3
"""Resolve the researched mod allowlist against the live trade2 stats table.

Every allowlist entry must map to a real stat id. Anything that does not
resolve is reported loudly rather than silently dropped, so the allowlist
can never ship a stat id that the trade API will reject.

Usage:
    curl -A '<ua>' https://www.pathofexile.com/api/trade2/data/stats -o stats.json
    python3 scripts/resolve_allowlist.py stats.json > src/sox/data/mod_allowlist.toml
"""

import json
import re
import sys
from collections import OrderedDict

# (weight, canonical mod text, note)
# weight 3 = build-defining, alone justifies a search
# weight 2 = strong, counts heavily toward the threshold
# weight 1 = supporting; only matters in combination
ALLOWLIST = OrderedDict(
    [
        (
            "defence_core",
            [
                (3, "# to maximum Life", "universal after the 0.5 ES nerfs"),
                (2, "#% increased maximum Life", None),
                # Local defences are NOT listed here. Flat and percent Armour
                # and Evasion are already folded into the item's displayed
                # total, and that total is searched through equipment_filters
                # instead. Listing them as stats too would constrain the same
                # thing twice, and on the wrong stat id.
                #
                # Flat Energy Shield is the exception, because it is not only
                # local. On an amulet — a Lunar Amulet's implicit, a rare's
                # prefix — it is a GLOBAL stat with no total to ride, and
                # unlisted it could not be searched at all: a Rondel of
                # Fragility reported its implicit as unsearchable. On armour
                # the query builder already folds it into the ES total, and
                # the local twin below resolves the id for the score.
                (1, "# to maximum Energy Shield", "global on jewellery, local on armour"),
            ],
        ),
        (
            "deflection",
            [
                (3, "Gain Deflection Rating equal to #% of Evasion Rating", "top chest mod"),
                (3, "Gain Deflection Rating equal to #% of Armour", None),
                (2, "#% increased Deflection Rating", None),
            ],
        ),
        (
            "mitigation",
            [
                (3, "#% of Armour also applies to Elemental Damage", "armour-stacker enabler"),
                (3, "#% of Armour also applies to Chaos Damage", None),
                # Block and Runic Ward are local too: the item shows a total
                # that already includes them, searched via equipment_filters.
            ],
        ),
        (
            "spirit",
            [
                (3, "# to Spirit", "gates every buff/minion build"),
                (3, "#% increased Spirit", None),
                (3, "#% increased Spirit Reservation Efficiency of Skills", None),
                (2, "#% increased Reservation Efficiency of Minion Skills", None),
                (2, "#% increased Reservation Efficiency of Herald Skills", None),
            ],
        ),
        (
            "resistances",
            [
                # No item ever prints "total Elemental Resistance" — that is a
                # search-only aggregate. The totals are computed from the
                # item's own resistance mods and searched as pseudo stats;
                # listing them here could never match anything.
                (1, "#% to Fire Resistance", None),
                (1, "#% to Cold Resistance", None),
                (1, "#% to Lightning Resistance", None),
                (1, "#% to all Elemental Resistances", None),
                # Chaos resistance is scarcer than elemental and was missing
                # entirely; only the maximum-res version was listed.
                (2, "#% to Chaos Resistance", None),
                (3, "#% to Maximum Fire Resistance", "max-res is rare and pricey"),
                (3, "#% to Maximum Cold Resistance", None),
                (3, "#% to Maximum Lightning Resistance", None),
                (3, "#% to Maximum Chaos Resistance", None),
            ],
        ),
        (
            "recovery",
            [
                (2, "#% faster start of Energy Shield Recharge", None),
                (2, "#% increased Energy Shield Recharge Rate", None),
                (2, "Recover #% of maximum Mana on Kill", "near-mandatory sustain"),
                (2, "Recover #% of maximum Life on Kill", None),
                (2, "Leeches #% of Physical Damage as Mana", None),
                (2, "Leeches #% of Physical Damage as Life", None),
                (1, "#% increased Mana Regeneration Rate", None),
            ],
        ),
        (
            "skill_levels",
            [
                (3, "# to Level of all Melee Skills", "best single scaler on jewellery"),
                (3, "# to Level of all Projectile Skills", None),
                (3, "# to Level of all Spell Skills", None),
                (3, "# to Level of all Minion Skills", None),
                (3, "# to Level of all Chaos Skills", None),
                (3, "# to Level of all Fire Skills", None),
                (3, "# to Level of all Cold Skills", None),
                (3, "# to Level of all Lightning Skills", None),
                (3, "# to Level of all Physical Spell Skills", None),
                (3, "# to Level of all Attack Skills", None),
                (3, "+# to Level of all Tamed Companion Skills", None),
                (3, "+# to Level of all Summon Spectre Skills", None),
            ],
        ),
        (
            "added_damage",
            [
                (3, "Adds # to # Physical Damage", None),
                (3, "Adds # to # Fire Damage", None),
                (3, "Adds # to # Cold Damage", None),
                (3, "Adds # to # Lightning Damage", None),
                # NB: GGG capitalizes this one differently from the elemental
                # variants below ("Damage" vs "damage"). Verified, not a typo.
                (2, "Adds # to # Physical Damage to Attacks", None),
                (2, "Adds # to # Fire damage to Attacks", None),
                (2, "Adds # to # Cold damage to Attacks", None),
                (2, "Adds # to # Lightning damage to Attacks", None),
            ],
        ),
        (
            "damage_scaling",
            [
                (3, "#% increased Physical Damage", None),
                # Per-element damage was missing while the generic Elemental
                # and Chaos versions were present. On an element-focused
                # weapon this is the strongest mod on the item.
                (3, "#% increased Fire Damage", None),
                (3, "#% increased Cold Damage", None),
                (3, "#% increased Lightning Damage", None),
                (2, "#% increased Melee Damage", None),
                (2, "#% increased Spell Physical Damage", None),
                (2, "#% increased Spell Damage", None),
                (2, "#% increased Elemental Damage with Attacks", None),
                (2, "#% increased Global Physical Damage", None),
                (2, "Gain #% of Damage as Extra Fire Damage", None),
                (2, "Gain #% of Damage as Extra Cold Damage", None),
                (2, "Gain #% of Damage as Extra Lightning Damage", None),
                (2, "Gain #% of Damage as Extra Chaos Damage", None),
                (1, "#% increased Projectile Damage", None),
            ],
        ),
        (
            "crit_and_speed",
            [
                (3, "#% increased Critical Hit Chance", None),
                (3, "#% increased Critical Damage Bonus", None),
                (2, "#% increased Attack Speed", None),
                (2, "#% increased Cast Speed", None),
                (2, "#% increased Critical Hit Chance for Attacks", None),
                (2, "#% increased Critical Hit Chance for Spells", None),
                (1, "# to Accuracy Rating", None),
                (1, "#% increased Projectile Speed", "Twister meta scaler"),
                (1, "#% increased Area of Effect", None),
            ],
        ),
        (
            "minions",
            [
                (2, "Minions deal #% increased Damage", None),
                (2, "Minions deal #% increased Damage if you've Hit Recently",
                 "conditional, but hitting is the minion player's normal loop"),
                (2, "Minions have #% increased Attack and Cast Speed", None),
                (2, "Minions have #% increased Critical Hit Chance", None),
                (2, "Minions have #% increased Critical Damage Bonus", None),
                (2, "Minions have #% increased maximum Life", "minion defensive core"),
                (2, "Minions deal #% increased Damage with Command Skills", None),
                (2, "Minions gain #% of their maximum Life as Extra maximum Energy Shield", None),
                (1, "Minions have #% increased Area of Effect", None),
                (1, "Minions have #% increased Movement Speed", None),
                (1, "Minions have #% additional Physical Damage Reduction", None),
                (1, "Minions Regenerate #% of maximum Life per second", None),
                (1, "Minions have #% to Chaos Resistance", None),
                (1, "Minions have #% to all Elemental Resistances",
                 "Disciple of Varashta guides warn this conflicts with their build"),
                # "Allies in your Presence" is PoE2's ally-scaling family and is
                # how Tactician scales its whole build. Same buyers as minions.
                (2, "Allies in your Presence deal #% increased Damage", None),
                (2, "Allies in your Presence deal # to # added Attack Physical Damage", None),
                (2, "Allies in your Presence have #% increased Attack Speed", None),
                (2, "Allies in your Presence have #% increased Cast Speed", None),
                (2, "Allies in your Presence have #% increased Critical Hit Chance", None),
                (2, "Allies in your Presence have #% increased Critical Damage Bonus", None),
                (1, "Allies in your Presence have # to Accuracy Rating", None),
                (1, "Allies in your Presence have #% to all Elemental Resistances", None),
                # Companions are minions and share the same buyers — the
                # Companion Spirit Walker build scales entirely on these.
                # Only mods whose SUBJECT is the companion are listed; the
                # "while your Companion is in your Presence" family scales the
                # PLAYER and must not be tagged as a minion mod.
                (2, "Companions deal #% increased Damage", None),
                (2, "Companions have #% increased maximum Life", None),
                (2, "Companions have #% increased Attack Speed", None),
                (1, "Companions deal #% increased damage to your Marked targets", None),
                (2, "#% increased Reservation Efficiency of Companion Skills", None),
                # Spectres and offerings are minion scaling too.
                (2, "Offerings have #% increased Maximum Life", None),
                (2, "Offering Skills have #% increased Buff effect", None),
                (1, "Offering Skills have #% increased Duration", None),
            ],
        ),
        (
            "utility",
            [
                (3, "#% increased Movement Speed", "boots gate"),
                (2, "#% increased Rarity of Items found", "150%+ IIR target"),
                (2, "#% increased Mana Cost Efficiency", None),
                (2, "# to maximum Mana", None),
                (1, "#% increased Skill Effect Duration", None),
                (1, "# to Strength", None),
                (1, "# to Dexterity", None),
                (1, "# to Intelligence", None),
            ],
        ),
        (
            # Jewels are pure mod items — no defences, no meaningful base — so
            # weapon-scoped and generic damage mods carry their whole value.
            "jewel_damage",
            [
                (2, "#% increased Damage", None),
                (2, "#% increased Attack Damage", None),
                (2, "#% increased Elemental Damage", None),
                (2, "#% increased Chaos Damage", None),
                (2, "#% increased Damage with Bows", "weapon-scoped jewel staple"),
                (2, "#% increased Damage with Bow Skills", None),
                (2, "#% increased Damage with Crossbows", None),
                (2, "#% increased Damage with Quarterstaves", None),
                (2, "#% increased Damage with Spears", None),
                (2, "#% increased Damage with Maces", None),
                (2, "#% increased Damage with Hits against Rare and Unique Enemies", None),
            ],
        ),
        (
            "totems",
            [
                (2, "#% increased Totem Damage", "Warbringer / spell-totem builds"),
                (2, "#% increased Totem Life", None),
                (2, "# to maximum number of Summoned Totems", None),
                (1, "#% increased Totem Placement speed", None),
            ],
        ),
        (
            "ailments",
            [
                (2, "#% increased Magnitude of Poison you inflict", None),
                (2, "#% increased Magnitude of Bleeding you inflict", None),
                (1, "#% increased Magnitude of Ailments you inflict", None),
                (1, "#% increased Freeze Buildup", None),
                (1, "#% increased chance to Shock", None),
            ],
        ),
    ]
)

# Prefer a pseudo stat when one exists — this is what the trade site does.
PSEUDO_PREFERRED = ("pseudo", "explicit")


def normalize(text):
    """Fold the ways GGG's mod text varies without changing meaning.

    Real variance observed in the live table:
      "Adds # to # Physical Damage to Attacks"  (capital D)
      "Adds # to # Fire damage to Attacks"      (lowercase d)
      "+#% to Fire Resistance" vs "#% to Fire Resistance"  (leading plus)

    Measured against the live table, this normalization introduces ZERO new
    ambiguity: no normalized key maps to more than one raw spelling in the
    pseudo or explicit groups. So it is safe to match on, and it means a
    future patch that merely re-cases or re-pluses a mod cannot break us.
    """
    text = text.casefold()
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<![a-z0-9])\+(?=#)", "", text)
    return text


def slugify(text):
    """A stable key derived from OUR canonical text, not GGG's.

    This is the lock key. Because it comes from text we control, it does not
    move when GGG re-words a mod — which is what makes id reuse possible.
    """
    return re.sub(r"[^a-z0-9]+", "_", normalize(text)).strip("_")



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
    lowered = normalize(text)
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
    lowered = normalize(text)
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
    lowered = normalize(text)
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


# --- Systematic coverage ------------------------------------------------
#
# Hand-listing every mod does not scale: the live table holds ~1,950 plausible
# gear/jewel affixes and 250 skill-level variants alone. These patterns expand
# against the live table so whole families are covered and stay covered when
# GGG adds to them.

# Mods that can never be a tradeable affix on a player's item. Excluded before
# any pattern runs, so map/monster/debuff text cannot leak into the allowlist.
NOT_PLAYER_GEAR = re.compile(
    r"^(Allocates|Small Passive|Notable Passive|Players |Player |Monsters? |"
    r"Rare Monsters|Magic Monsters|Map |Area |Waystone|Unique Boss|Enemies |"
    r"Your Maps?|League |Strongbox|Shrines|Chests)|"
    r"( in Map$| per Level$|Monster |to Monsters)",
    re.I,
)

# (regex, weight, note). First match wins.
PATTERN_RULES = [
    (r"^\+?# to Level of all .+ Skills$", 3, "skill levels are the top scaler"),
    (r"^Damage Penetrates #% .*Resistances?$", 3, None),
    (r"^Bow Attacks fire # additional Arrows?$", 3, None),
    (r"^Skills fire # additional Projectiles?$", 3, None),
    (r"^#% increased Damage with .+$", 2, None),
    (r"^Leech(es)? #% of .+ as (Life|Mana)$", 2, None),
    (r"^#% chance to (Pierce|Chain|Fork) .+$", 2, None),
    (r"^Projectiles? (Pierce|Chain|Fork) .+$", 2, None),
    (r"^#% chance to gain a? ?(Frenzy|Power|Endurance) Charge.*$", 2, None),
    (r"^# to (maximum )?(Frenzy|Power|Endurance) Charges$", 2, None),
    (r"^#% increased Skill Speed$", 2, None),
    (r"^\+?# to all Attributes$", 2, None),
    (r"^#% increased Presence Area of Effect$", 2, None),
    (r"^Herald Skills deal #% increased Damage$", 2, None),
    (r"^#% increased Buff Effect .*$", 2, None),
    (r"^# Life Regeneration per second$", 1, None),
    (r"^#% of maximum Life Regenerated per second$", 1, None),
    (r"^# to Stun Threshold$", 1, None),
    (r"^#% increased Stun Buildup$", 1, None),
    (r"^#% increased Flask Charges gained$", 1, None),
    (r"^#% increased Charm Charges gained$", 1, None),
]


def expand_patterns(stats):
    """Yield (category, weight, text, note) for every pattern match in the
    live table that is not already hand-listed."""
    listed = {text for mods in ALLOWLIST.values() for _, text, _ in mods}
    seen = set()
    for group in stats["result"]:
        if group["id"] != "explicit":
            continue
        for entry in group["entries"]:
            text = entry["text"]
            if text in listed or text in seen or NOT_PLAYER_GEAR.search(text):
                continue
            for pattern, weight, note in PATTERN_RULES:
                if re.match(pattern, text):
                    seen.add(text)
                    yield "expanded", weight, text, note
                    break


# Eight stats exist twice: once as a LOCAL mod on the item that provides the
# stat, once as a GLOBAL mod. "+145 to Evasion Rating" on a helmet is the
# local one; the same text on an amulet is the global one. The clipboard shows
# identical text for both, so the item's category has to decide — searching
# the global id for a helmet mod matches nothing, which is how a 20ex helmet
# priced at 0.2ex.
LOCAL_SUFFIX = " (Local)"


def local_variants(stats):
    """Canonical text -> local stat id, for stats that have a local twin."""
    out = {}
    for group in stats["result"]:
        if group["id"] != "explicit":
            continue
        for entry in group["entries"]:
            if entry["text"].endswith(LOCAL_SUFFIX):
                out[entry["text"][: -len(LOCAL_SUFFIX)]] = entry["id"]
    return out


def implicit_variants(stats):
    """Numeric stat id -> implicit id, for stats an item can roll as implicit.

    An implicit is a real mod on the item and belongs in the search, but the
    trade API files it under its own group: `implicit.stat_3299347043` and
    `explicit.stat_3299347043` are the same stat reached two ways, and asking
    the explicit one about an implicit returns nothing. Only 178 of the 3031
    explicit stats have an implicit twin, so the map has to be exact.
    """
    out = {}
    for group in stats["result"]:
        if group["id"] != "implicit":
            continue
        for entry in group["entries"]:
            out[entry["id"].split(".", 1)[-1]] = entry["id"]
    return out


def build_index(stats):
    """normalized text -> [(group, id)] in table order."""
    idx = {}
    for group in stats["result"]:
        gid = group["id"]
        for entry in group["entries"]:
            idx.setdefault(normalize(entry["text"]), []).append((gid, entry["id"]))
    return idx


def load_lock(path):
    """slug -> [ids] from a previously generated file, if present."""
    lock = {}
    try:
        content = open(path).read()
    except OSError:
        return lock
    for block in content.split("[[category.mod]]")[1:]:
        slug = re.search(r'^slug = "(.+)"$', block, re.M)
        ids = re.search(r"^ids = \[(.+)\]$", block, re.M)
        if slug and ids:
            lock[slug.group(1)] = re.findall(r'"([^"]+)"', ids.group(1))
    return lock


def resolve(text, idx, lock, valid_ids):
    """Resolve to (ids, how). Ambiguity is preserved, never silently picked.

    Resolution order:
      1. Previously locked ids that still exist   -> immune to text rewording
      2. Normalized text match                    -> immune to case/plus churn
      3. Nothing                                  -> caller reports loudly
    """
    slug = slugify(text)
    locked = [i for i in lock.get(slug, []) if i in valid_ids]
    if locked and len(locked) == len(lock.get(slug, [])):
        return locked, "locked"

    hits = idx.get(normalize(text))
    if not hits:
        return None, "unresolved"

    for preferred in PSEUDO_PREFERRED:
        chosen = [sid for gid, sid in hits if gid == preferred]
        if chosen:
            # Several ids can share one text (e.g. "# to Spirit" has two).
            # Keep them all: the query emits an OR group rather than guessing.
            return chosen, "matched"

    # Deliberately NO fallback to other groups. The same mod text also exists
    # under `fractured`, `crafted`, `desecrated` and friends, and those match
    # only items carrying that mod *as* fractured/crafted/desecrated. Silently
    # substituting one would skew every search built from it, so an explicit
    # or pseudo stat is required — otherwise this is unresolved and fails.
    return None, "unresolved"


def main():
    stats = json.load(open(sys.argv[1]))
    idx = build_index(stats)
    locals_by_text = local_variants(stats)
    implicits_by_id = implicit_variants(stats)
    valid_ids = {e["id"] for g in stats["result"] for e in g["entries"]}

    lock_path = sys.argv[2] if len(sys.argv) > 2 else "src/sox/data/mod_allowlist.toml"
    lock = load_lock(lock_path)

    counts = {"locked": 0, "matched": 0}
    missing, ambiguous = [], []
    out = [
        "# Default mod allowlist for sox.",
        "# GENERATED by scripts/resolve_allowlist.py — every id verified against",
        "# the live trade2 stats table. Do not hand-edit stat ids.",
        "#",
        "# weight 3 = build-defining (justifies a search alone)",
        "# weight 2 = strong",
        "# weight 1 = supporting (only matters in combination)",
        "#",
        "# `slug` is derived from OUR canonical text and is the stable lock key:",
        "# on regeneration an id that still exists is reused, so GGG rewording a",
        "# mod cannot silently drop it. Matching is also case/whitespace/plus",
        "# insensitive, so re-casing cannot break it either.",
        "#",
        "# `ids` is a LIST because several stat ids can share one mod text",
        "# (e.g. \"# to Spirit\" has two). Those become an OR group in the query",
        "# rather than an arbitrary pick.",
        "",
    ]

    # Hand-listed mods first, then whole families expanded from the live table.
    grouped = OrderedDict((name, list(mods)) for name, mods in ALLOWLIST.items())
    for category, weight, text, note in expand_patterns(stats):
        grouped.setdefault(category, []).append((weight, text, note))

    for category, mods in grouped.items():
        out.append(f"[[category]]")
        out.append(f'name = "{category}"')
        out.append("")
        for weight, text, note in mods:
            ids, how = resolve(text, idx, lock, valid_ids)
            if ids is None:
                missing.append((category, text))
                continue
            counts[how] = counts.get(how, 0) + 1
            # Ambiguity is a property of the ids, not of how we found them —
            # a locked entry with two ids is still ambiguous and must say so.
            is_ambiguous = len(ids) > 1
            if is_ambiguous:
                ambiguous.append((text, ids))
            rendered = ", ".join(f'"{i}"' for i in ids)
            out.append("[[category.mod]]")
            out.append(f"ids = [{rendered}]")
            out.append(f'slug = "{slugify(text)}"')
            out.append(f'text = "{text}"')
            out.append(f"weight = {weight}")
            local = locals_by_text.get(text)
            if local:
                out.append(f'local_ids = ["{local}"]')
            implicits = [implicits_by_id[n] for n in
                         (i.split(".", 1)[-1] for i in ids)
                         if n in implicits_by_id]
            if implicits:
                rendered_implicit = ", ".join(f'"{i}"' for i in implicits)
                out.append(f"implicit_ids = [{rendered_implicit}]")
            tags = tags_for(text)
            if tags:
                out.append("tags = [" + ", ".join(f'"{t}"' for t in tags) + "]")
            subject = subject_for(text)
            if subject != "self":
                out.append(f'subject = "{subject}"')
                subtype = minion_subtype(text, subject)
                if subtype:
                    out.append(f'minion_subtype = "{subtype}"')
            if is_ambiguous:
                out.append("ambiguous = true  # matched by OR across these ids")
            if note:
                out.append(f'note = "{note}"')
            out.append("")

    print("\n".join(out))

    total = sum(counts.values()) + len(missing)
    print(
        f"# resolved {sum(counts.values())}/{total} mods "
        f"(locked {counts['locked']}, matched {counts['matched']}; "
        f"{len(ambiguous)} ambiguous)",
        file=sys.stderr,
    )
    for text, ids in ambiguous:
        print(f"#   AMBIGUOUS {text!r} -> OR({', '.join(ids)})", file=sys.stderr)
    if missing:
        print("# UNRESOLVED (no match in the live stats table):", file=sys.stderr)
        for category, text in missing:
            print(f"#   [{category}] {text}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
