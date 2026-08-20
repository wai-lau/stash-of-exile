"""Regression tests for bugs found by running against the live API."""

from pathlib import Path

from sox import itemtext
from sox.scout import IndexEntry
from sox.valuation import candidates
from sox.valuation.allowlists import load_bases, load_mods, load_notables, load_uniques
from sox.valuation.classify import ItemClass, classify
from sox.valuation.mods import build_index
from sox.valuation.query import (
    RELAX_STEPS,
    build_query,
    category_for,
    explain_selection,
    query_hash,
)

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


def test_the_query_is_capped_by_the_ladder_rung():
    """Constraining on every matched mod returns nothing.

    The rare bow carries fire AND cold AND lightning AND accuracy AND light
    radius; demanding all of them at once describes one item in the world.
    The rung caps how many survive.
    """
    from sox.valuation.query import RELAX_STEPS

    item = load("RareItem")
    for rung, cap in enumerate(RELAX_STEPS):
        query = build_query(item, category_for(item), MODS, NOTABLES, relax=rung)
        sent = len(query["query"]["stats"][0]["filters"])
        assert sent <= cap, f"rung {rung} sent {sent} filters, cap is {cap}"


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

    assert "Adds 121 to 183 Cold Damage" in actual, "item wording, not the template"
    assert len(actual) == len(canonical)
    # Cohering mods lead; Intelligence trails them rather than being dropped.
    assert canonical.index("Adds # to # Cold Damage") < canonical.index("# to Intelligence")


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


def test_penetration_is_offensive_not_defensive():
    """Penetration cuts through the ENEMY's resistance.

    Matching the word "Resistance" filed it as a defence, clustering it with
    life and resistance rolls for coherence.
    """
    by_text = {m.text: m for m in load_mods()}
    pen = by_text["Damage Penetrates #% Cold Resistance"]
    assert "defence" not in pen.tags
    assert "resistance" not in pen.tags
    assert "elemental" in pen.tags


def test_a_skill_name_is_not_a_defence():
    by_text = {m.text: m for m in load_mods()}
    for text in ("+# to Level of all Armour Breaker Skills",
                 "+# to Level of all Arctic Armour Skills"):
        assert "defence" not in by_text[text].tags, text
        assert "armour" not in by_text[text].tags, text


def test_named_skills_get_the_tags_of_what_they_actually_are():
    """Verified against the PoE2 wikis, not inferred from the name."""
    by_text = {m.text: m for m in load_mods()}
    breaker = by_text["+# to Level of all Armour Breaker Skills"]
    assert {"attack", "melee", "physical"} <= set(breaker.tags)

    arctic = by_text["+# to Level of all Arctic Armour Skills"]
    assert {"spirit", "elemental"} <= set(arctic.tags), "a Spirit gem, cold buff"

    piercing = by_text["+# to Level of all Armour Piercing Rounds Skills"]
    assert {"attack", "projectile"} <= set(piercing.tags), "crossbow ammunition"


def test_minion_defences_do_not_carry_the_players_defence_tags():
    """Minion life is not your life, and must not cluster with it."""
    for entry in load_mods():
        if entry.subject in ("minion", "companion", "totem"):
            assert not {"defence", "life", "es", "armour", "evasion",
                        "resistance"} & set(entry.tags), entry.text


def _forgotten_warden(dex: int, life: int):
    """A real index-priced unique: 9 ex across 15,162 listings."""
    from sox.scout import IndexEntry

    item = itemtext.parse(
        "Item Class: Body Armours\nRarity: Unique\nForgotten Warden\n"
        "Primal Markings\n--------\nEvasion Rating: 965\nEnergy Shield: 295\n"
        "--------\nItem Level: 84\n--------\n"
        f"+85 to Deflection Rating per 50 missing Energy Shield\n"
        f"250% increased Evasion and Energy Shield\n"
        f"+{dex} to Dexterity\n"
        f"Companions have {life}% increased maximum Life\n"
        "12% of Damage from Deflected Hits is taken from Damageable "
        "Companion's Life before you\n"
    )
    entry = IndexEntry(
        name="Forgotten Warden", price_ex=9.0, quantity=15162,
        metadata={"explicit_mods": [
            "+(70-100) to Deflection Rating per 50 missing Energy Shield",
            "(200-300)% increased Evasion and Energy Shield",
            "+(20-30) to Dexterity",
            "Companions have (30-50)% increased maximum Life",
            "(10-15)% of Damage from Deflected Hits is taken from "
            "Damageable Companion's Life before you",
        ]},
    )
    return item, entry


