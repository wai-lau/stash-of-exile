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
    # No archetype of its own — it serves no elemental buyer — but it is
    # still searched, as the Intelligence total, last in the ladder and first
    # to go when the search widens.
    assert rows["+21 to Intelligence"] == "pseudo"


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


def test_the_allowlisted_mods_with_an_implicit_twin_are_exactly_known():
    """The map has to be exact: only 178 of the 3031 explicit stats have an
    implicit twin, so an unchecked prefix swap would send filters that match
    no listing. Forty, and flat Energy Shield since a Lunar Amulet's implicit
    went unsearched."""
    from sox.valuation.allowlists import load_mods

    with_twin = [m for m in load_mods() if m.implicit_ids]
    assert len(with_twin) == 41
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
    assert filters["req_filters"]["filters"] == {"str": {"max": 104}}


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


def test_a_waystone_is_searched_on_every_total_its_tooltip_shows():
    """0.5 welded a fixed loot line onto every waystone mod and the tooltip
    sums them: Item Rarity, Pack Size, Monster Rarity, Monster Effectiveness,
    Waystone Drop Chance. A buyer reads those five and nothing else, so the
    comparable is a stone at least as good on all five. (Before 0.5 only item
    rarity moved the price, measured live; monster rarity is now the loot
    stat and the old measurement is void.)

    Filter ids verified against /api/trade2/data/filters -> map_filters:
    "Monster Effectiveness" is what the API labels map_magic_monsters."""
    item = load("RareMapFakeAllProps")
    query = build_query(item, category_for(item), MODS, NOTABLES)
    filters = query["query"]["filters"]["map_filters"]["filters"]
    assert filters == {
        "map_tier": {"min": 16}, "map_iir": {"min": 17},
        "map_packsize": {"min": 20}, "map_rare_monsters": {"min": 32},
        "map_magic_monsters": {"min": 45}, "map_bonus": {"min": 90},
    }


def test_a_waystone_tier_is_a_floor_not_a_pin():
    """The tier comes from the base name, and it is a minimum like every
    other constraint: a higher tier is at least as good, and there is no
    maximum anywhere in the search. RareMap is a pre-0.5 stone: revives are
    never searched and its "Rare Monsters" is the old label, not the
    tooltip's "Monster Rarity"."""
    item = load("RareMap")
    query = build_query(item, category_for(item), MODS, NOTABLES)
    filters = query["query"]["filters"]["map_filters"]["filters"]
    assert filters == {"map_tier": {"min": 14}, "map_packsize": {"min": 34},
                       "map_bonus": {"min": 75}}


def test_waystone_stats_survive_every_widening_rung():
    """Like a defence total, a waystone stat is the honest measure of the
    item, so widening trims mods around it and never the stat itself."""
    item = load("RareMapFakeAllProps")
    for step in range(len(RELAX_STEPS)):
        query = build_query(item, category_for(item), MODS, NOTABLES, relax=step)
        filters = query["query"]["filters"]["map_filters"]["filters"]
        assert filters["map_iir"] == {"min": 17}, f"rung {step} dropped the stat"


def test_gear_carries_no_map_filters():
    item = load("RareItem")
    query = build_query(item, category_for(item), MODS, NOTABLES)
    assert "map_filters" not in query["query"]["filters"]


def test_the_report_words_the_waystone_stats_off_the_query():
    """Same discipline as the stat ladder: the wording the report shows is
    derived from the filters the query sends, so the two cannot drift."""
    from sox.valuation.query import waystone_stat_texts

    item = load("RareMapFakeAllProps")
    assert waystone_stat_texts(item, category_for(item)) == [
        "tier 16+", "item rarity 17%+", "pack size 20%+", "monster rarity 32%+",
        "effectiveness 45%+", "drop chance 90%+",
    ]


