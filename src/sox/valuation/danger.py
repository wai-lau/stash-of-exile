"""The waystone mods that kill the player.

A stone's mods are difficulty and never searched, so nothing else on the
report shows them — and the loot score says nothing about whether the map
can be run. Two tiers, from the 0.5 mod table and what the guides agree
bricks a map. Everything else on a stone most builds shrug off.

deadly: uncapped by the map itself, or a hit you cannot answer
    -#% maximum Player Resistances
    Monster Damage Penetrates #% Elemental Resistances
    Monsters deal #% of Damage as Extra Chaos
    Players have #% less Recovery Rate of Life and Energy Shield
    desecrated: no damage for 3 of every 10 seconds; Marked for Death
risky: more damage, faster, or harder to recover from
    extra Fire/Cold/Lightning, increased Monster Damage, crit, speed,
    additional projectiles, the three curses, less Cooldown Recovery,
    reduced Flask Charges, ground patches, Grasping Vine, Mana Siphon
"""

from __future__ import annotations

import re

from sox.valuation.query import category_for

DEADLY = [re.compile(p, re.I) for p in (
    r"maximum Player Resistances",
    r"Monster Damage Penetrates",
    r"Damage as Extra Chaos",
    r"less Recovery Rate of Life",
    r"deal no damage for",
    r"Marked for Death",
)]

RISKY = [re.compile(p, re.I) for p in (
    r"Damage as Extra (Fire|Cold|Lightning)",
    r"increased Monster Damage",
    r"increased Critical Hit Chance",
    r"Attack, Cast and Movement Speed",
    r"additional Projectiles",
    r"periodically Cursed",
    r"less Cooldown Recovery",
    r"reduced Flask Charges",
    r"patches of (Ignited|Shocked|Chilled) Ground",
    r"Grasping Vine",
    r"Mana Siphoning",
)]


def dangers(item: dict) -> tuple[tuple[str, str], ...]:
    """(mod text, tier) for every killer mod on a waystone, deadly first."""
    if category_for(item) != "map.waystone":
        return ()
    mods = (list(item.get("explicitMods") or []) + list(item.get("implicitMods") or [])
            + list(item.get("desecratedMods") or []))
    deadly = [(text, "deadly") for text in mods if any(p.search(text) for p in DEADLY)]
    risky = [(text, "risky") for text in mods
             if not any(p.search(text) for p in DEADLY)
             and any(p.search(text) for p in RISKY)]
    return tuple(deadly + risky)