def test_one_strong_roll_escalates_a_unique_the_mean_would_bury():
    """ANY roll in the top quarter is worth a search.

    The market prices a unique on the roll people buy it for. Forgotten Warden
    reported "47th percentile (average roll)" and took the index price while
    carrying a near-perfect Dexterity roll.
    """
    from sox.valuation.candidates import item_mods, should_search_unique
    from sox.valuation.rolls import roll_percentiles

    item, entry = _forgotten_warden(dex=29, life=32)
    percentiles = roll_percentiles(item_mods(item), entry.metadata)
    assert max(percentiles) >= 0.75 and sum(percentiles) / len(percentiles) < 0.75, (
        "the mean must hide the strong roll, or this proves nothing"
    )
    assert should_search_unique(item, entry, UNIQUES) == "good-roll"


def test_a_uniformly_mediocre_unique_still_takes_the_index_price():
    from sox.valuation.candidates import should_search_unique

    item, entry = _forgotten_warden(dex=25, life=40)
    assert should_search_unique(item, entry, UNIQUES) is None


def test_a_granted_skill_escalates_a_unique():
    """No unique in the scout index carries a granted skill, so the level ours
    grants is invisible to the index price."""
    from sox.valuation.candidates import should_search_unique
    from sox.scout import IndexEntry

    item = itemtext.parse(
        "Item Class: Wands\nRarity: Unique\nSomeWand\nWithered Wand\n"
        "--------\nItem Level: 82\n--------\n"
        "Grants Skill: Level 20 Chaos Bolt\n--------\n"
        "30% increased Chaos Damage\n"
    )
    entry = IndexEntry(name="SomeWand", price_ex=9.0, quantity=100,
                       metadata={"explicit_mods": ["(25-35)% increased Chaos Damage"]})
    assert should_search_unique(item, entry, UNIQUES) == "granted-skill"


def test_a_clean_item_does_not_price_against_corrupted_listings():
    """Corruption closes off every further craft, so a corrupted listing is not
    'at least as good' and would drag the cheapest match below our floor."""
    item = itemtext.parse(
        "Item Class: Helmets\nRarity: Rare\nX\nWarded Helm\n"
        "--------\nArmour: 91\n--------\nItem Level: 81\n--------\n"
        "{ Suffix Modifier }\n+31(26-35)% to Lightning Resistance\n"
    )
    misc = build_query(item, category_for(item), MODS, NOTABLES)["query"]["filters"]
    assert misc["misc_filters"]["filters"] == {
        "corrupted": {"option": "false"}, "sanctified": {"option": "false"}
    }

    # Ours is corrupted: once the item has been touched at all the whole
    # market is comparable again, so neither flag is pinned.
    corrupted = itemtext.parse(
        "Item Class: Helmets\nRarity: Rare\nX\nWarded Helm\n"
        "--------\nArmour: 91\n--------\nItem Level: 81\n--------\n"
        "{ Suffix Modifier }\n+31(26-35)% to Lightning Resistance\n"
        "--------\nCorrupted\n"
    )
    assert corrupted["corrupted"] is True
    filters = build_query(corrupted, category_for(corrupted), MODS, NOTABLES)
    assert "misc_filters" not in filters["query"]["filters"]


def _warden_text():
    return Path("tests/fixtures/items/ForgottenWardenTwiceCorrupted.txt").read_text()


def test_twice_corrupted_reads_as_corrupted():
    """A twice-corrupted item prints only "Twice Corrupted", never "Corrupted"
    as well, so the plain flag has to be implied.

    Without it the item read as untouched: it escaped the corrupted escalation
    and the search pinned corrupted=No, asking the market for clean copies of
    an item that can never be one.
    """
    item = itemtext.parse(_warden_text())
    assert item["corrupted"] is True and item["twiceCorrupted"] is True

    # Nothing is pinned once ours is touched — not twice_corrupted either. A
    # second corruption is as likely to have ruined the item as improved it.
    filters = build_query(item, category_for(item), MODS, NOTABLES)
    assert "misc_filters" not in filters["query"]["filters"]
    assert should_search_unique_of(item) == "corrupted"


