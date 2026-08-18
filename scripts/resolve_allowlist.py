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
                (2, "# to maximum Energy Shield", "still core for ES builds"),
                (2, "#% increased maximum Life", None),
                (2, "#% increased Energy Shield", None),
                (1, "#% increased Evasion Rating", None),
                (1, "#% increased Armour", None),
                (2, "#% increased Evasion and Energy Shield", "hybrid bases"),
                (2, "#% increased Armour and Energy Shield", None),
                (2, "#% increased Armour and Evasion", None),
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
                (2, "#% increased Block chance", None),
                (2, "# to maximum Runic Ward", None),
                (1, "#% increased maximum Runic Ward", None),
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
                (2, "+#% total Elemental Resistance", "pseudo; triple-res suffixes"),
                (2, "+#% total to Chaos Resistance", None),
                (1, "#% to Fire Resistance", None),
                (1, "#% to Cold Resistance", None),
                (1, "#% to Lightning Resistance", None),
                (1, "#% to all Elemental Resistances", None),
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
                (2, "Minions have #% increased Attack and Cast Speed", None),
                (2, "Minions have #% increased Critical Hit Chance", None),
                (2, "Minions have #% increased Critical Damage Bonus", None),
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


def build_index(stats):
    """text -> [(group, id)], preserving group priority order."""
    idx = {}
    for group in stats["result"]:
        gid = group["id"]
        for entry in group["entries"]:
            idx.setdefault(entry["text"], []).append((gid, entry["id"]))
    return idx


def resolve(text, idx):
    """Return (stat_id, group) preferring pseudo over explicit. None if absent."""
    hits = idx.get(text)
    if not hits:
        return None
    for preferred in PSEUDO_PREFERRED:
        for gid, sid in hits:
            if gid == preferred:
                return sid, gid
    return hits[0][1], hits[0][0]


def main():
    stats = json.load(open(sys.argv[1]))
    idx = build_index(stats)

    resolved = 0
    missing = []
    out = [
        "# Default mod allowlist for sox.",
        "# GENERATED by scripts/resolve_allowlist.py — every id verified against",
        "# the live trade2 stats table. Do not hand-edit stat ids.",
        "#",
        "# weight 3 = build-defining (justifies a search alone)",
        "# weight 2 = strong",
        "# weight 1 = supporting (only matters in combination)",
        "",
    ]

    for category, mods in ALLOWLIST.items():
        out.append(f"[[category]]")
        out.append(f'name = "{category}"')
        out.append("")
        for weight, text, note in mods:
            hit = resolve(text, idx)
            if hit is None:
                missing.append((category, text))
                continue
            sid, group = hit
            resolved += 1
            out.append("[[category.mod]]")
            out.append(f'id = "{sid}"')  # already group-qualified, e.g. explicit.stat_123
            out.append(f'text = "{text}"')
            out.append(f"weight = {weight}")
            if note:
                out.append(f'note = "{note}"')
            out.append("")

    print("\n".join(out))

    total = resolved + len(missing)
    print(f"# resolved {resolved}/{total} mods", file=sys.stderr)
    if missing:
        print("# UNRESOLVED (text did not match the live stats table):", file=sys.stderr)
        for category, text in missing:
            print(f"#   [{category}] {text}", file=sys.stderr)


if __name__ == "__main__":
    main()
