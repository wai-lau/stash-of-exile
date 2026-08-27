"""Archetype tagging is one set of rules, shared by the allowlist generator
and the runtime.

The rules used to live only in scripts/resolve_allowlist.py, so a mod
resolved at runtime from GGG's table carried no tags: "24% increased Global
Armour, Evasion and Energy Shield" was searchable but cohered with nothing,
and sat in the unrelated bin beside the evasion roll it plainly belongs
with — which of the two survived widening came down to roll percentile.
"""

from sox.valuation.tagging import minion_subtype, subject_for, tags_for


def test_a_hybrid_defence_mod_carries_every_defence_it_names():
    assert tags_for("#% increased Global Armour, Evasion and Energy Shield") == [
        "armour", "defence", "es", "evasion",
    ]


def test_penetration_is_offence_not_resistance():
    assert "defence" not in tags_for("Damage Penetrates #% Fire Resistance")
    assert "resistance" not in tags_for("Damage Penetrates #% Fire Resistance")


def test_a_skill_name_is_not_a_defence():
    tags = tags_for("+# to Level of all Armour Breaker Skills")
    assert "armour" not in tags and "defence" not in tags
    assert {"attack", "melee"} <= set(tags)


def test_a_minion_mod_keeps_attack_but_not_the_players_defences():
    text = "Minions have #% increased Attack Speed"
    assert subject_for(text) == "minion"
    assert "attack" in tags_for(text)
    assert "defence" not in tags_for("Minions have #% increased maximum Life")
    assert minion_subtype(text, "minion") == "attack"
    assert minion_subtype("Minions have #% increased Attack and Cast Speed", "minion") is None


def test_item_text_with_numbers_tags_like_the_template():
    """The runtime hands over the item's own wording, numbers and all."""
    assert tags_for("37% increased Evasion Rating") == tags_for("#% increased Evasion Rating")