def should_search_unique_of(item):
    from sox.valuation.candidates import should_search_unique
    from sox.scout import IndexEntry

    return should_search_unique(item, IndexEntry(
        name="Forgotten Warden", price_ex=9.0, quantity=15162,
        metadata={"explicit_mods": [
            "+(70-100) to Deflection Rating per 50 missing Energy Shield",
            "(200-300)% increased Evasion and Energy Shield",
            "+(20-30) to Dexterity",
            "Companions have (30-50)% increased maximum Life",
            "(10-15)% of Damage from Deflected Hits is taken from "
            "Damageable Companion's Life before you",
        ]}), UNIQUES)


def test_rolls_match_their_template_by_text_not_position():
    """One extra mod used to shift every roll onto the wrong range.

    This copy carries a corruption enhancement the index template has no entry
    for, so zipping the two lists in order scored Dexterity 24 against the
    Evasion range (200-300) and reported 0th, then Evasion 280 against
    Dexterity's (20-30) and reported 100th.
    """
    from sox.valuation.candidates import item_mods
    from sox.valuation.rolls import roll_percentiles

    item = itemtext.parse(_warden_text())
    entry_mods = [
        "+(70-100) to Deflection Rating per 50 missing Energy Shield",
        "(200-300)% increased Evasion and Energy Shield",
        "+(20-30) to Dexterity",
        "Companions have (30-50)% increased maximum Life",
        "(10-15)% of Damage from Deflected Hits is taken from "
        "Damageable Companion's Life before you",
    ]
    got = roll_percentiles(item_mods(item), {"explicit_mods": entry_mods})
    assert [round(p, 2) for p in got] == [0.2, 0.8, 0.4, 0.6, 0.8]
    # The corruption enhancement has no template and is skipped, not misaligned.
    assert len(got) == len(entry_mods)


def test_the_granted_skill_survives_a_leading_property_line():
    """`Grants Skill:` sits in its own section here, but when it heads a mod
    block the colon used to make the parser read the whole block as properties
    and drop every mod under it."""
    from sox.valuation.query import granted_skill_filter

    item = itemtext.parse(_warden_text())
    assert granted_skill_filter(item) == {
        "id": "skill.spirit_vessel_companion", "value": {"min": 20}
    }

    inline = itemtext.parse(
        "Item Class: Body Armours\nRarity: Unique\nX\nPrimal Markings\n"
        "--------\nEnergy Shield: 414\n--------\nItem Level: 84\n--------\n"
        "Grants Skill: Level 20 Spirit Vessel\n"
        "+24(20-30) to Dexterity\n"
    )
    assert granted_skill_filter(inline) is not None
    assert inline["explicitMods"] == ["+24 to Dexterity"], "the mod survived"


def test_a_uniques_defences_are_not_rebuilt_at_its_worst_roll():
    """Every copy of a unique carries the same mods, so the roll is the only
    thing separating them — and it is exactly what is being priced.

    Rebuilding Forgotten Warden's (200-300)% hybrid at 200% asked the market
    for the worst copy of the item in hand: 414 Energy Shield became 312 and
    1355 Evasion became 1021, which live matched 196 listings from 4 divine
    instead of 64 from 10.
    """
    item = itemtext.parse(_warden_text())
    equipment = build_query(item, category_for(item), MODS, NOTABLES)[
        "query"]["filters"]["equipment_filters"]["filters"]
    # The rune's 18% is still removed — its bonus is not the item's — and what
    # is left is filed at 20% quality, which is the unit the ev/es filters
    # compare in. This copy is +27%, so the filter de-rates it rather than
    # inflating it: 1,294 rune-free Evasion is asked for as 1,222.
    assert equipment == {"es": {"min": 373}, "ev": {"min": 1222}}


def test_a_hybrid_defence_mod_counts_for_every_defence_it_names():
    """"increased Evasion and Energy Shield" ends on Energy Shield, so an
    Evasion pattern anchored to the end of the line missed it — the same mod
    adjusted the ES total and left Evasion untouched."""
    from sox.valuation.query import DEFENCE_PROPERTIES

    def hit(text):
        return sorted({fid for _, (fid, _, pct) in DEFENCE_PROPERTIES.items()
                       if pct.search(text)})

    assert hit("280% increased Evasion and Energy Shield") == ["es", "ev"]
    assert hit("18% increased Armour, Evasion and Energy Shield") == ["ar", "es", "ev"]
    # The end-anchor used to keep these out; the lookaheads do it now.
    assert hit("25% increased Energy Shield Recharge Rate") == []
    assert hit("40% increased Armour Break duration") == []