def test_a_waystones_loot_score_weighs_rares_over_whites():
    """From the tooltip totals, not the mods. Effectiveness is exact from the
    game's own glossary — 1% more quantity per 2% — the rest is judgement:
    monster rarity is where the currency drops, item rarity upgrades drops
    with diminishing returns, pack size mostly adds whites."""
    from sox.valuation.query import loot_score

    # 37 + 0/2 + 24/2 + 16 = 65 -> the integer, and the band is its
    assert loot_score(load("GhostExpedition")) == (65, "run it")
    # 32 + 45/2 + 17/2 + 20 = 83
    assert loot_score(load("RareMapFakeAllProps")) == (83, "juice it")
    assert loot_score(load("RareItem")) is None


def test_a_waystone_is_priced_off_its_tiers_bulk_book_without_a_search(tmp_path):
    """A waystone is never searched. Its search caps at 10,000 matches —
    a commodity, and the exchange carries that commodity by tier: Waystone
    (Tier 15) held 6,957 online units — so the search only ever spent a
    call to learn what the book already said. What separates one stone from
    another is the loot score, computed from the tooltip."""
    import types

    from sox.cache import Cache
    from sox.cli import _price_item
    from sox.ggg.exchange import Book, Offer
    from sox.valuation.query import loot_score

    class NoSearch:
        def search(self, query):
            raise AssertionError("a waystone must not be searched")

        fetch = search

    class BulkBook:
        def ids(self):
            return {"Waystone (Tier 14)": "waystone-14"}

        def book(self, item_id, have="exalted"):
            if item_id == "waystone-14" and have == "exalted":
                return Book([Offer(ratio=24.0, stock=500)], 2501)
            return Book([], 0)

    item = load("RareMap")
    priced = _price_item(
        item, {}, {"exalted": 1.0}, MODS, BASES, UNIQUES, NOTABLES,
        NoSearch(), Cache(tmp_path / "c.sqlite"),
        types.SimpleNamespace(status="any", max_searches=4, force=False),
        exchange=BulkBook(),
    )
    assert priced.source == "exchange"
    assert priced.tag == "waystone"
    assert priced.price_ex == 24.0
    assert priced.loot == loot_score(item) == (34, "reroll")
    assert priced.map_stats == (), "nothing was searched"
    assert priced.category == "map.waystone"


def test_a_capped_search_with_no_book_keeps_the_sample_and_its_note(tmp_path):
    """Gear has no bulk book, so a capped gear search keeps the trade answer
    — the render already brands it a sample."""
    import types

    from sox.cache import Cache
    from sox.cli import _price_item
    from sox.ggg.trade import Listing

    class CappedRung:
        def search(self, query):
            return "q1", [f"h{i}" for i in range(100)], 10_000

        def fetch(self, query_id, hashes):
            return [Listing(amount=1.0, currency="exalted", account="a")
                    for _ in hashes]

    class EmptyExchange:
        def ids(self):
            return {}

    item = load("RareItem")
    priced = _price_item(
        item, {}, {"exalted": 1.0}, MODS, BASES, UNIQUES, NOTABLES,
        CappedRung(), Cache(tmp_path / "c.sqlite"),
        types.SimpleNamespace(status="any", max_searches=4, force=True),
        exchange=EmptyExchange(),
    )
    assert priced.source == "trade"
    assert priced.matches == 10_000


def test_a_waystone_without_a_book_is_unpriced_but_still_scored(tmp_path):
    """The wiring end to end: a waystone priced by search hands the report
    the stats the price rested on."""
    import types

    from sox.cache import Cache
    from sox.cli import _price_item
    from sox.ggg.trade import Listing

    class OneRung:
        def search(self, query):
            return "q1", [f"h{i}" for i in range(12)], 12

        def fetch(self, query_id, hashes):
            return [Listing(amount=2.0, currency="exalted", account="a")
                    for _ in hashes]

    item = load("RareMap")
    priced = _price_item(
        item, {}, {"exalted": 1.0}, MODS, BASES, UNIQUES, NOTABLES, OneRung(),
        Cache(tmp_path / "c.sqlite"),
        types.SimpleNamespace(status="any", max_searches=4, force=False),
    )
    # No exchange to hand (--no-trade): still no search, still the score.
    assert priced.source == "unpriced"
    assert priced.tag == "unpriced:no-book"
    assert priced.loot == (34, "reroll")
    assert priced.map_stats == ()


