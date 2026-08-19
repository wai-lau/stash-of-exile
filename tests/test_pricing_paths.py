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


def test_clipboard_detects_item_text_and_ignores_prose():
    from sox.clipboard import looks_like_item

    assert looks_like_item((FIXTURES / "RareItem.txt").read_text())
    assert looks_like_item("Rarity: Unique\nMageblood\nUtility Belt")
    assert not looks_like_item("just some text I copied from a wiki")
    assert not looks_like_item("")


def test_watch_session_tracks_totals_and_best():
    from sox.watch import Session

    s = Session()
    s.record("Cheap Thing", 10.0, searches=1)
    s.record("Mageblood", 135416.0, searches=2)
    s.record("Unpriceable", None, searches=3)
    assert s.priced == 2 and s.unpriced == 1
    assert s.searches == 6
    assert s.best_name == "Mageblood"
    assert round(s.total_ex) == 135426


def test_unrevealed_desecrated_mods_are_useless_but_occupy_slots():
    """Unrevealed mods have no known stat, so they are worth nothing.

    They are not free either: they hold an affix slot a buyer would otherwise
    craft into, so they must reduce the open-affix bonus like a junk mod.
    """
    from sox.valuation.candidates import item_mods, used_affixes

    text = (
        "Item Class: Body Armours\nRarity: Rare\nDoom Guardian\nVaal Cuirass\n"
        "--------\nItem Level: 82\n--------\n"
        '{ Prefix Modifier "Athlete\'s" (Tier: 1) }\n+96(90-99) to maximum Life\n'
        "{ Desecrated Prefix Modifier }\nUnrevealed Prefix Modifier\n"
        "{ Desecrated Suffix Modifier }\nUnrevealed Suffix Modifier\n"
    )
    item = itemtext.parse(text)

    assert len(item["unrevealedMods"]) == 2
    # Never scored, never searched.
    assert not any("Unrevealed" in m for m in item_mods(item))
    assert not any("Unrevealed" in m for m in item["explicitMods"])
    # But they still consume slots: 1 real mod + 2 unrevealed.
    assert used_affixes(item) == 3


def test_unrevealed_mods_reduce_the_open_affix_bonus():
    from sox.valuation.candidates import open_affix_bonus

    base = ("Item Class: Body Armours\nRarity: Rare\nDoom Guardian\nVaal Cuirass\n"
            "--------\nItem Level: 82\n--------\n"
            "{ Prefix Modifier }\n+96 to maximum Life\n")
    clean = itemtext.parse(base)
    veiled = itemtext.parse(
        base + "{ Desecrated Prefix Modifier }\nUnrevealed Prefix Modifier\n"
    )
    clean_bonus, _ = open_affix_bonus(clean, mod_score=3, has_premium=True)
    veiled_bonus, veiled_reason = open_affix_bonus(veiled, mod_score=3, has_premium=True)
    assert veiled_bonus <= clean_bonus
    assert "open4" in veiled_reason, "6 capacity - 1 real - 1 unrevealed = 4 open"


def test_never_searched_on_an_unrevealed_mod():
    from sox.valuation.query import build_query, category_for

    item = itemtext.parse(
        "Item Class: Body Armours\nRarity: Rare\nDoom Guardian\nVaal Cuirass\n"
        "--------\nItem Level: 82\n--------\n"
        "{ Prefix Modifier }\n+96 to maximum Life\n"
        "{ Desecrated Prefix Modifier }\nUnrevealed Prefix Modifier\n"
    )
    query = build_query(item, category_for(item), MODS, NOTABLES)
    dumped = str(query)
    assert "Unrevealed" not in dumped


def test_unique_absent_from_index_is_searched_not_abandoned():
    """We know its name, so a search can price it.

    Leaving it unpriced would be giving up with a usable option in hand.
    """
    from sox.valuation.candidates import should_search_unique

    item = itemtext.parse(
        "Item Class: Shields\nRarity: Unique\nSomeNewUnique\nTower Shield\n"
        "--------\nItem Level: 81\n"
    )
    assert should_search_unique(item, None, UNIQUES) == "not-indexed"


def test_roll_score_from_advanced_descriptions_needs_no_index():
    """PoE2 inlines actual(min-max), so roll quality is free to compute."""
    from sox.valuation.rolls import roll_score_from_item

    item = itemtext.parse(
        "Item Class: Shields\nRarity: Unique\nDoomgate\nBraced Tower Shield\n"
        "--------\nItem Level: 81\n--------\n"
        "{ Unique Modifier }\n100(80-100)% increased Block chance\n"
        "{ Unique Modifier }\n150(100-150)% increased Armour\n"
    )
    assert roll_score_from_item(item) == 1.0

    floor = itemtext.parse(
        "Item Class: Shields\nRarity: Unique\nDoomgate\nBraced Tower Shield\n"
        "--------\nItem Level: 81\n--------\n"
        "{ Unique Modifier }\n80(80-100)% increased Block chance\n"
        "{ Unique Modifier }\n100(100-150)% increased Armour\n"
    )
    assert roll_score_from_item(floor) == 0.0