def test_quality_is_searched_on_gems_only():
    """Currency takes any other item to 20%, so pinning quality excludes
    cheaper copies a buyer would happily quality up themselves."""
    warden = itemtext.parse(_warden_text())
    assert warden["properties"][0]["name"] == "Quality"
    types = build_query(warden, category_for(warden), MODS, NOTABLES)[
        "query"]["filters"]["type_filters"]["filters"]
    assert "quality" not in types


def test_a_corruption_enhancement_is_an_enchant_not_an_explicit():
    """It occupies no affix slot, and the trade API files it under the enchant
    group — the same place the notables turned out to live.

    Searched as explicit it returned 0 listings live where the enchant group
    returned thousands, so the mod silently contributed nothing.
    """
    from sox.valuation.query import regroup

    item = itemtext.parse(_warden_text())
    assert item["enchantMods"] == ["40% increased Thorns damage"]
    assert "40% increased Thorns damage" not in item["explicitMods"]
    assert regroup(("explicit.stat_1315743832",), "enchant") == (
        "enchant.stat_1315743832",
    )


def test_an_implicit_is_searched_under_the_implicit_group():
    """An implicit is a real stat a buyer filters on, but the trade API files
    it under its own group.

    `implicit.stat_3299347043` and `explicit.stat_3299347043` are the same
    stat reached two ways, and asking the explicit table about an implicit
    returns nothing — so this used to be dropped from the search entirely.
    """
    # Rarity has no pseudo total, so it reaches the query as itself. A
    # resistance implicit would be folded into the pseudo instead, which is
    # group-independent and needs no twin.
    item = itemtext.parse(
        "Item Class: Amulets\nRarity: Rare\nX\nGold Amulet\n"
        "--------\nItem Level: 82\n--------\n"
        "{ Implicit Modifier }\n18(15-20)% increased Rarity of Items found\n"
        "--------\n"
        "{ Prefix Modifier }\n+96(90-99) to maximum Life\n"
    )
    assert item["implicitMods"] == ["18% increased Rarity of Items found"]

    ids = [f["id"] for g in build_query(item, category_for(item), MODS, NOTABLES)[
        "query"]["stats"] for f in g["filters"]]
    assert "implicit.stat_3917489142" in ids, "the implicit reached the query"
    assert not any(i.startswith("explicit.stat_3917489142") for i in ids), \
        "and not under the explicit group, which would match nothing"


def test_forty_allowlisted_mods_can_be_rolled_as_an_implicit():
    """The map has to be exact: only 178 of the 3031 explicit stats have an
    implicit twin, so an unchecked prefix swap would send filters that match
    no listing."""
    from sox.valuation.allowlists import load_mods

    with_twin = [m for m in load_mods() if m.implicit_ids]
    assert len(with_twin) == 40
    for entry in with_twin:
        assert all(i.startswith("implicit.") for i in entry.implicit_ids)
        # Same numeric stat, different table.
        assert {i.split(".", 1)[-1] for i in entry.implicit_ids} <= {
            i.split(".", 1)[-1] for i in entry.ids}


def test_an_implicit_scores_nothing_and_costs_no_affix_slot():
    """It is searched, not scored: an implicit does not occupy a prefix or
    suffix, so it must not eat into the room left to craft either."""
    from sox.valuation.candidates import score_rows, used_affixes

    item = itemtext.parse(
        "Item Class: Amulets\nRarity: Rare\nX\nGold Amulet\n"
        "--------\nItem Level: 82\n--------\n"
        "{ Implicit Modifier }\n18(15-20)% increased Rarity of Items found\n"
        "--------\n"
        "{ Prefix Modifier }\n+96(90-99) to maximum Life\n"
    )
    assert used_affixes(item) == 1, "the implicit is not an affix"
    rows = score_rows(item, MODS, load_bases())
    implicit_rows = [r for r in rows if r[2] == "implicit"]
    assert implicit_rows == [("18% increased Rarity of Items found", None, "implicit")]


def test_an_implicit_is_filtered_at_the_floor_of_its_range():
    """An implicit comes with the base rather than being rolled onto it.

    The buyer is shopping for the base and will take any roll of it, so
    filtering at ours would drop the same base over a difference nobody is
    paying for.
    """
    item = itemtext.parse(
        "Item Class: Amulets\nRarity: Rare\nX\nGold Amulet\n"
        "--------\nItem Level: 82\n--------\n"
        "{ Implicit Modifier }\n18(15-20)% increased Rarity of Items found\n"
        "--------\n"
        "{ Prefix Modifier }\n+96(90-99) to maximum Life\n"
    )
    filters = {f["id"]: f["value"]["min"] for g in
               build_query(item, category_for(item), MODS, NOTABLES)["query"]["stats"]
               for f in g["filters"]}
    assert filters["implicit.stat_3917489142"] == 15.0, "the floor, not our 18"
    # An explicit is still filtered at what we actually rolled.
    assert filters["pseudo.pseudo_total_life"] == 90