GALE_NAIL = """Item Class: Rings
Rarity: Rare
Gale Nail
Sapphire Ring
--------
Requirements:
Level: 60
--------
Item Level: 81
--------
+28% to Cold Resistance (implicit)
--------
{ Prefix Modifier "Flaming" (Tier: 3) }
29(26-30)% increased Fire Damage
{ Prefix Modifier "Catalyzing" (Tier: 3) }
27(26-30)% increased Chaos Damage
{ Suffix Modifier "of Legerdemain" (Tier: 4) }
24(20-25)% increased Cast Speed
{ Suffix Modifier "of the Lamprey" (Tier: 2) }
Leech 7.81(7-8)% of Physical Attack Damage as Life
"""


def test_an_explicit_is_searched_at_its_roll_range_floor():
    """Gale Nail, live: five minimums at the exact rolls matched 0 listings.
    Every same-tier near-copy rolled a point under on some axis — fire 28
    against 29, leech 7.76 against 7.81 — and the ladder, able only to drop
    whole mods, priced the ring at the 1 ex junk floor while the same-tier
    market sat at 3-20 ex. A roll one point lower is the same good at the
    same tier, so the floor of the roll's own range is the honest minimum,
    exactly as implicits already search."""
    item = itemtext.parse(GALE_NAIL)
    query = build_query(item, category_for(item), MODS, NOTABLES)
    mins = {f["id"]: f["value"]["min"]
            for f in query["query"]["stats"][0]["filters"] if "id" in f}
    assert mins["explicit.stat_3962278098"] == 26   # fire, rolled 29
    assert mins["explicit.stat_2891184298"] == 20   # cast speed, rolled 24
    assert mins["explicit.stat_2557965901"] == 7    # leech, rolled 7.81


CORRUPTION_HOLD = """Item Class: Rings
Rarity: Rare
Corruption Hold
Amethyst Ring
--------
Requirements:
Level: 52
--------
Item Level: 80
--------
+13(7-13)% to Chaos Resistance (implicit)
--------
{ Prefix Modifier "Ghastly" (Tier: 4) }
Minions deal 22(20-24)% increased Damage
{ Prefix Modifier "Sharp" (Tier: 5) }
Adds 9(8-11) to 18(16-19) Physical Damage to Attacks
{ Suffix Modifier "of the Order" (Tier: 3) }
Minions have 37(35-38)% increased Critical Hit Chance
{ Suffix Modifier "of Command" (Tier: 2) }
Minions have 10(9-11)% increased Attack and Cast Speed
{ Suffix Modifier "of the Bastion" (Tier: 6) }
+23(21-25)% to Chaos Resistance
"""


def test_the_archetype_survives_every_rung():
    """The archetype orders the whole ladder, so the report names it at every
    rung — read off the item's own mods, not off whichever filters a rung
    kept. Derived per rung, the name vanished exactly where the reader needs
    it most: a minion ring widened down to minion crit alone reported no
    archetype at all."""
    item = itemtext.parse(CORRUPTION_HOLD)
    for step in range(len(RELAX_STEPS)):
        group, _ = explain_selection(item, MODS, NOTABLES, relax=step)
        assert group == "minion", f"rung {step} lost the archetype"


