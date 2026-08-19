"""Parser tests against REAL clipboard captures.

Fixtures in tests/fixtures/items/ are genuine PoE2 item text taken from the
Exiled-Exchange-2 parser test suite, not hand-written approximations.
"""

from pathlib import Path

import pytest

from sox.itemtext import parse, parse_rolls, strip_rolls

FIXTURES = Path(__file__).parent / "fixtures" / "items"


def load(name: str) -> dict:
    return parse((FIXTURES / f"{name}.txt").read_text())


def test_strip_rolls_reduces_advanced_descriptions():
    assert strip_rolls("Adds 5(1-5) to 82(62-89) Lightning Damage") == \
        "Adds 5 to 82 Lightning Damage"
    assert strip_rolls("+57(41-60) to Accuracy Rating") == "+57 to Accuracy Rating"
    assert strip_rolls("15% increased Light Radius") == "15% increased Light Radius"


def test_parse_rolls_extracts_actual_and_range():
    assert parse_rolls("Adds 5(1-5) to 82(62-89) Lightning Damage") == \
        [(5.0, 1.0, 5.0), (82.0, 62.0, 89.0)]


def test_rare_item_header():
    item = load("RareItem")
    assert item["itemClass"] == "Bows"
    assert item["rarity"] == "Rare"
    assert item["name"] == "Oblivion Strike"
    assert item["baseType"] == "Rider Bow"
    assert item["frameType"] == 2
    assert item["ilvl"] == 80


def test_rare_item_mods_are_plain_text():
    item = load("RareItem")
    assert "Adds 5 to 82 Lightning Damage" in item["explicitMods"]
    assert "+57 to Accuracy Rating" in item["explicitMods"]
    # A single modifier can carry several stat lines.
    assert "15% increased Light Radius" in item["explicitMods"]


def test_rare_item_captures_tiers_and_ranges():
    item = load("RareItem")
    assert item["modTiers"]["Adds 5 to 82 Lightning Damage"] == 4
    assert item["modTiers"]["+57 to Accuracy Rating"] == 1
    actual, lo, hi = item["modRanges"]["+57 to Accuracy Rating"]
    assert (actual, lo, hi) == (57.0, 41.0, 60.0)


def test_rare_item_properties():
    item = load("RareItem")
    props = {p["name"]: p["values"][0][0] for p in item["properties"]}
    assert props["Physical Damage"] == "36-61"
    assert props["Critical Hit Chance"] == "5.00%"


def test_normal_item_has_base_but_no_name():
    item = load("NormalItem")
    assert item["rarity"] == "Normal"
    assert item["name"] is None
    assert item["baseType"] == "Superior Divine Crown"
    props = {p["name"]: p["values"][0][0] for p in item["properties"]}
    assert props["Armour"] == "174"        # "(augmented)" stripped
    assert props["Energy Shield"] == "60"


def test_magic_item_single_name_line():
    item = load("MagicItem")
    assert item["rarity"] == "Magic"
    assert item["baseType"] == "Crackling Temple Maul of the Brute"
    assert item["frameType"] == 1


def test_unique_item_and_flavour_text_is_not_a_mod():
    item = load("UniqueItem")
    assert item["rarity"] == "Unique"
    assert item["name"] == "The Eternal Spark"
    assert item["baseType"] == "Crystal Focus"
    assert "56% increased Energy Shield" in item["explicitMods"]
    joined = " ".join(item["explicitMods"])
    assert "stormcloud" not in joined, "flavour text must not be parsed as a mod"


def test_implicit_mods_are_separated():
    item = load("RareWithImplicit")
    assert item["implicitMods"], "expected at least one implicit"
    assert not set(item["implicitMods"]) & set(item["explicitMods"])


def test_fractured_mods_are_separated():
    item = load("FracturedItem")
    assert item["fracturedMods"], "expected a fractured mod"


def test_waystone_parses():
    item = load("RareMap")
    assert item["itemClass"] == "Waystones"
    assert item["ilvl"] >= 1


def test_every_fixture_parses_without_error():
    names = sorted(p.stem for p in FIXTURES.glob("*.txt"))
    assert len(names) >= 20
    for name in names:
        item = load(name)
        assert item["rarity"] is not None, f"{name} has no rarity"


@pytest.mark.parametrize("name", ["UncutSkillGem", "UncutSpiritGem", "UncutSupportGem"])
def test_uncut_gems_expose_level(name):
    """Uncut gems name their level, and the index keys on exactly that string.

    "Uncut Skill Gem (Level 19)" is both the clipboard name and the
    poe2scout key, so no reconstruction is needed.
    """
    item = load(name)
    assert item["gemLevel"] is not None
    assert f"(Level {item['gemLevel']})" in item["baseType"]


def test_rune_and_fractured_markers_route_by_line_not_header():
    """A per-line marker overrides the modifier block it sits in."""
    item = load("FracturedItem")
    assert any("Physical Damage" in m for m in item["runeMods"])
    assert any("Physical Damage" in m for m in item["fracturedMods"])
    joined = " ".join(item["fracturedMods"] + item["runeMods"])
    assert "(fractured)" not in joined and "(rune)" not in joined


def test_trade_note_is_not_a_modifier():
    """A copied listing carries "Note: ~b/o 1 aug" — the seller's price."""
    item = parse(
        "Item Class: Shields\nRarity: Normal\nPolished Targe\n"
        "--------\nBlock chance: 25%\n--------\nItem Level: 54\n"
        "--------\nNote: ~b/o 1 aug\n"
    )
    assert item["note"] == "~b/o 1 aug"
    assert not any("Note:" in m for m in item["explicitMods"])


def test_roll_ranges_are_captured_outside_modifier_blocks():
    """Desecrated and rune mods print their range inline, with no header.

    Without the range they carry no roll quality, which decides both the roll
    score and which mods survive when a search widens.
    """
    item = parse(
        "Item Class: Quarterstaves\nRarity: Rare\nX\nBolting Quarterstaff\n"
        "--------\nItem Level: 81\n--------\n"
        "98(78-118)% increased Cold Damage (desecrated)\n"
        "16(12-20)% increased Freeze Buildup (desecrated)\n"
    )
    assert item["modRanges"]["98% increased Cold Damage"] == (98.0, 78.0, 118.0)
    assert "98% increased Cold Damage" in item["desecratedMods"]