def test_an_implicit_folded_into_a_pseudo_is_not_searched_twice():
    """Resistances go to the pseudo total, which is group-independent and
    needs no implicit twin — so the implicit must not also appear alone."""
    item = itemtext.parse(
        "Item Class: Amulets\nRarity: Rare\nX\nGold Amulet\n"
        "--------\nItem Level: 82\n--------\n"
        "{ Implicit Modifier }\n+18(15-20)% to Fire Resistance\n"
        "--------\n"
        "{ Suffix Modifier }\n+31(26-35)% to Lightning Resistance\n"
    )
    ids = [f["id"] for g in build_query(item, category_for(item), MODS, NOTABLES)[
        "query"]["stats"] for f in g["filters"]]
    assert "pseudo.pseudo_total_elemental_resistance" in ids
    assert not any(i.startswith("implicit.") for i in ids), \
        "the pseudo already carries it"


def test_the_search_asks_for_the_rarity_the_item_actually_is():
    """A rare, a magic and a normal of the same base are different goods.

    The normal is bought as a craft base and priced on its ilvl, the magic on
    its two mods and the room to regal it, the rare on its mods. "nonunique"
    spans all three, so each was priced against the others' market.
    """
    base = ("Item Class: Body Armours\nRarity: {rarity}\n{name}\nShrouded Mail\n"
            "--------\nArmour: 178\n--------\nItem Level: 81\n{mods}")
    cases = {
        "Rare": ("Behemoth Cloak",
                 "--------\n{ Suffix Modifier }\n+32(25-35) to Strength\n", "rare"),
        "Normal": ("Shrouded Mail", "", "normal"),
        "Magic": ("Sturdy Shrouded Mail",
                  "--------\n+32(25-35) to Strength\n", "magic"),
    }
    for rarity, (name, mods, expected) in cases.items():
        item = itemtext.parse(base.format(rarity=rarity, name=name, mods=mods))
        types = build_query(item, category_for(item), MODS, NOTABLES)[
            "query"]["filters"]["type_filters"]["filters"]
        assert types["rarity"] == {"option": expected}, rarity


def test_a_mod_covered_by_an_equipment_filter_is_tagged_as_one():
    """The mod IS searched, but through the item's total.

    Naming its buyer group instead implied a stat filter that is not in the
    query: a mace showed its cold roll as "(elemental)" and its physical roll
    as "(filter)" when both had become the one DPS filter.
    """
    from sox.valuation.candidates import score_rows

    mace = itemtext.parse(
        "Item Class: One Hand Maces\nRarity: Rare\nPain Ram\nBandit Mace\n"
        "--------\nPhysical Damage: 100-211\nCold Damage: 58-97\n"
        "Attacks per Second: 1.45\n--------\nItem Level: 80\n--------\n"
        "{ Prefix Modifier }\nAdds 58(40-60) to 97(80-110) Cold Damage\n"
        "{ Prefix Modifier }\n127(100-129)% increased Physical Damage\n"
        "{ Suffix Modifier }\n+2 to Level of all Attack Skills\n"
    )
    tags = {text: tag for text, _weight, tag in score_rows(mace, MODS, load_bases())}
    assert tags["Adds 58 to 97 Cold Damage"] == "filter"
    assert tags["127% increased Physical Damage"] == "filter"
    assert tags["+2 to Level of all Attack Skills"] == ""


def test_the_ladder_can_widen_all_the_way_to_no_mods():
    """What is left is still a real search — category, rarity, item level,
    requirements, and the totals in the equipment filters.

    On a weapon those totals are most of the item: DPS and requirements alone
    is how you would search for a mace by hand. Without this rung a weapon
    whose exact mods nobody else rolled came back unpriced while comparable
    maces were listed at 10 divine.
    """
    from sox.valuation.query import RELAX_STEPS, build_query, category_for

    assert RELAX_STEPS[-1] == 0
    mace = itemtext.parse(
        "Item Class: One Hand Maces\nRarity: Rare\nPain Ram\nBandit Mace\n"
        "--------\nPhysical Damage: 100-211\nAttacks per Second: 1.45\n"
        "--------\nRequires: Level 60, 104 Str\n"
        "--------\nItem Level: 80\n--------\n"
        "{ Suffix Modifier }\n+2 to Level of all Attack Skills\n"
    )
    last = build_query(mace, category_for(mace), MODS, NOTABLES,
                       relax=len(RELAX_STEPS) - 1)
    assert last["query"]["stats"][0]["filters"] == [], "no mods left"
    filters = last["query"]["filters"]
    assert filters["equipment_filters"]["filters"]["dps"]["min"] > 0
    assert filters["req_filters"]["filters"] == {"lvl": {"max": 60},
                                                 "str": {"max": 104}}