def test_unrelated_mods_drop_before_generic_value():
    """Survival order: identity, archetype mods, anointed notables, generic
    value, unrelated mods last. On this ring the attack mod outlived a 36%
    chaos-res total — but the buyer is a minion build who filters minion
    mods and resistance, and nobody filters attack damage on a minion ring:
    it constrains the comparables while describing no buyer."""
    item = itemtext.parse(CORRUPTION_HOLD)
    # Rung 1 keeps four axes: the minion cluster and the res total. The
    # attack mod is the first thing widening gives up.
    ids = _stat_ids(build_query(item, category_for(item), MODS, NOTABLES, relax=1))
    assert "explicit.stat_3032590688" not in ids, "attack mod must drop first"
    assert "pseudo.pseudo_total_chaos_resistance" in ids
    # Rung 2, three axes: the cluster alone.
    ids = _stat_ids(build_query(item, category_for(item), MODS, NOTABLES, relax=2))
    assert "pseudo.pseudo_total_chaos_resistance" not in ids
    assert len(ids) == 3


def test_an_anointed_notable_drops_before_the_archetype_mods():
    """An anoint can be re-anointed, so it is not identity the way a
    Megalomaniac's rolled notables are: it rides behind the archetype mods
    and widening gives it up before them."""
    item = load("EnhancementAmulet")
    exact = _stat_ids(build_query(item, category_for(item), MODS, NOTABLES))
    assert NOTABLES["The Soul Meridian"] in exact
    deep = _stat_ids(build_query(item, category_for(item), MODS, NOTABLES, relax=3))
    assert NOTABLES["The Soul Meridian"] not in deep
    assert deep, "the mods are what survive, not the anoint"


def test_conditional_minion_damage_counts_toward_the_cluster():
    """Corruption Hold's fourth minion mod scored +0, was never searched at
    any rung, and did not count toward coherence — the wording was simply
    missing from the curated allowlist while GGG's stats table carries it."""
    from sox.valuation.mods import coherence_keys, match_mod

    entry = match_mod(
        "Minions deal 25% increased Damage if you've Hit Recently", MODS)
    assert entry is not None
    assert "minion" in coherence_keys(entry)


