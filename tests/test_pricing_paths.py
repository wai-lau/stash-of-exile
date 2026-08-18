"""Regression tests for bugs found by running against the live API."""

from pathlib import Path

from sox import itemtext
from sox.scout import IndexEntry
from sox.valuation import candidates
from sox.valuation.allowlists import load_bases, load_mods, load_notables, load_uniques
from sox.valuation.classify import ItemClass, classify
from sox.valuation.mods import build_index
from sox.valuation.query import RELAX_STEPS, build_query, category_for

FIXTURES = Path(__file__).parent / "fixtures" / "items"
MODS = build_index(load_mods())
BASES, UNIQUES, NOTABLES = load_bases(), load_uniques(), load_notables()


def load(name):
    return itemtext.parse((FIXTURES / f"{name}.txt").read_text())


def test_notables_use_the_enchant_group():
    """Verified live: explicit.* returns 0 results, enchant.* returns matches.

    The same numeric id exists under explicit, crafted and enchant; only the
    enchant one matches listed jewels.
    """
    assert NOTABLES, "notable table must not be empty"
    assert all(v.startswith("enchant.stat_") for v in NOTABLES.values())


def test_megalomaniac_escalates_on_notables():
    item = load("MegalomaniacJewel")
    entry = IndexEntry("Megalomaniac", 1.0, 24992, {})
    verdict = candidates.assess(item, entry, MODS, BASES, UNIQUES)
    assert verdict.should_search, "index says 1ex; the notables are the value"
    assert verdict.reason == "notable"


def test_notable_query_uses_exact_ids_and_no_minimum():
    item = load("MegalomaniacJewel")
    query = build_query(item, category_for(item), MODS, NOTABLES)
    filters = query["query"]["stats"][0]["filters"]
    assert filters, "expected notable filters"
    assert all(f["id"].startswith("enchant.stat_") for f in filters)
    assert all(f["value"] == {} for f in filters), "notables have no numeric min"
    assert query["query"]["name"] == "Megalomaniac"


def test_ladder_trims_notables_last_and_eventually_to_one():
    """An exact notable PAIR is often unlisted while one notable is not."""
    item = load("MegalomaniacJewel")
    counts = []
    for step in range(len(RELAX_STEPS)):
        q = build_query(item, category_for(item), MODS, NOTABLES, relax=step)
        counts.append(len(q["query"]["stats"][0]["filters"]))
    assert counts[0] == 2
    assert counts[-1] == 1, "the last rung must drop to a single notable"


def test_query_constrains_only_the_top_weight_mods():
    """Constraining on every matched mod returns nothing.

    The rare bow carries fire AND cold AND lightning AND accuracy; demanding
    all four at once describes one item in the world.
    """
    item = load("RareItem")
    query = build_query(item, category_for(item), MODS, NOTABLES)
    assert len(query["query"]["stats"][0]["filters"]) <= 3


def test_category_comes_from_item_class():
    assert category_for(load("RareItem")) == "weapon.bow"
    assert category_for(load("RareMap")) == "map.waystone"
    assert category_for(load("MegalomaniacJewel")) == "jewel"


def test_endgame_classes_always_search_since_no_index_covers_them():
    for name in ("RareMap", "CharmQuality", "TwoLineOneImplicitItem"):
        item = load(name)
        assert classify(item) is ItemClass.ENDGAME, name
        verdict = candidates.assess(item, None, MODS, BASES, UNIQUES)
        assert verdict.should_search, f"{name} has no index price and must be searched"


def test_uncut_gem_is_a_gem_not_currency():
    """Uncut gems report Rarity: Currency but price as gems, keyed by level."""
    item = load("UncutSkillGem")
    assert classify(item) is ItemClass.GEM
    assert item["gemLevel"] == 19


def test_selection_prefers_synergy_over_raw_weight():
    """The judgement a price-check overlay leaves to the player.

    An amulet carrying both caster and attack mods must be searched as ONE
    archetype; picking the heaviest mods regardless would mix Cast Speed with
    Attack Speed and describe a buyer who does not exist.
    """
    from sox.valuation.mods import matched, select_synergistic

    item = itemtext.parse(
        "Item Class: Amulets\nRarity: Rare\nBrood Collar\nLapis Amulet\n"
        "--------\nItem Level: 82\n--------\n"
        "{ Prefix Modifier }\n+3 to Level of all Spell Skills\n"
        "{ Prefix Modifier }\n25% increased Cast Speed\n"
        "{ Suffix Modifier }\n18% increased Attack Speed\n"
        "{ Suffix Modifier }\n40% increased Spell Damage\n"
    )
    entries = matched(item["explicitMods"], MODS)
    chosen, group = select_synergistic(entries, 3)
    texts = [e.text for e in chosen]

    assert group == "spell"
    assert "#% increased Attack Speed" not in texts, "must not mix buyer pools"
    assert "#% increased Spell Damage" in texts


def test_explain_selection_reports_notables_for_jewels():
    from sox.valuation.query import explain_selection

    group, stats = explain_selection(load("MegalomaniacJewel"), MODS, NOTABLES)
    assert group == "notable"
    assert all(s.startswith("Allocates ") for s in stats)