# A unique tablet's biome. Measured live 2026-08-19 against Runes of Aldur:
# the Forest variant asked 55 ex over 8,620 listings while Desert, Grass,
# Swamp and Water sat at 1-3 ex, and both the index and the exchange report
# one price — 1 exalted — for all 26,357 Mastered Domains regardless of biome.
FOREST = "explicit.stat_864099561"


def test_a_unique_tablet_is_searched_by_its_name():
    """The name is gated on rarity, not on the pricing class.

    A unique Tablet classifies as ENDGAME — the class picks the market, and
    endgame items have no index — so gating on ItemClass.UNIQUE left the name
    off entirely and the search asked for every unique tablet in the game.
    """
    item = load("UniqueTabletForest")
    query = build_query(item, category_for(item), MODS, NOTABLES)
    assert classify(item) is ItemClass.ENDGAME, "the class is not what decides"
    assert query["query"]["name"] == "Mastered Domain"


def test_a_flag_mod_is_searched_with_no_minimum():
    """The biome IS the item, and it carries no number to search at.

    Priced without it the query returned every biome sorted by price, so a
    Desert tablet at 1 exalted set the price of a Forest one worth 55.
    """
    item = load("UniqueTabletForest")
    query = build_query(item, category_for(item), MODS, NOTABLES)
    assert query["query"]["stats"][0]["filters"] == [{"id": FOREST, "value": {}}]


def test_the_unscalable_annotation_is_not_part_of_the_mod():
    """The game tags map mods nothing can scale; the stats table does not.

    "Map also counts as a Forest Map — Unscalable Value" against a table that
    says "Map also counts as a Forest Map" matched nothing at all.
    """
    from sox.valuation.mods import normalize_mod

    assert (normalize_mod("Map also counts as a Forest Map — Unscalable Value")
            == normalize_mod("Map also counts as a Forest Map"))


def test_widening_drops_mods_around_a_flag_and_never_the_flag():
    """Same rule as a notable: widening must not change what is being
    searched for. A Mastered Domain without its biome is one of 26,357."""
    from sox.valuation.query import RELAX_STEPS

    item = load("UniqueTabletForest")
    for step in range(len(RELAX_STEPS)):
        query = build_query(item, category_for(item), MODS, NOTABLES, relax=step)
        assert query["query"]["stats"][0]["filters"] == [{"id": FOREST, "value": {}}]


def test_a_rare_map_flag_is_left_alone():
    """Only a unique's flags are identity. On a rare the mods are value, and
    the allowlist is what says which of them a buyer pays for."""
    item = load("RareMap")
    query = build_query(item, category_for(item), MODS, NOTABLES)
    ids = [f["id"] for f in query["query"]["stats"][0]["filters"]]
    assert "explicit.stat_3477720557" not in ids, "Shocked Ground is not identity"
    assert all(f.get("value") != {} for f in query["query"]["stats"][0]["filters"])


def _stat_ids(query):
    """Every stat id the query asks for, and-filters and or-groups alike."""
    out = []
    for group in query["query"]["stats"]:
        out += [f["id"] for f in group["filters"]]
    return out


def test_the_report_names_the_stats_the_query_asked_for():
    """Spirit read as ignored on a chest whose price rested on it.

    +61 to Spirit went into every rung of the search — as an or-group over
    the two ids a listing can carry it under — while the breakdown reported
    it at rung 0 and dropped it at rung 2. The two were derived separately
    from the same item; now the report reads off the query.
    """
    from sox.valuation.query import explain_selection

    item = load("SpiritLifeChest")
    for step in range(len(RELAX_STEPS)):
        query = build_query(item, category_for(item), MODS, NOTABLES, relax=step)
        _, reported = explain_selection(item, MODS, NOTABLES, relax=step)
        spirit = any(i == "explicit.stat_3981240776" for i in _stat_ids(query))
        assert spirit == ("# to Spirit" in reported), f"rung {step}"