def test_unrevealed_mod_without_a_number_still_occupies_a_slot():
    """"Unrevealed Suffix Modifier" carries no digits.

    The mod-shape filter drops lines with no number, which silently gave the
    item a free affix slot it does not have.
    """
    from sox.valuation.candidates import used_affixes

    item = itemtext.parse(
        "Item Class: Quarterstaves\nRarity: Rare\nDragon Bane\nBolting Quarterstaff\n"
        "--------\nItem Level: 81\n--------\n"
        "Adds 121 to 183 Cold Damage\n"
        "Adds 6 to 102 Lightning Damage\n"
        "+21 to Intelligence\n"
        "Leeches 8.66% of Physical Damage as Life\n"
        "Gain 66 Life per enemy killed\n"
        "Unrevealed Suffix Modifier\n"
    )
    assert item["unrevealedMods"] == ["Unrevealed Suffix Modifier"]
    assert used_affixes(item) == 6, "5 revealed + 1 unrevealed fills the item"


def test_score_line_shows_how_the_total_was_reached():
    from sox.valuation.allowlists import load_bases
    from sox.valuation.candidates import score_gear

    item = itemtext.parse(
        "Item Class: Quarterstaves\nRarity: Rare\nDragon Bane\nBolting Quarterstaff\n"
        "--------\nItem Level: 81\n--------\n"
        "Adds 121 to 183 Cold Damage\nAdds 6 to 102 Lightning Damage\n"
    )
    total, reason = score_gear(item, MODS, load_bases())
    # The total is printed on its own line; the reason lists the components
    # beneath it, so it must NOT repeat the total.
    assert "mods" in reason and "elemental" in reason
    assert total > 0


def test_breakdown_rows_plus_coherence_sum_to_the_total():
    """The rows ARE the explanation, so they must add up to the number.

    Coherence is reported on its own line rather than as a row, so it is
    added back here — if it were double counted or dropped, the printed
    breakdown would stop matching the score.
    """
    from sox.valuation.allowlists import load_bases
    from sox.valuation.candidates import coherence_of, score_gear, score_rows

    item = itemtext.parse(
        "Item Class: Quarterstaves\nRarity: Rare\nDragon Bane\nBolting Quarterstaff\n"
        "--------\nItem Level: 81\n--------\n"
        "Adds 121 to 183 Cold Damage\n"
        "Adds 6 to 102 Lightning Damage\n"
        "+21 to Intelligence\n"
        "Leeches 8.66% of Physical Damage as Life\n"
        "Gain 66 Life per enemy killed\n"
        "Unrevealed Suffix Modifier\n"
    )
    base_rules = load_bases()
    total, _ = score_gear(item, MODS, base_rules)
    rows = score_rows(item, MODS, base_rules)
    _, _, bonus = coherence_of(item, MODS)
    assert sum(w for _, w, _ in rows if isinstance(w, int)) + bonus == total


def test_mods_carry_the_archetype_they_serve():
    from sox.valuation.allowlists import load_bases
    from sox.valuation.candidates import score_rows

    item = itemtext.parse(
        "Item Class: Quarterstaves\nRarity: Rare\nX\nBolting Quarterstaff\n"
        "--------\nItem Level: 81\n--------\n"
        "Adds 121 to 183 Cold Damage\nAdds 6 to 102 Lightning Damage\n"
        "+21 to Intelligence\n"
    )
    rows = {text: tag for text, _, tag in score_rows(item, MODS, load_bases())}
    assert rows["Adds 121 to 183 Cold Damage"] == "elemental"
    assert rows["+21 to Intelligence"] == "", "not part of the dominant group"


def test_searched_mods_are_reported_in_the_items_own_wording():
    """The breakdown lists the item's text; the query lists the allowlist's.

    Highlighting needs them matched up, or nothing would ever be marked.
    """
    from sox.valuation.allowlists import load_notables
    from sox.valuation.query import explain_selection, searched_item_texts

    item = itemtext.parse(
        "Item Class: Quarterstaves\nRarity: Rare\nX\nBolting Quarterstaff\n"
        "--------\nItem Level: 81\n--------\n"
        "Adds 121 to 183 Cold Damage\nAdds 6 to 102 Lightning Damage\n"
        "+21 to Intelligence\n"
    )
    notables = load_notables()
    _, canonical = explain_selection(item, MODS, notables)
    actual = searched_item_texts(item, MODS, notables)

    assert "# to Intelligence" not in canonical, "not part of the dominant group"
    assert "Adds 121 to 183 Cold Damage" in actual, "item wording, not the template"
    assert "+21 to Intelligence" not in actual
    assert len(actual) == len(canonical)


def test_endgame_families_map_to_a_search_category():
    """An Inscribed Ultimatum came back JUNK with no category at all.

    The endgame Item Class strings are irregular — "Inscribed Ultimatum" is
    its own class — so the base name carries the family instead. 20 items in
    the map group have no index price and reach a price only this way.
    """
    from sox.valuation.query import category_for

    cases = {
        "Inscribed Ultimatum": "map.ultimatum",
        "Djinn Barya": "map.barya",
        "Test of Will Barya": "map.barya",
        "Breachstone": "map.breachstone",
        "Expedition Logbook": "map.logbook",
        "Primary Calamity Fragment": "map.fragment",
        "Simulacrum": "map.fragment",
        "Waystone (Tier 15)": "map.waystone",
        "Abyss Tablet": "map.tablet",
    }
    for base, expected in cases.items():
        item = {"itemClass": base, "baseType": base}
        assert category_for(item) == expected, base


def test_endgame_families_classify_as_endgame():
    from sox.valuation.classify import ItemClass, classify

    for base in ("Inscribed Ultimatum", "Djinn Barya", "Breachstone",
                 "Expedition Logbook", "Simulacrum", "Waystone (Tier 15)"):
        item = {"itemClass": base, "baseType": base, "frameType": 0}
        assert classify(item) is ItemClass.ENDGAME, base