def test_an_added_damage_mod_keeps_its_average_minimum():
    """The trade filter for "Adds # to #" compares the AVERAGE of the two
    numbers, and modRanges holds one roll's range — not the average's — so
    flooring it would compare the wrong quantity. 12 to 31 averages 21.5."""
    item = load("RingImplicitTwin")
    query = build_query(item, category_for(item), MODS, NOTABLES)
    mins = [f["value"]["min"] for group in query["query"]["stats"]
            for f in group["filters"] if "id" in f]
    assert 21.5 in mins


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

    Rungs 1 and 2 built the identical query, so a search was spent replaying
    the previous one and the ladder arrived a rung late.

    The whole-item rung is exempt: on an item carrying no more stats than the
    next rung's cap it IS that rung, and the pricer skips the repeat rather
    than paying for it.
    """
    item = load("SpiritLifeChest")
    seen = [query_hash(build_query(item, category_for(item), MODS, NOTABLES,
                                   relax=step))
            for step in range(1, len(RELAX_STEPS))]
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
    # Generic value no longer elects the buyer, so "defence" is not the
    # item's archetype — but the fold into the pseudo total still shows.
    assert tags["+121 to maximum Life"] == "pseudo"
    assert tags["+24% to Cold Resistance"] == "pseudo"
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


def test_a_pseudo_fed_implicit_reads_as_part_of_the_total():
    """The ring's implicit chaos res is summed into the pseudo total.

    Its breakdown row said "(implicit)" alone, which reads as a separate
    implicit filter the query does not contain.
    """
    from sox.valuation.candidates import score_rows

    rows = {text: tag for text, _, tag in
            score_rows(load("MinionRing"), MODS, load_bases())}
    assert rows["+13% to Chaos Resistance"] == "implicit, pseudo"


def test_highlights_respect_the_rung_that_priced():
    """One assembly decides what the breakdown lights up.

    cli appended every implicit unconditionally, so the ring's implicit lit
    up cyan at a rung that had dropped the chaos total it rides in.
    """
    from sox.valuation.query import defence_mod_texts, searched_item_texts

    ring = load("MinionRing")
    lit = searched_item_texts(ring, MODS, NOTABLES, relax=1)
    assert "+13% to Chaos Resistance" not in lit, "its total was dropped"
    assert "+13% to Chaos Resistance" in searched_item_texts(
        ring, MODS, NOTABLES, relax=0), "at rung 0 the total is searched"

    armour = load("ArmourHighValueRareItem")
    covered = defence_mod_texts(armour)
    assert covered, "fixture must carry defence mods"
    lit = searched_item_texts(armour, MODS, NOTABLES)
    assert set(covered) <= set(lit), "equipment-filter mods are searched too"


def test_explain_query_prints_each_filter_with_its_floor():
    from sox.valuation.query import explain_query

    ring = load("MinionRing")
    exact = explain_query(ring, MODS, NOTABLES, relax=0)
    assert "total chaos resistance ≥ 30" in exact, "implicit 10 + explicit 20"
    assert "Minions deal #% increased Damage ≥ 20" in exact
    relaxed = explain_query(ring, MODS, NOTABLES, relax=1)
    assert not any("chaos" in line for line in relaxed), "dropped at rung 1"


def test_unsearched_rows_name_the_mods_the_price_ignores():
    """The ring's chaos res and phys damage were in the rung-0 query and the
    widened rung dropped them; the Warden's deflection mods were never
    searchable at all. The section must say which is which."""
    from sox.valuation.candidates import unsearched_rows

    ring = load("MinionRing")
    rows = dict(unsearched_rows(ring, MODS, NOTABLES, relax=1))
    assert rows["+23% to Chaos Resistance"] == "widened away"
    assert rows["Adds 9 to 18 Physical Damage to Attacks"] == "widened away"
    assert rows["+13% to Chaos Resistance"] == "widened away"
    assert not any("Minions" in text for text in rows)

    assert unsearched_rows(ring, MODS, NOTABLES, relax=0) == []

    warden = load("ForgottenWardenTwiceCorrupted")
    rows = dict(unsearched_rows(warden, MODS, NOTABLES, relax=0))
    assert rows["+76 to Deflection Rating per 50 missing Energy Shield"] == "unsearchable"


def test_an_unset_rings_skill_slot_implicit_is_searched():
    """"Grants 1 additional Skill Slot" reported (unsearchable) on a rare —
    a lie: flag_mods carries implicit.stat_958696139 for it, and the slot is
    the whole reason an Unset Ring is bought. An implicit flag is the BASE's
    identity, so it must search at any rarity and survive every rung — a
    rare's search is not base-pinned, and this is what keeps the comparables
    Unset Rings."""
    from sox.valuation.candidates import unsearched_rows

    ring = load("UnsetRing")
    slot = {"id": "implicit.stat_958696139", "value": {}}
    query = build_query(ring, "accessory.ring", MODS, NOTABLES)
    assert slot in query["query"]["stats"][0]["filters"]

    group, _ = explain_selection(ring, MODS, NOTABLES)
    assert group != "variant", "a rare's archetype is its mods, not the flag"

    rows = dict(unsearched_rows(ring, MODS, NOTABLES))
    assert "Grants 1 additional Skill Slot" not in rows

    bare = build_query(ring, "accessory.ring", MODS, NOTABLES,
                       relax=len(RELAX_STEPS) - 1)
    assert slot in bare["query"]["stats"][0]["filters"], \
        "never priced as a bare ring"


def test_generic_value_does_not_elect_the_buyer():
    """Three resistance rolls out-voted the ring's minion mod: their
    "elemental" tag crowned an elemental buyer, the minion mod became
    unrelated, and widening dropped the one mod the market prices the ring
    on. Generic value is paid by every buyer — it cannot say who the buyer
    IS. Measured live: the comparables carrying the minion mod listed at
    5-49 div while the res-and-fire search floored at 75 ex."""
    from sox.valuation.mods import dominant_archetype, matched
    from sox.valuation.query import searchable_mods

    ring = load("UnsetRing")
    group, _ = dominant_archetype(matched(searchable_mods(ring), MODS))
    assert group is None, "fire 1, minion 1 — the res votes must not count"

    _, searched = explain_selection(ring, MODS, NOTABLES, relax=2)
    assert "Minions deal #% increased Damage if you've Hit Recently" in searched


def test_the_query_names_its_rarity_pin():
    """The Oaksworn question: a unique searched by name printed nothing that
    said so, and the reader had to ask what market 603 listings were."""
    from sox.valuation.query import explain_query

    assert explain_query(load("MinionRing"), MODS, NOTABLES)[0] == "rare"

    oaksworn = itemtext.parse(
        "Item Class: Shields\nRarity: Unique\nOaksworn\nSigil Crest Shield\n"
        "--------\nArmour: 379\n--------\nItem Level: 80\n--------\n"
        "{ Unique Modifier }\n+17(13-20)% to Chaos Resistance\n"
    )
    line = explain_query(oaksworn, MODS, NOTABLES)[0]
    assert line == "Oaksworn · unique · Sigil Crest Shield"


def test_a_resistance_penalty_is_not_summed_as_resistance():
    """Live, a Rondel of Fragility walked every rung to the bare-name search.

    Its "-30(30-30)% to all Elemental Resistances" was read with the floor at
    +30, so the pseudo total asked for 90 resistance from an item defined by
    LOSING 30. Totals lead the ladder, so the impossible filter survived to
    the last mod rung and all of them returned nothing. The fixture is
    reconstructed from the item's five live query hashes, every one of which
    it reproduces.
    """
    import re

    from sox.valuation.query import explain_query, pseudo_totals
    from sox.valuation.rolls import parse_values

    item = load("RondelOfFragility")
    assert pseudo_totals(item, MODS) == [], "a penalty is not a total"
    lines = explain_query(item, MODS, NOTABLES)
    assert "#% to all Elemental Resistances ≥ -30" in lines
    assert not any(line.startswith("total elemental") for line in lines)

    # Without advanced descriptions the value itself has to carry the sign.
    plain = re.sub(r"\((-?\d+)-(-?\d+)\)", "",
                   (FIXTURES / "RondelOfFragility.txt").read_text())
    assert parse_values("-30% to all Elemental Resistances") == [-30.0]
    item = itemtext.parse(plain)
    assert pseudo_totals(item, MODS) == []
    assert "#% to all Elemental Resistances ≥ -30" in explain_query(item, MODS, NOTABLES)


def test_flat_energy_shield_is_searched_where_it_is_global():
    """On an amulet "+30 to maximum Energy Shield" is a global stat with no
    equipment total to ride, so unlisted it could not be searched at all: a
    Rondel of Fragility reported its implicit as unsearchable. On armour the
    same wording is local and already inside the ES total, so it must stay
    out of the stat filters there or it is asked for twice.
    """
    from sox.valuation.query import explain_query

    item = load("RondelOfFragility")
    assert "# to maximum Energy Shield ≥ 20" in explain_query(item, MODS, NOTABLES)
    query = build_query(item, category_for(item), MODS, NOTABLES)
    ids = [f["id"] for f in query["query"]["stats"][0]["filters"]]
    assert "implicit.stat_3489782002" in ids, "the implicit twin, at its floor"
    unsearched = candidates.unsearched_rows(item, MODS, NOTABLES)
    assert not any("Energy Shield" in text for text, _why in unsearched)

    chest = load("SpiritLifeChest")
    query = build_query(chest, category_for(chest), MODS, NOTABLES)
    ids = [f.get("id", "") for f in query["query"]["stats"][0]["filters"]]
    assert not any(i.endswith(("3489782002", "4052037485")) for i in ids), \
        "on armour the ES total already carries it"
    assert "es" in query["query"]["filters"]["equipment_filters"]["filters"]


def test_a_good_roll_on_a_unique_at_the_listing_floor_takes_the_index_price():
    """Erian's Cobble: fifteen mods, every one rolling from 0, indexed at
    1 ex. Almost every copy carries some roll in the top quarter — the odds
    are 1 - 0.75^15 — and each one spent two searches to learn it was worth
    1 ex. At the listing floor a roll has nothing to multiply; Forgotten
    Warden at 9 ex still escalates."""
    from dataclasses import replace

    from sox.valuation.candidates import should_search_unique

    item, entry = _forgotten_warden(dex=29, life=32)
    assert should_search_unique(item, entry, UNIQUES) == "good-roll"
    assert should_search_unique(item, replace(entry, price_ex=1.0), UNIQUES) is None


ERIANS_COBBLE = """Item Class: Helmets
Rarity: Unique
Erian's Cobble
Guarded Helm
--------
Item Level: 80
--------
+8(0-10) to Strength
+3(0-10) to Dexterity
+24(0-30) to maximum Life
"""


def test_a_uniques_mods_are_searched_at_their_roll_not_the_range_floor():
    """Every Erian's Cobble mod rolls from 0, so its range floor asks for any
    copy at all: 112 listings at 1 ex, and the roll that escalated it never
    in the query. On a unique the roll is the only thing separating copies —
    the defences already search that way. The tier-floor argument is a
    rare's: there is no same-tier near-copy of a unique, only a worse one."""
    item = itemtext.parse(ERIANS_COBBLE)
    query = build_query(item, category_for(item), MODS, NOTABLES)
    mins = {f["id"]: f["value"]["min"]
            for f in query["query"]["stats"][0]["filters"] if "id" in f}
    assert mins["explicit.stat_4080418644"] == 8    # strength, range 0-10
    assert mins["explicit.stat_3299347043"] == 24   # life, range 0-30