def test_every_rung_of_the_ladder_widens_the_query():
    """An or-group kept outside the cap could not be widened away.

    Rungs 0 and 1 built the identical query, so a search was spent replaying
    the previous one and the ladder arrived a rung late.
    """
    item = load("SpiritLifeChest")
    seen = [query_hash(build_query(item, category_for(item), MODS, NOTABLES,
                                   relax=step))
            for step in range(len(RELAX_STEPS))]
    assert len(set(seen)) == len(seen), "each rung must be a different search"


def test_a_multi_id_mod_takes_one_place_in_the_ladder():
    """Spirit is local on a sceptre and global on an amulet, so it is asked
    for as "at least one of these two ids" — but it is still one mod."""
    item = load("SpiritLifeChest")
    for step, cap in enumerate(RELAX_STEPS):
        query = build_query(item, category_for(item), MODS, NOTABLES, relax=step)
        groups = query["query"]["stats"]
        constraints = len(groups[0]["filters"]) + len(groups) - 1
        assert constraints <= cap, f"rung {step} asked for {constraints} of {cap}"


def test_a_mod_folded_into_a_pseudo_total_says_so():
    """The row has to name the filter the query actually sent.

    +121 to maximum Life is searched as pseudo_total_life, not under its own
    stat id, and a row reading only "(defence)" described a stat filter that
    is not in the query.
    """
    item = load("SpiritLifeChest")
    tags = {text: tag for text, _weight, tag in candidates.score_rows(item, MODS, BASES)}
    assert tags["+121 to maximum Life"] == "defence, pseudo"
    assert tags["+24% to Cold Resistance"] == "defence, pseudo"
    # The archetype stays: the total serves the same buyer the mod does, and
    # the coherence line needs its two defence rows to point at.
    assert tags["+61 to Spirit"] == ""
    # An equipment filter still substitutes rather than adds — the mod's own
    # identity is dissolved into the item's displayed total.
    assert tags["+40 to Armour"] == "filter"


def test_a_skill_name_with_two_ids_searches_both():
    """Corpsewade Iron Greaves came back with no comparable listing at all.

    "Grants Skill: Level 18 Decompose" resolves to TWO stat ids — the plain
    skill and the triggered copy — and the item text says only the name. The
    resolver picked the shorter, skill.corpse_cloud, and these boots grant the
    triggered one: measured live, that id matched 0 listings where
    skill.corpse_cloud_triggered matched 1,806.
    """
    from sox.valuation.allowlists import load_skills
    from sox.valuation.query import granted_skill_filter

    assert load_skills()["Decompose"] == [
        "skill.corpse_cloud", "skill.corpse_cloud_triggered"]

    item = load("CorpsewadeBoots")
    skill = granted_skill_filter(item)
    assert skill["type"] == "count" and skill["value"] == {"min": 1}
    assert skill["filters"] == [
        {"id": "skill.corpse_cloud", "value": {"min": 18}},
        {"id": "skill.corpse_cloud_triggered", "value": {"min": 18}},
    ]


def test_an_unambiguous_skill_stays_one_filter():
    """Most skills carry exactly one id, and a count group would say nothing."""
    from sox.valuation.query import granted_skill_filter

    wand = load("WandRareItem")
    skill = granted_skill_filter(wand)
    assert skill is not None and "type" not in skill


def test_a_skill_count_group_is_a_stat_group_of_its_own():
    """A count group is never a member of the `and` list, and it stays exempt
    from the widening ladder: dropping the skill would not widen the search,
    it would change what is being searched for."""
    item = load("CorpsewadeBoots")
    for step in range(len(RELAX_STEPS)):
        stats = build_query(item, category_for(item), MODS, NOTABLES,
                            relax=step)["query"]["stats"]
        assert all("type" not in f for f in stats[0]["filters"])
        groups = [g for g in stats[1:]
                  if any(f["id"].startswith("skill.") for f in g["filters"])]
        assert len(groups) == 1, f"rung {step}"


