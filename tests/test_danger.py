"""The mods on a waystone that kill the player, called out on the scan.

A stone's mods are difficulty and never searched, so nothing else on the
report shows them — and the loot score says nothing about whether the map
can be run. Two tiers: deadly, and risky. Everything else on a stone is
survivable by most builds.
"""

from sox import itemtext
from sox.valuation.danger import dangers

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures" / "items"


def stone(*mods):
    return itemtext.parse(
        "Item Class: Waystones\nRarity: Rare\nDoom Route\nWaystone (Tier 15)\n"
        "--------\nItem Rarity: +24% (augmented)\n--------\nItem Level: 80\n"
        "--------\n" + "\n".join(mods) + "\n"
    )


def test_the_deadly_tier():
    item = stone("-8% maximum Player Resistances",
                 "Monster Damage Penetrates 12% Elemental Resistances",
                 "Monsters deal 20% of Damage as Extra Chaos",
                 "Players have 36% less Recovery Rate of Life and Energy Shield",
                 "Players and their Minions deal no damage for 3 out of every 10 seconds",
                 "Players are Marked for Death for 10 seconds after killing a Rare or Unique monster")
    assert [tier for _, tier in dangers(item)] == ["deadly"] * 6


def test_the_risky_tier():
    item = stone("Monsters deal 20% of Damage as Extra Fire",
                 "24% increased Monster Damage",
                 "Monsters have 260% increased Critical Hit Chance",
                 "Monsters have 15% increased Attack, Cast and Movement Speed",
                 "Monsters fire 3 additional Projectiles",
                 "Players are periodically Cursed with Elemental Weakness",
                 "Players have 25% less Cooldown Recovery Rate",
                 "Players gain 33% reduced Flask Charges",
                 "Area has patches of Ignited Ground",
                 "Monsters inflict 1 Grasping Vine on Hit")
    assert [tier for _, tier in dangers(item)] == ["risky"] * 10


def test_survivable_mods_are_not_called_out():
    """The screenshot stone: a speed mod is all that bites."""
    item = itemtext.parse((FIXTURES / "GhostExpedition.txt").read_text())
    assert dangers(item) == (
        ("Monsters have 15% increased Attack, Cast and Movement Speed", "risky"),
    )


def test_gear_is_never_dangerous():
    assert dangers({"itemClass": "Rings", "rarity": "Rare",
                    "explicitMods": ["-8% maximum Player Resistances"]}) == ()