def test_a_waystone_worth_juicing_is_searched_on_its_totals(tmp_path):
    """From 80 up a stone is worth a search: the
    comparable is one at least as good on all five totals, which the bulk
    book by tier cannot say. Below that, the book."""
    import types

    from sox.cache import Cache
    from sox.cli import _price_item
    from sox.ggg.trade import Listing
    from sox.valuation.query import loot_score

    class OneRung:
        def __init__(self):
            self.queries = []

        def search(self, query):
            self.queries.append(query)
            return "q1", [f"h{i}" for i in range(12)], 12

        def fetch(self, query_id, hashes):
            return [Listing(amount=2.0, currency="exalted", account="a")
                    for _ in hashes]

    item = load("RareMapFakeAllProps")
    assert loot_score(item) == (83, "juice it")
    trade = OneRung()
    priced = _price_item(
        item, {}, {"exalted": 1.0}, MODS, BASES, UNIQUES, NOTABLES, trade,
        Cache(tmp_path / "c.sqlite"),
        types.SimpleNamespace(status="any", max_searches=4, force=False),
    )
    assert priced.source == "trade"
    assert trade.queries[0]["query"]["filters"]["map_filters"]["filters"]["map_rare_monsters"] == {"min": 32}
    assert "monster rarity 32%+" in priced.map_stats
    assert priced.loot == (83, "juice it")
    assert priced.instill is not None and priced.instill.blocked == "corrupted"