def test_a_notable_is_looked_up_without_the_games_tag():
    """The amulet says "Allocates The Soul Meridian — Unscalable Value".

    The tag is the game's annotation, not part of the notable's name, and the
    table holds "The Soul Meridian". Left on, the lookup missed: the line
    scored nothing, went into no query, and the amulet was priced as though
    its enchant were not there. Searched for it, rung 0 matches one listing
    and rung 2 matches sixty-one.
    """
    from sox.valuation.mods import strip_annotation

    assert strip_annotation("The Soul Meridian — Unscalable Value") == "The Soul Meridian"

    item = load("EnhancementAmulet")
    group, stats = explain_selection(item, MODS, NOTABLES)
    assert group == "notable"
    assert "Allocates The Soul Meridian — Unscalable Value" in stats

    filters = build_query(item, category_for(item), MODS, NOTABLES)["query"]["stats"]
    ids = [f["id"] for g in filters for f in g["filters"]]
    assert NOTABLES["The Soul Meridian"] in ids
    assert NOTABLES["The Soul Meridian"].startswith("enchant.stat_")


def test_a_normal_or_magic_or_unique_item_is_searched_as_its_base():
    """Without the base pinned the query describes a CATEGORY.

    Measured live on this belt: 5,595 belts matched and the cheapest were a
    Double Belt, a Mail Belt and a Wide Belt at 1 exalted apiece, none of them
    the item in hand. Pinned, the same search matched 4,896 Heavy Belts and
    the cheapest was 14.
    """
    belt = load("NormalHeavyBelt")
    query = build_query(belt, category_for(belt), MODS, NOTABLES)["query"]
    assert query["type"] == "Heavy Belt"


def test_the_base_is_read_out_of_whatever_the_clipboard_calls_it():
    """A magic item wraps its base in affixes and a normal one with quality
    prefixes "Superior", so the longest known base on the line is the base."""
    from sox.valuation.query import base_type

    assert base_type({"baseType": "Crackling Temple Maul of the Brute"}) == "Temple Maul"
    assert base_type({"baseType": "Superior Divine Crown"}) == "Divine Crown"
    assert base_type({"baseType": "Heavy Belt"}) == "Heavy Belt"
    # Nothing recognised leaves the search on its category rather than pinning
    # a base that was guessed at.
    assert base_type({"baseType": "Sword of Not A Real Thing"}) is None
    assert base_type({}) is None


def test_a_unique_pins_its_base_beside_its_name():
    item = load("UniqueTabletForest")
    query = build_query(item, category_for(item), MODS, NOTABLES)["query"]
    assert (query["name"], query["type"]) == ("Mastered Domain", "Irradiated Tablet")


def test_a_rare_is_not_pinned_to_its_base():
    """A rare is bought on the mods it rolled, and its base is already bounded
    by the requirements, which are searched as a cap — that is what separates
    a Bandit Mace from every one-hander."""
    for name in ("RareItem", "SpiritLifeChest", "MinionRing"):
        item = load(name)
        query = build_query(item, category_for(item), MODS, NOTABLES)["query"]
        assert "type" not in query, name


def test_every_melee_family_the_trade_filters_carry_has_a_class():
    """An unmapped item class cannot be searched at all.

    A rare Alpha Talisman scoring 10 came back unpriced, and the only tell was
    the type row reading "Talismans" with no category after it. A talisman is
    filed under WEAPONS, not accessories.
    """
    from sox.valuation.query import ITEM_CLASS_CATEGORIES

    talisman = load("RareTalisman")
    assert category_for(talisman) == "weapon.talisman"
    for one_handed, two_handed in (("mace", "mace"), ("sword", "sword"),
                                   ("axe", "axe")):
        assert ITEM_CLASS_CATEGORIES[f"one hand {one_handed}s"] == f"weapon.one{one_handed}"
        assert ITEM_CLASS_CATEGORIES[f"two hand {two_handed}s"] == f"weapon.two{two_handed}"


def test_an_unmapped_class_says_so_rather_than_blaming_the_index(tmp_path):
    """"no-index" reads as a fact about the market. This is a gap in a table
    in this repo, and the item is priceable the moment it is filled."""
    import types

    from sox.cache import Cache
    from sox.cli import _price_item

    rod = itemtext.parse(
        "Item Class: Fishing Rods\nRarity: Rare\nX\nY\n"
        "--------\nItem Level: 80\n--------\n"
        "{ Prefix Modifier }\n+96(90-100) to maximum Life\n"
    )
    assert category_for(rod) is None
    priced = _price_item(
        rod, {}, {"exalted": 1.0}, MODS, BASES, UNIQUES, NOTABLES, None,
        Cache(tmp_path / "c.sqlite"),
        types.SimpleNamespace(status="any", max_searches=4, force=False),
    )
    assert priced.tag == "unpriced:no-category:Fishing Rods"