def test_a_stone_under_the_search_gate_is_not_searched(tmp_path):
    import types

    from sox.cache import Cache
    from sox.cli import _price_item
    from sox.valuation.query import loot_score

    class NoSearch:
        def search(self, query):
            raise AssertionError("under 80 is the book's to price")

        fetch = search

    item = load("GhostExpedition")
    assert loot_score(item) == (65, "run it")
    priced = _price_item(
        item, {}, {"exalted": 1.0}, MODS, BASES, UNIQUES, NOTABLES, NoSearch(),
        Cache(tmp_path / "c.sqlite"),
        types.SimpleNamespace(status="any", max_searches=4, force=False),
    )
    assert priced.source == "unpriced" and priced.tag == "unpriced:no-book"


def test_a_searched_stone_with_no_comparable_falls_back_to_the_book_as_a_floor(tmp_path):
    """Nothing at least as good on all five totals is listed: that is what
    the search says, and it is worth knowing. But the stone is still at
    least a Waystone (Tier 16), and the book by tier is the floor."""
    import types

    from sox.cache import Cache
    from sox.cli import _price_item
    from sox.ggg.exchange import Book, Offer

    class NothingListed:
        def search(self, query):
            return "q1", [], 0

        def fetch(self, query_id, hashes):
            return []

    class BulkBook:
        def ids(self):
            return {"Waystone (Tier 16)": "waystone-16"}

        def book(self, item_id, have="exalted"):
            if item_id == "waystone-16" and have == "exalted":
                return Book([Offer(ratio=24.0, stock=500)], 2501)
            return Book([], 0)

    item = load("RareMapFakeAllProps")
    priced = _price_item(
        item, {}, {"exalted": 1.0}, MODS, BASES, UNIQUES, NOTABLES,
        NothingListed(), Cache(tmp_path / "c.sqlite"),
        types.SimpleNamespace(status="any", max_searches=4, force=False),
        exchange=BulkBook(),
    )
    assert priced.source == "exchange"
    assert priced.tag == "waystone-floor"
    assert priced.price_ex == 24.0
    assert "monster rarity 32%+" in priced.map_stats, "what the search asked is still shown"
    assert priced.loot == (83, "juice it")


def test_with_the_search_down_gear_is_priced_without_it(tmp_path):
    """Search is in a lockout: the item is not queued behind it, it is
    priced with what is left — the index, the book, the loot score — and
    says the search was down."""
    import types

    from sox.cache import Cache
    from sox.cli import _price_item

    class Down:
        def down(self):
            return 1700.0

        def search(self, query):
            raise AssertionError("search is down; it must not be called")

        fetch = search

    item = load("RareItem")
    priced = _price_item(
        item, {}, {"exalted": 1.0}, MODS, BASES, UNIQUES, NOTABLES, Down(),
        Cache(tmp_path / "c.sqlite"),
        types.SimpleNamespace(status="any", max_searches=4, force=True),
    )
    assert priced.source == "unpriced"
    assert priced.tag == "unpriced:search-down"
    assert priced.search_down == 1700.0


def test_with_the_search_down_a_stone_keeps_its_loot_and_its_book(tmp_path):
    import types

    from sox.cache import Cache
    from sox.cli import _price_item
    from sox.ggg.exchange import Book, Offer

    class Down:
        def down(self):
            return 1700.0

        def search(self, query):
            raise AssertionError("search is down; it must not be called")

        fetch = search

    class BulkBook:
        def ids(self):
            return {"Waystone (Tier 16)": "waystone-16"}

        def book(self, item_id, have="exalted"):
            if item_id == "waystone-16" and have == "exalted":
                return Book([Offer(ratio=24.0, stock=500)], 2501)
            return Book([], 0)

    item = load("RareMapFakeAllProps")   # loot 83: would search, if it could
    priced = _price_item(
        item, {}, {"exalted": 1.0}, MODS, BASES, UNIQUES, NOTABLES, Down(),
        Cache(tmp_path / "c.sqlite"),
        types.SimpleNamespace(status="any", max_searches=4, force=False),
        exchange=BulkBook(),
    )
    assert priced.source == "exchange" and priced.price_ex == 24.0
    assert priced.loot == (83, "juice it")
    assert priced.search_down == 1700.0
