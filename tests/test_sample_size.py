"""A handful of listings is not a market.

A quarterstaff worth about 3ex once reported 320ex: three listings matched,
and the cheapest of those three was a far better item priced at a divine.
Pricing must keep widening until the sample means something.
"""

from pathlib import Path

from sox import itemtext
from sox.cache import Cache
from sox.ggg.trade import Listing
from sox.valuation.allowlists import load_bases, load_mods, load_notables
from sox.valuation.mods import build_index
from sox.valuation.query import category_for
from sox.valuation.trade_pricer import MIN_SAMPLE, price_by_search

MODS = build_index(load_mods())
NOTABLES = load_notables()
BASES = load_bases()
FIXTURES = Path(__file__).parent / "fixtures" / "items"
RATES = {"exalted": 1.0, "divine": 320.0}
ITEM = itemtext.parse(
    (Path(__file__).parent / "fixtures" / "items" / "RareItem.txt").read_text()
)


class ScriptedTrade:
    """Returns a scripted (count, prices) per successive relaxation rung."""

    def __init__(self, rungs):
        self.rungs = rungs
        self.searches = 0

    def search(self, query):
        count, _ = self.rungs[min(self.searches, len(self.rungs) - 1)]
        self.searches += 1
        return f"q{self.searches}", [f"h{i}" for i in range(count)], count

    def fetch(self, query_id, hashes):
        _, prices = self.rungs[min(self.searches - 1, len(self.rungs) - 1)]
        return [Listing(amount=p, currency="exalted", account="a") for p in prices]


def price(trade, tmp_path):
    return price_by_search(
        ITEM, category_for(ITEM), MODS, NOTABLES, trade,
        Cache(tmp_path / "c.sqlite"), RATES,
    )


def test_keeps_relaxing_when_the_sample_is_too_small(tmp_path):
    """Three listings must not settle the price when widening finds a market."""
    trade = ScriptedTrade([
        (3, [320.0, 400.0, 500.0]),          # the outlier-only rung
        (12, [1.0, 2.0, 3.0, 4.0, 40.0]),    # the real market
    ])
    result = price(trade, tmp_path)
    assert trade.searches >= 2, "must not stop at a 3-listing sample"
    assert result.ceiling_ex == 1.0
    assert result.confidence == "firm"


def test_accepts_a_thin_sample_only_after_exhausting_the_ladder(tmp_path):
    """Scarce items still get an answer — labelled, not silently confident."""
    trade = ScriptedTrade([(3, [320.0, 400.0, 500.0])])
    result = price(trade, tmp_path)
    assert result.ceiling_ex == 320.0
    assert result.confidence == "thin"
    assert trade.searches > 1, "should have tried to widen first"


def test_median_is_reported_alongside_the_low(tmp_path):
    """The cheapest listing alone hides a skewed distribution."""
    trade = ScriptedTrade([(12, [1.0, 2.0, 3.0, 100.0, 200.0])])
    result = price(trade, tmp_path)
    assert result.ceiling_ex == 1.0
    assert result.median_ex == 3.0


def test_firm_requires_a_real_sample():
    assert MIN_SAMPLE >= 8


def test_ask_ignores_a_dump_listing(tmp_path):
    """One person dumping at 0.2ex does not make the item worth 0.2ex.

    A low far under the body of the market is a dump, and an ask derived from
    it tells you to give the item away.
    """
    trade = ScriptedTrade([(12, [0.2, 30.0, 36.0, 40.0, 45.0])])
    result = price(trade, tmp_path)
    assert result.ceiling_ex == 0.2, "the low is still reported"
    assert result.skewed is True
    assert result.suggested_ask_ex > 1.0, "ask must not follow the dump listing"


def test_ask_follows_the_low_when_the_market_is_tight(tmp_path):
    trade = ScriptedTrade([(12, [10.0, 11.0, 12.0, 13.0])])
    result = price(trade, tmp_path)
    assert result.skewed is False
    assert result.suggested_ask_ex == 9.0     # 10 * 0.9


def test_result_records_which_rung_priced_it(tmp_path):
    """The explanation must describe the search that actually ran."""
    trade = ScriptedTrade([
        (0, []),
        (0, []),
        (12, [5.0, 6.0, 7.0]),
    ])
    result = price(trade, tmp_path)
    assert result.relax_used == 2
    assert result.tag == "relaxed:2"


def test_explanation_matches_the_rung_used():
    """Rung 2 keeps 2 stats, so the display must not show 3."""
    from sox.valuation.query import RELAX_STEPS, explain_selection

    at_rung_2 = RELAX_STEPS[2]
    _, stats = explain_selection(ITEM, MODS, NOTABLES, relax=2)
    assert len(stats) <= at_rung_2


def test_minimums_are_never_lowered():
    """Searching below your own values prices a WORSE item, not yours."""
    from sox.valuation.query import build_query, category_for

    def minimums(relax):
        q = build_query(ITEM, category_for(ITEM), MODS, NOTABLES, relax=relax)
        return {f["id"]: f["value"].get("min") for f in q["query"]["stats"][0]["filters"]}

    strict = minimums(0)
    for rung in range(1, 4):
        widened = minimums(rung)
        # Fewer stats each rung, but every surviving minimum is unchanged.
        assert len(widened) <= len(strict)
        for stat_id, value in widened.items():
            assert value == strict[stat_id], f"rung {rung} lowered {stat_id}"


def test_widening_drops_the_worst_tier_mod_first():
    """Tier 1 is the best roll, so the highest tier number goes first."""
    from sox.valuation.query import build_query, category_for

    item = itemtext.parse(
        "Item Class: Bows\nRarity: Rare\nTest Bow\nRider Bow\n"
        "--------\nItem Level: 80\n--------\n"
        '{ Prefix Modifier "Best" (Tier: 1) }\nAdds 5(1-5) to 82(62-89) Lightning Damage\n'
        '{ Prefix Modifier "Worst" (Tier: 9) }\nAdds 9(6-9) to 13(10-15) Cold Damage\n'
    )
    narrow = build_query(item, category_for(item), MODS, NOTABLES, relax=3)
    kept = [f["id"] for f in narrow["query"]["stats"][0]["filters"]]
    assert len(kept) == 1
    lightning = MODS[__import__("sox.valuation.mods", fromlist=["x"]).normalize_mod(
        "Adds 5 to 82 Lightning Damage")]
    assert kept[0] == lightning.ids[0], "the tier 1 mod must survive"


def test_cohering_mods_are_searched_first_but_others_are_not_dropped():
    """Every allowlisted mod belongs in the search; coherence sets the order.

    A chaos resistance roll adds value whether or not it serves the item's
    main archetype, so it is only dropped when the ladder narrows.
    """
    from sox.valuation.query import explain_selection

    item = itemtext.parse(
        "Item Class: Amulets\nRarity: Rare\nMixed\nLapis Amulet\n"
        "--------\nItem Level: 82\n--------\n"
        "{ Prefix Modifier }\n+3 to Level of all Spell Skills\n"
        "{ Prefix Modifier }\n25% increased Cast Speed\n"
        "{ Suffix Modifier }\n40% increased Spell Damage\n"
        "{ Suffix Modifier }\n18% increased Attack Speed\n"
    )
    group, stats = explain_selection(item, MODS, NOTABLES, relax=0)
    assert group == "spell"
    assert stats.index("#% increased Cast Speed") < stats.index("#% increased Attack Speed"), \
        "cohering mods come first"

    # At the narrowest rung only the cohering mods survive.
    _, narrow = explain_selection(item, MODS, NOTABLES, relax=3)
    assert "#% increased Attack Speed" not in narrow


def test_coherence_reports_but_does_not_gate_the_search():
    """Coherence picks WHICH stats to search on, not WHETHER to search.

    A low score is worth reporting — it says the item is unremarkable — but
    withholding the price on account of it answers a question nobody asked.
    """
    from sox.cli import wants_search
    from sox.valuation import candidates
    from sox.valuation.allowlists import load_bases, load_uniques

    # Corrupted, so no open-affix bonus, and only a weight-1 resistance.
    item = itemtext.parse(
        "Item Class: Helmets\nRarity: Rare\nDragon Visor\nFreebooter Cap\n"
        "--------\nItem Level: 70\n--------\n"
        "{ Suffix Modifier }\n+8% to Fire Resistance\n"
        "--------\nCorrupted\n"
    )
    verdict = candidates.assess(item, None, MODS, load_bases(), load_uniques())
    assert not verdict.should_search, "still scored as unremarkable"
    assert verdict.score >= 0, "and the score is still reported"
    assert wants_search(verdict, None, item), "but it is priced anyway"


def test_local_defences_are_searched_as_equipment_filters():
    """Flat and percent Armour/Evasion/ES are not stats here.

    The displayed total already contains them, so the total is what gets
    searched. Listing them as stats too would constrain the same thing twice,
    and on the global stat id, which matches nothing on a helmet.
    """
    from sox.valuation.mods import normalize_mod
    from sox.valuation.query import build_query, category_for

    for text in ("# to maximum Energy Shield", "#% increased Evasion Rating",
                 "#% increased Armour"):
        assert normalize_mod(text) not in MODS, f"{text} must not be a stat"

    helmet = itemtext.parse(
        "Item Class: Helmets\nRarity: Rare\nDragon Visor\nFreebooter Cap\n"
        "--------\nEvasion Rating: 485\n--------\nItem Level: 81\n--------\n"
        "{ Prefix Modifier }\n+145(117-150) to Evasion Rating\n"
    )
    query = build_query(helmet, category_for(helmet), MODS, NOTABLES)
    # 457 at the worst roll, filed at 20% quality like every value the ev
    # filter compares against.
    assert query["query"]["filters"]["equipment_filters"]["filters"]["ev"] == {
        "min": round(457 * 1.2)}


def test_equipment_minimum_normalises_to_the_worst_roll():
    """485 Evasion with a +145 mod that could roll 117 is really a 457 item.

    Asking for 485 would exclude the identical item with a worse roll, which
    is exactly a comparable.
    """
    from sox.valuation.query import DEFENCE_PROPERTIES, equipment_minimum

    filter_id, flat, percent = DEFENCE_PROPERTIES["Evasion Rating"]
    assert filter_id == "ev"
    item = itemtext.parse(
        "Item Class: Helmets\nRarity: Rare\nX\nFreebooter Cap\n"
        "--------\nEvasion Rating: 485\n--------\nItem Level: 81\n--------\n"
        "{ Prefix Modifier }\n+145(117-150) to Evasion Rating\n"
    )
    worst = 485 - 145 + 117
    assert equipment_minimum(item, "Evasion Rating", flat, percent) == round(worst * 1.2)

    # With no roll range reported, the total stands as-is.
    plain = itemtext.parse(
        "Item Class: Helmets\nRarity: Rare\nX\nFreebooter Cap\n"
        "--------\nEvasion Rating: 485\n--------\nItem Level: 81\n"
    )
    assert equipment_minimum(plain, "Evasion Rating", flat, percent) == round(485 * 1.2)


def test_a_weapon_is_searched_on_its_dps():
    """DPS is what a weapon is shopped on — the number the tooltip already
    worked out, not the mods behind it.

    Attack speed, crit and sockets stay unconstrained: speed and crit are
    traded off against damage rather than added to it, so a minimum on either
    excludes comparables rather than weak items.
    """
    from sox.valuation.query import build_query, category_for

    staff = itemtext.parse(
        "Item Class: Quarterstaves\nRarity: Rare\nX\nBolting Quarterstaff\n"
        "--------\nPhysical Damage: 54-219\nCold Damage: 121-183\n"
        "Attacks per Second: 1.40\n"
        "--------\nItem Level: 81\n--------\n"
        "{ Prefix Modifier }\nAdds 121 to 183 Cold Damage\n"
    )
    equipment = build_query(staff, category_for(staff), MODS, NOTABLES)[
        "query"]["filters"]["equipment_filters"]["filters"]
    # (136.5 physical filed at 20% quality + 152 cold) * 1.40. Quality raises
    # physical damage and nothing else, and the dps the filter compares
    # against is filed at 20% of it — measured on four listed maces.
    #
    # Total only: splitting it into pdps and edps pins the SOURCE, and a
    # weapon reaching the same DPS through fire instead of cold is a
    # comparable. Live, all three filters left 65 matches where DPS alone
    # left 995.
    assert equipment == {"dps": {"min": round((136.5 * 1.2 + 152) * 1.40, 1)}}


def test_a_damage_mod_covered_by_dps_is_not_also_a_stat_filter():
    """Left as a stat filter it would be asked for twice — and worse, it pins
    the SOURCE: a weapon with the same elemental DPS rolled as fire instead of
    cold is a comparable, and "Adds # to # Cold Damage" excludes it."""
    from sox.valuation.query import build_query, category_for, defence_mod_texts

    mace = itemtext.parse(
        "Item Class: One Hand Maces\nRarity: Rare\nPain Ram\nBandit Mace\n"
        "--------\nPhysical Damage: 100-200\nCold Damage: 58-97\n"
        "Attacks per Second: 1.55\n--------\nItem Level: 80\n--------\n"
        "{ Prefix Modifier }\nAdds 58(40-60) to 97(80-110) Cold Damage\n"
        "{ Prefix Modifier }\n127(100-129)% increased Physical Damage\n"
        "{ Suffix Modifier }\n+2 to Level of all Attack Skills\n"
    )
    assert defence_mod_texts(mace) == [
        "Adds 58 to 97 Cold Damage", "127% increased Physical Damage"]
    ids = [f["id"] for g in build_query(mace, category_for(mace), MODS, NOTABLES)[
        "query"]["stats"] for f in g["filters"]]
    assert ids == ["explicit.stat_3035140377"], "only the attack-level mod is left"


def test_a_weapon_dps_drops_its_runes():
    """DPS strips runes, the same as the defences do.

    Both sides come off: the floor is built rune-free here, and
    `meets_without_runes` recomputes each listing rune-free before comparing.
    Stripping only the floor would admit every weapon between our real DPS
    and our stripped one — on one mace that gap was 448 against 482 and the
    cheapest match fell from 1 divine to 29 exalted. That is an argument for
    stripping the listings too, not for keeping our own runes.

    The two items below differ by one 36% rune. 728.5 average damage against
    100% of our own is a 364.25 base; the runed copy divides by 2.36 instead
    of 2 and rebuilds at 2, so 617.4 average and 1278 dps. Both are then
    filed at 20% quality, which these carry none of.
    """
    from sox.valuation.query import damage_filters

    text = ("Item Class: Crossbows\nRarity: Rare\nX\nSiege Crossbow\n"
            "--------\nPhysical Damage: 414-1,043\nAttacks per Second: 2.07\n"
            "--------\nItem Level: 79\n--------\n"
            "{ Prefix Modifier }\n100(80-120)% increased Physical Damage\n")
    bare = itemtext.parse(text)
    runed = itemtext.parse(text + "--------\n36% increased Physical Damage (rune)\n")
    assert damage_filters(bare)["dps"] == {"min": 1508.0 * 1.2}, "nothing to strip"
    assert damage_filters(runed)["dps"] == {"min": 1278.0 * 1.2}


def test_requirements_are_capped_not_floored():
    """A requirement is a cost, not a benefit.

    An item demanding less than ours is strictly easier to equip and is a
    comparable; one demanding more is not. It is also what separates a Bandit
    Mace from the whole one-handed mace category.
    """
    from sox.valuation.query import build_query, category_for

    mace = itemtext.parse(
        "Item Class: One Hand Maces\nRarity: Rare\nPain Ram\nBandit Mace\n"
        "--------\nPhysical Damage: 100-211\nAttacks per Second: 1.45\n"
        "--------\nRequires: Level 60, 104 Str\n"
        "--------\nItem Level: 80\n--------\n"
        "{ Suffix Modifier }\n+2 to Level of all Attack Skills\n"
    )
    assert mace["requirements"] == {"lvl": 60, "str": 104}
    filters = build_query(mace, category_for(mace), MODS, NOTABLES)["query"]["filters"]
    assert filters["req_filters"]["filters"] == {
        "lvl": {"max": 60}, "str": {"max": 104}
    }


def test_added_damage_filters_on_the_average():
    """The API compares the average of "Adds X to Y", not the low roll.

    Verified live: cold min=121 (the low) returns 29 results while min=152
    (the average) returns 8. Passing the low asks for items at least as good
    as the BOTTOM of the range, which is a different question.
    """
    from sox.valuation.query import build_query, category_for, filter_value

    assert filter_value("Adds # to # Cold Damage", [121.0, 183.0]) == 152.0
    assert filter_value("# to maximum Life", [96.0]) == 96.0

    item = itemtext.parse(
        "Item Class: Quarterstaves\nRarity: Rare\nX\nBolting Quarterstaff\n"
        "--------\nItem Level: 81\n--------\n"
        "{ Prefix Modifier }\nAdds 121 to 183 Cold Damage\n"
    )
    query = build_query(item, category_for(item), MODS, NOTABLES)
    mins = [f["value"]["min"] for f in query["query"]["stats"][0]["filters"]]
    assert 152.0 in mins


def test_default_status_is_instant_buyout():
    """Only a listing someone can actually complete is evidence of a price.

    "any" includes listings nobody can buy — a seller who quit the league
    still shows a number, and the cheapest-match ceiling lands on exactly
    those. Not "online" either: PoE2 trade is asynchronous, and filtering to
    online sellers cut one query from 918 listings to 1, which then priced a
    3ex item in the hundreds.
    """
    from sox.config import Config
    from sox.valuation.query import build_query, category_for

    assert Config().status == "securable"
    query = build_query(ITEM, category_for(ITEM), MODS, NOTABLES,
                        status=Config().status)
    assert query["query"]["status"]["option"] == "securable"


def test_a_cached_price_does_not_claim_a_search(tmp_path):
    """Replaying a stored result costs no API call.

    Reporting the count from when it was first computed makes a free lookup
    look like a fresh one, which hides how much of a session was cached.
    """
    cache = Cache(tmp_path / "c.sqlite")
    first = ScriptedTrade([(12, [5.0, 6.0, 7.0])])
    price_by_search(ITEM, category_for(ITEM), MODS, NOTABLES, first, cache, RATES)
    assert first.searches == 1

    second = ScriptedTrade([(12, [5.0, 6.0, 7.0])])
    result = price_by_search(ITEM, category_for(ITEM), MODS, NOTABLES, second,
                             cache, RATES)
    assert second.searches == 0, "must not hit the API again"
    assert result.from_cache is True
    assert result.searches_used == 0


def test_percent_defence_mods_are_normalised_multiplicatively():
    """A percent roll scales the base, so it cannot be subtracted.

    A sceptre showing 152 Spirit from a 52%(51-55) roll has a base of 100, so
    at the worst roll it would show 151.
    """
    from sox.valuation.query import DEFENCE_PROPERTIES, equipment_minimum

    _, flat, percent = DEFENCE_PROPERTIES["Spirit"]
    item = itemtext.parse(
        "Item Class: Sceptres\nRarity: Rare\nX\nRattling Sceptre\n"
        "--------\nSpirit: 152\n--------\nItem Level: 81\n--------\n"
        "{ Prefix Modifier }\n52(51-55)% increased Spirit\n"
    )
    assert equipment_minimum(item, "Spirit", flat, percent) == 151


def test_block_and_ward_are_equipment_filters_not_stats():
    from sox.valuation.mods import normalize_mod

    for text in ("#% increased Block chance", "# to maximum Runic Ward",
                 "#% increased maximum Runic Ward"):
        assert normalize_mod(text) not in MODS, f"{text} must not be a stat"


def test_a_local_mod_is_never_constrained_twice():
    """Spirit is local on a sceptre and global on an amulet.

    On the sceptre the equipment filter covers it, so it must not also appear
    as a stat filter; on the amulet there is no such property, so it must.
    """
    from sox.valuation.query import build_query, category_for

    sceptre = itemtext.parse(
        "Item Class: Sceptres\nRarity: Rare\nX\nRattling Sceptre\n"
        "--------\nSpirit: 152\n--------\nItem Level: 81\n--------\n"
        "{ Prefix Modifier }\n52(51-55)% increased Spirit\n"
    )
    def all_stats(query):
        # An ambiguous mod lands in its own OR group, not the "and" group.
        return [f for group in query["query"]["stats"] for f in group["filters"]]

    query = build_query(sceptre, category_for(sceptre), MODS, NOTABLES)
    assert query["query"]["filters"]["equipment_filters"]["filters"]["spirit"]
    assert all_stats(query) == [], "already covered by the equipment filter"

    amulet = itemtext.parse(
        "Item Class: Amulets\nRarity: Rare\nX\nLapis Amulet\n"
        "--------\nItem Level: 81\n--------\n"
        "{ Prefix Modifier }\n+30 to Spirit\n"
    )
    query = build_query(amulet, category_for(amulet), MODS, NOTABLES)
    assert "equipment_filters" not in query["query"]["filters"]
    assert all_stats(query), "must stay a stat filter here"


def test_pseudo_totals_replace_the_mods_that_feed_them():
    """Fire resistance from two mods is one total, not two filters.

    Measured on helmets: single-mod fire res >= 60 returns 871 listings while
    the pseudo total returns 2,128 — the difference is items carrying the
    stat across several mods, which a per-mod filter cannot see.
    """
    from sox.valuation.query import build_query, category_for, pseudo_totals

    item = itemtext.parse(
        "Item Class: Helmets\nRarity: Rare\nX\nFreebooter Cap\n"
        "--------\nItem Level: 81\n--------\n"
        "{ Suffix Modifier }\n+31(26-35)% to Fire Resistance\n"
        "{ Suffix Modifier }\n+18(15-20)% to all Elemental Resistances\n"
    )
    totals = dict((pid, value) for pid, value, _ in pseudo_totals(item, MODS))
    # Floor rolls: 26 fire, plus 15 all-elemental counting three times.
    assert totals["pseudo.pseudo_total_elemental_resistance"] == 26 + 15 * 3

    query = build_query(item, category_for(item), MODS, NOTABLES)
    ids = [f["id"] for f in query["query"]["stats"][0]["filters"]]
    assert any(i.startswith("pseudo.") for i in ids)
    assert not any("stat_3372524247" in i for i in ids), "fire mod is in the total"


def test_notables_outrank_pseudo_totals_and_mods():
    """A notable identifies the item; it must survive the whole ladder."""
    from sox.valuation.query import RELAX_STEPS, build_query, category_for

    from pathlib import Path
    fixtures = Path(__file__).parent / "fixtures" / "items"
    item = itemtext.parse((fixtures / "MegalomaniacJewel.txt").read_text())
    for rung in range(len(RELAX_STEPS)):
        query = build_query(item, category_for(item), MODS, NOTABLES, relax=rung)
        ids = [f["id"] for f in query["query"]["stats"][0]["filters"]]
        assert ids, f"rung {rung} dropped every filter"
        assert all("stat_2954116742" in i for i in ids), "notables must survive"


def test_every_source_the_item_owns_feeds_the_pseudo_total():
    """A buyer filtering on total life does not care where it came from.

    Explicit, desecrated, fractured and the base's implicit all contribute.
    A socketed rune does not: its bonus leaves with the rune.
    """
    from sox.valuation.query import pseudo_totals

    item = itemtext.parse(
        "Item Class: Body Armours\nRarity: Rare\nX\nVaal Cuirass\n"
        "--------\nItem Level: 82\n--------\n"
        "{ Implicit Modifier }\n+20 to maximum Life\n"
        "--------\n"
        "{ Prefix Modifier }\n+96 to maximum Life\n"
        "{ Desecrated Prefix Modifier }\n+40 to maximum Life\n"
        "--------\n"
        "+15 to maximum Life (rune)\n"
    )
    totals = dict((pid, value) for pid, value, _ in pseudo_totals(item, MODS))
    assert totals["pseudo.pseudo_total_life"] == 20 + 96 + 40, "the rune's 15 is excluded"


def test_desecrated_mods_reach_the_query():
    """A revealed desecrated roll is often the strongest mod on the item.

    It was counted by the score and then never searched on, which valued a
    1-divine quarterstaff at 3 exalted.
    """
    from sox.valuation.query import searchable_mods, searched_item_texts

    item = itemtext.parse(
        "Item Class: Quarterstaves\nRarity: Rare\nX\nBolting Quarterstaff\n"
        "--------\nItem Level: 81\n--------\n"
        "Adds 121(115-125) to 183(175-190) Cold Damage\n"
        "--------\n98(78-118)% increased Cold Damage (desecrated)\n"
    )
    assert "98% increased Cold Damage" in searchable_mods(item)
    assert "98% increased Cold Damage" in searched_item_texts(item, MODS, NOTABLES)


def test_a_weak_roll_is_dropped_before_a_strong_one():
    """Roll quality decides what survives widening.

    "Adds 6 to 102 Lightning" sits at the 7th percentile of its range and
    says little about the item; the search keeps the strong rolls instead.
    """
    from sox.valuation.query import explain_selection

    item = itemtext.parse(
        "Item Class: Quarterstaves\nRarity: Rare\nX\nBolting Quarterstaff\n"
        "--------\nItem Level: 81\n--------\n"
        "{ Prefix Modifier (Tier: 1) }\nAdds 121(115-125) to 183(175-190) Cold Damage\n"
        "{ Prefix Modifier (Tier: 1) }\nAdds 6(4-40) to 102(95-190) Lightning Damage\n"
        "--------\n98(78-118)% increased Cold Damage (desecrated)\n"
    )
    _, kept = explain_selection(item, MODS, NOTABLES, relax=2)   # rung keeps 2
    assert "Adds # to # Cold Damage" in kept
    assert "#% increased Cold Damage" in kept
    assert "Adds # to # Lightning Damage" not in kept, "the 7th-percentile roll goes first"


def test_a_group_of_one_is_not_a_cluster():
    """With every archetype at a count of one, the winner is arbitrary.

    A helmet whose mods each served a different archetype was ordered by
    whichever was seen first, putting an incidental mana roll ahead of the
    item's only build-defining mod.
    """
    from sox.valuation.mods import dominant_archetype, matched
    from sox.valuation.query import explain_selection, searchable_mods

    # An amulet: no defence property, so nothing seeds the count and every
    # archetype is a group of one.
    item = itemtext.parse(
        "Item Class: Amulets\nRarity: Rare\nX\nLapis Amulet\n"
        "--------\nItem Level: 81\n--------\n"
        "{ Suffix Modifier }\n+23(20-28) to maximum Mana\n"
        "{ Suffix Modifier }\n+24(20-26) to Intelligence\n"
        "{ Suffix Modifier }\n+29(25-32) to Accuracy Rating\n"
    )
    group, _ = dominant_archetype(matched(searchable_mods(item), MODS))
    assert group is None, "one mod per archetype is not a cluster"

    named, stats = explain_selection(item, MODS, NOTABLES)
    assert named is None or named == ""
    assert stats[0] == "# to maximum Mana", "highest weight leads"


def test_weight_leads_the_ranking():
    """A build-defining mod belongs in the query whatever it rolled."""
    from sox.valuation.query import explain_selection

    item = itemtext.parse(
        "Item Class: Helmets\nRarity: Rare\nX\nWarded Helm\n"
        "--------\nArmour: 91\n--------\nItem Level: 81\n--------\n"
        "{ Suffix Modifier }\n+28(20-28) to maximum Mana\n"   # near-max roll, w2
        "--------\n+21(20-30)% of Armour also applies to Elemental Damage (desecrated)\n"
    )  # w3, near-floor roll
    _, stats = explain_selection(item, MODS, NOTABLES, relax=3)   # keeps 1
    assert stats == ["#% of Armour also applies to Elemental Damage"]


def test_a_socketed_rune_does_not_inflate_the_equipment_filter():
    """The displayed total includes the rune; the search must not.

    A chest showing 500 ES where 100 of it comes from a socketed rune is a
    400-ES item, and pricing it as a 500-ES item asks for something the
    seller is not selling.
    """
    from sox.valuation.query import DEFENCE_PROPERTIES, equipment_minimum

    _, flat, percent = DEFENCE_PROPERTIES["Energy Shield"]
    with_rune = itemtext.parse(
        "Item Class: Body Armours\nRarity: Rare\nX\nVaal Cuirass\n"
        "--------\nEnergy Shield: 500\n--------\nItem Level: 82\n--------\n"
        "{ Prefix Modifier }\n+400(380-420) to maximum Energy Shield\n"
        "--------\n+100 to maximum Energy Shield (rune)\n"
    )
    assert with_rune["runeMods"] == ["+100 to maximum Energy Shield"]
    # 500 shown - 400 own - 100 rune = 0 base, rebuilt at the mod's floor roll,
    # then filed at 20% quality like every value the es filter compares against.
    assert equipment_minimum(with_rune, "Energy Shield", flat, percent) == round(380 * 1.2)

    # Without the rune the same item shows 400 and asks for the same floor,
    # which is the point: the rune changes the display, not the item.
    without = itemtext.parse(
        "Item Class: Body Armours\nRarity: Rare\nX\nVaal Cuirass\n"
        "--------\nEnergy Shield: 400\n--------\nItem Level: 82\n--------\n"
        "{ Prefix Modifier }\n+400(380-420) to maximum Energy Shield\n"
    )
    assert equipment_minimum(without, "Energy Shield", flat, percent) == round(380 * 1.2)


def test_rune_mods_are_neither_scored_nor_searched():
    from sox.valuation.candidates import item_mods
    from sox.valuation.query import searchable_mods

    item = itemtext.parse(
        "Item Class: Body Armours\nRarity: Rare\nX\nVaal Cuirass\n"
        "--------\nItem Level: 82\n--------\n"
        "{ Prefix Modifier }\n+96 to maximum Life\n"
        "--------\n36% increased Physical Damage (rune)\n"
    )
    assert "36% increased Physical Damage" not in item_mods(item)
    assert "36% increased Physical Damage" not in searchable_mods(item)


def test_the_items_own_defence_type_counts_toward_coherence():
    """An ES chest is an ES item before any mod is read.

    A recharge-rate mod on an Energy Shield base serves the same buyer the
    base does; the same mod on a ring serves nobody in particular.
    """
    from sox.valuation.candidates import coherence_of
    from sox.valuation.query import defence_seed

    chest = itemtext.parse(
        "Item Class: Body Armours\nRarity: Rare\nX\nVaal Regalia\n"
        "--------\nEnergy Shield: 500\n--------\nItem Level: 82\n--------\n"
        "{ Suffix Modifier }\n25(20-30)% increased Energy Shield Recharge Rate\n"
    )
    assert defence_seed(chest) == {"es": 1}
    group, count, bonus = coherence_of(chest, MODS)
    assert group == "es", "the base names the archetype, not the umbrella tag"
    assert count >= 2 and bonus >= 1, "the base and the mod cluster together"

    # The same mod on a ring has nothing to cluster with.
    ring = itemtext.parse(
        "Item Class: Rings\nRarity: Rare\nX\nGold Ring\n"
        "--------\nItem Level: 82\n--------\n"
        "{ Suffix Modifier }\n25(20-30)% increased Energy Shield Recharge Rate\n"
    )
    assert defence_seed(ring) == {}
    _, ring_count, ring_bonus = coherence_of(ring, MODS)
    assert ring_bonus == 0


def _wand():
    return itemtext.parse(
        Path("tests/fixtures/items/WandRareItem.txt").read_text()
    )


def test_a_granted_skill_is_always_searched_at_its_own_level():
    """A Level 20 Chaos Bolt wand is not a bare wand.

    The granted skill and its level are the whole identity of a wand or
    sceptre, so the filter is exempt from the widening ladder: dropping it
    would not widen the search, it would search for a different item.
    """
    from sox.valuation.query import RELAX_STEPS, build_query, granted_skill_filter

    def all_stats(query):
        return [f["id"] for g in query["query"]["stats"] for f in g["filters"]]

    wand = _wand()
    assert granted_skill_filter(wand) == {
        "id": "skill.chaosbolt", "value": {"min": 20}
    }
    for rung in range(len(RELAX_STEPS)):
        query = build_query(wand, category_for(wand), MODS, NOTABLES, relax=rung)
        assert all_stats(query)[0] == "skill.chaosbolt", (
            f"rung {rung} dropped the granted skill"
        )


def test_an_unlevelled_granted_skill_is_not_searched():
    """Every shield grants Raise Shield. Filtering on it constrains nothing,
    and there is no level to use as a minimum."""
    from sox.valuation.query import granted_skill_filter, granted_skill_text

    shield = itemtext.parse(
        Path("tests/fixtures/items/NormalShield.txt").read_text()
    )
    assert granted_skill_filter(shield) is None
    assert granted_skill_text(shield) == []


def test_the_granted_skill_shows_as_searched_not_ignored():
    from sox.valuation.candidates import score_rows
    from sox.valuation.query import granted_skill_text

    wand = _wand()
    assert granted_skill_text(wand) == ["Grants Skill: Level 20 Chaos Bolt"]
    rows = score_rows(wand, MODS, {})
    assert rows[0] == ("Grants Skill: Level 20 Chaos Bolt", None, "filter")


def test_the_reported_count_is_what_matched_not_what_we_fetched(tmp_path):
    """"10 listings" appeared on every item with a real market.

    One fetch call is enough to find the cheap end, so we only ever price the
    cheapest 10 — but reporting that count as the market size made a thin
    result and a thousand-listing result look identical.
    """
    from sox.report import PricedItem, render

    class WideMarket:
        searches = 0

        def search(self, query):
            return "q1", [f"h{i}" for i in range(10)], 842

        def fetch(self, query_id, hashes):
            return [Listing(amount=p, currency="exalted", account="a")
                    for p in range(1, 11)]

    result = price_by_search(
        ITEM, category_for(ITEM), MODS, NOTABLES, WideMarket(),
        Cache(tmp_path / "c.sqlite"), RATES,
    )
    assert result.listings == 10, "we priced the cheapest 10"
    assert result.matches == 842, "but 842 items matched"

    text = render(ITEM, PricedItem(
        name="X", item_class="Helmets", price_ex=1.0, source="trade",
        tag="exact", listings=result.listings, matches=result.matches,
    ), RATES)
    assert "cheapest 10 of 842 listings" in text


def _listed(evasion, energy_shield, own_pct, rune_pct):
    """A listing payload in the shape the fetch endpoint returns.

    Game terms arrive wrapped in markup — "[Evasion|Evasion Rating]" renders
    as "Evasion Rating" — and every mod carries its actual roll inline.
    """
    return {
        "properties": [
            {"name": "[Evasion|Evasion Rating]", "values": [[str(evasion), 1]]},
            {"name": "[EnergyShield|Energy Shield]", "values": [[str(energy_shield), 1]]},
        ],
        "explicitMods": [{"description":
                          f"{own_pct}% increased [Evasion] and "
                          "[EnergyShield|Energy Shield]"}],
        "runeMods": ([{"description":
                       f"{rune_pct}% increased [Armour|Armour], [Evasion|Evasion] "
                       "and [EnergyShield|Energy Shield]"}] if rune_pct else []),
    }


def test_a_listing_that_only_meets_our_defences_with_runes_is_not_a_comparable():
    """The buyer sockets their own runes, so a listing propped up by them is a
    worse item wearing our defences.

    Live, all four cheapest matches for a 1294-Evasion Forgotten Warden were
    rune-inflated — 1376 showing, 1260 without — and they set the price.
    """
    from sox.valuation.query import meets_without_runes, rune_free_defence
    from sox.valuation.query import DEFENCE_PROPERTIES

    # Both sides filed at 20% quality, which is the unit the filters compare
    # in: the floor the query sent, and the listing recomputed here.
    required = {"ev": {"min": round(1294 * 1.2)}, "es": {"min": round(395 * 1.2)}}

    inflated = _listed(evasion=1376, energy_shield=421, own_pct=292, rune_pct=36)
    _, flat, pct = DEFENCE_PROPERTIES["Evasion Rating"]
    assert round(rune_free_defence(inflated, "Evasion Rating", flat, pct)) == round(1260 * 1.2)
    assert not meets_without_runes(inflated, required)

    # The same defences with no rune behind them clear the floor honestly —
    # and never reach the arithmetic at all, because an item with no rune
    # cannot be rune-inflated and the search already applied every floor.
    genuine = _listed(evasion=1376, energy_shield=421, own_pct=292, rune_pct=0)
    assert meets_without_runes(genuine, required)

    # A rune that is not load-bearing keeps its listing: 1376 shown, and still
    # over the floor once the rune's 6% comes off.
    carried = _listed(evasion=1376, energy_shield=421, own_pct=292, rune_pct=6)
    assert meets_without_runes(carried, {"ev": {"min": round(1000 * 1.2)}})


def test_rune_inflated_listings_are_replaced_not_just_dropped(tmp_path):
    """Dropping them thins the sample, so the pricer reads deeper rather than
    pricing off whatever the first page happened to leave."""
    class Padded:
        def search(self, query):
            return "q1", [f"h{i}" for i in range(40)], 196

        def fetch(self, query_id, hashes):
            out = []
            for h in hashes:
                n = int(h[1:])
                # The first ten are cheap and rune-propped; the rest are real.
                cheap = n < 10
                out.append(Listing(
                    amount=5.0 if cheap else 20.0, currency="divine", account="a",
                    item=_listed(1376, 421, 292, 36 if cheap else 0)))
            return out

    # A unique: its floor is its actual roll, so a rune-propped listing that
    # merely reaches the same displayed number does not clear it.
    item = itemtext.parse(
        "Item Class: Body Armours\nRarity: Unique\nForgotten Warden\n"
        "Primal Markings\n--------\nEvasion Rating: 1376\nEnergy Shield: 421\n"
        "--------\nItem Level: 84\n--------\n"
        "{ Unique Modifier }\n292(200-300)% increased Evasion and Energy Shield\n"
    )
    result = price_by_search(
        item, category_for(item), MODS, NOTABLES, Padded(),
        Cache(tmp_path / "c.sqlite"), RATES,
    )
    assert result.rune_inflated == 10, "the propped-up listings were skipped"
    assert result.listings == 10, "and replaced from deeper in the results"
    assert result.ceiling_ex == 20.0 * 320.0, "the cheap ones set no price"


def test_an_archetype_tie_is_broken_by_weight_not_by_parse_order():
    """A hybrid weapon is 2-2 constantly, and taking the first was a coin flip.

    A Bandit Mace with two elemental mods and two physical ones counted
    elemental 2, physical 2, and 'elemental' won only because the cold roll
    was parsed first.
    """
    from sox.valuation.mods import dominant_archetype, matched
    from sox.valuation.candidates import item_mods

    item = itemtext.parse(
        "Item Class: One Hand Maces\nRarity: Rare\nPain Ram\nBandit Mace\n"
        "--------\nItem Level: 80\n--------\n"
        "{ Prefix Modifier }\nAdds 58(40-60) to 97(80-110) Cold Damage\n"
        "{ Prefix Modifier }\n127(100-129)% increased Physical Damage\n"
        "{ Prefix Modifier }\nAdds 5(4-8) to 195(150-200) Lightning Damage\n"
        "{ Suffix Modifier }\nLeeches 7.85(5-8)% of Physical Damage as Mana\n"
    )
    entries = matched(item_mods(item), MODS)
    # elemental: two weight-3 mods. physical: one weight-3 and one weight-2.
    assert dominant_archetype(entries) == ("elemental", 2)


def test_an_archetype_tied_on_weight_too_is_no_cluster():
    """The item genuinely serves both, and claiming either is worse than
    admitting there is no cluster."""
    from sox.valuation.allowlists import ModEntry
    from sox.valuation.mods import dominant_archetype

    def entry(tag, weight):
        return ModEntry(ids=[], slug="x", text="x", weight=weight,
                        category="c", tags=(tag,))

    tied = [entry("elemental", 3), entry("elemental", 2),
            entry("physical", 3), entry("physical", 2)]
    assert dominant_archetype(tied) == (None, 2)


def test_the_explanation_only_names_stats_the_query_actually_asks_for():
    """A mod covered by an equipment filter is not searched as a stat.

    Reporting it named a buyer group the query never asked for: once a mace's
    damage mods had all become one DPS filter, it still read "searched as
    elemental" with no elemental stat in the query at all.
    """
    from sox.valuation.query import build_query, category_for, explain_selection

    mace = itemtext.parse(
        "Item Class: One Hand Maces\nRarity: Rare\nPain Ram\nBandit Mace\n"
        "--------\nPhysical Damage: 100-200\nCold Damage: 58-97\n"
        "Attacks per Second: 1.55\n--------\nItem Level: 80\n--------\n"
        "{ Prefix Modifier }\nAdds 58(40-60) to 97(80-110) Cold Damage\n"
        "{ Prefix Modifier }\n127(100-129)% increased Physical Damage\n"
        "{ Suffix Modifier }\nLeeches 7.85(5-8)% of Physical Damage as Mana\n"
        "{ Suffix Modifier }\n+2 to Level of all Attack Skills\n"
    )
    group, stats = explain_selection(mace, MODS, NOTABLES)
    assert "Adds # to # Cold Damage" not in stats, "that is the DPS filter"
    assert group != "elemental", "no elemental stat is searched"
    assert set(stats) == {"# to Level of all Attack Skills",
                          "Leeches #% of Physical Damage as Mana"}

    # And the explanation matches what the query really contains.
    ids = [f["id"] for g in build_query(mace, category_for(mace), MODS, NOTABLES)[
        "query"]["stats"] for f in g["filters"]]
    assert len(ids) == len(stats)


def test_a_defence_floor_is_filed_at_twenty_percent_quality():
    """The filters do not compare the number the item shows.

    Measured live on a Corpsewade Iron Greaves listing: the item's Armour
    property reads 78 and its `extended` block — the field the `ar` filter
    reads — says 94, which is 78 x 1.2 at 0% quality. A search for "at least
    my 94 Armour" was therefore asking for items a fifth weaker than ours.

    It cuts both ways. Above 20% the filter DE-rates: a +27% Forgotten Warden
    showing 1,294 rune-free Evasion is filed at 1,222.
    """
    from sox.valuation.query import DEFENCE_PROPERTIES, equipment_minimum

    _, flat, percent = DEFENCE_PROPERTIES["Armour"]
    head = ("Item Class: Boots\nRarity: Rare\nX\nIron Greaves\n"
            "--------\n{quality}Armour: 94\n--------\nItem Level: 81\n")

    plain = itemtext.parse(head.format(quality=""))
    assert equipment_minimum(plain, "Armour", flat, percent) == round(94 * 1.2)

    keen = itemtext.parse(head.format(quality="Quality: +20% (augmented)\n"))
    assert equipment_minimum(keen, "Armour", flat, percent) == 94, "already filed"

    rich = itemtext.parse(head.format(quality="Quality: +30% (augmented)\n"))
    assert equipment_minimum(rich, "Armour", flat, percent) == round(94 * 1.2 / 1.3)


def test_spirit_and_block_are_left_at_face_value():
    """Only ar, es and ev were measured against a quality item. Normalising
    the rest on the strength of an analogy would be guessing at the filter."""
    from sox.valuation.query import DEFENCE_PROPERTIES, equipment_minimum

    _, flat, percent = DEFENCE_PROPERTIES["Spirit"]
    item = itemtext.parse(
        "Item Class: Sceptres\nRarity: Rare\nX\nRattling Sceptre\n"
        "--------\nQuality: +20% (augmented)\nSpirit: 100\n"
        "--------\nItem Level: 81\n"
    )
    assert equipment_minimum(item, "Spirit", flat, percent) == 100


def test_an_item_with_no_runes_is_never_rune_inflated():
    """29 of the 30 listings dropped as rune-inflated on one pair of boots
    carried no rune at all — they were being recomputed from the shown value
    and compared against a floor filed at 20% quality.

    The search already applied every floor. With no rune there is nothing to
    take off, so there is nothing left to check.
    """
    from sox.valuation.query import meets_without_runes

    bare = _listed(evasion=1, energy_shield=1, own_pct=0, rune_pct=0)
    assert meets_without_runes(bare, {"ev": {"min": 99999}})


def test_physical_dps_is_filed_at_twenty_percent_quality_and_elemental_is_not():
    """Measured on four listed two-hand maces, against their own `extended`
    block — the field the dps filter reads.

    A +17% mace showing 112-160 physical at 1.10 aps is filed at 153.45 pdps,
    which is the average rebuilt at 20% quality. A +16% mace showing 1-7
    lightning is filed at 4.4 edps, exactly as shown: quality raises physical
    damage and nothing else.
    """
    import pytest

    from sox.valuation.query import rune_free_dps

    head = ("Item Class: Two Hand Maces\nRarity: Rare\nX\nY\n--------\n"
            "Quality: +{q}% (augmented)\n{damage}Attacks per Second: 1.10\n"
            "--------\nItem Level: 81\n")

    physical = itemtext.parse(head.format(q=17, damage="Physical Damage: 112-160\n"))
    assert rune_free_dps(physical) == pytest.approx(153.45, abs=0.05)

    mixed = itemtext.parse(head.format(
        q=16, damage="Physical Damage: 128-174\nLightning Damage: 1-7\n"))
    assert rune_free_dps(mixed) == pytest.approx(176.0, abs=0.3)


def test_a_minion_mod_does_not_tie_with_its_own_subtypes():
    """A universal minion mod votes for "minion" AND every subtype at once.

    Three of them therefore counted minion 3, minion:attack 3, minion:caster
    3 and minion:companion 3 — four names for the same three mods, all tied on
    count and weight. The tie rule read that as two buyers and reported "none
    — the mods serve different builds" about a ring carrying minion damage,
    minion crit and minion attack speed. Only a DIFFERENT family can make an
    item ambiguous.
    """
    from sox.valuation.candidates import coherence_of, item_mods, score_gear
    from sox.valuation.mods import dominant_archetype, matched

    ring = itemtext.parse((FIXTURES / "MinionRing.txt").read_text())
    assert dominant_archetype(matched(item_mods(ring), MODS)) == ("minion", 3)
    # Reported as the family, not as one of its subtypes.
    assert coherence_of(ring, MODS) == ("minion", 3, 2)
    assert score_gear(ring, MODS, BASES)[0] == 12, "10 on mods, +2 for the cluster"


def test_the_cluster_decides_which_mods_survive_widening():
    """With no cluster the query is ranked by weight alone, which is how a
    search comes to describe a buyer who does not exist: this ring's chaos
    resistance outranked its minion crit and its minion attack speed."""
    from sox.valuation.query import explain_selection

    ring = itemtext.parse((FIXTURES / "MinionRing.txt").read_text())
    group, stats = explain_selection(ring, MODS, NOTABLES, relax=2)
    assert group == "minion"
    assert all("Minion" in text for text in stats), stats


def test_a_genuine_hybrid_is_still_no_cluster():
    """Narrowing the tie rule to one family must not disarm it: two
    elementals against two physicals is still an item serving both."""
    from sox.valuation.allowlists import ModEntry
    from sox.valuation.mods import dominant_archetype

    def entry(tag, weight):
        return ModEntry(ids=[], slug="x", text="x", weight=weight,
                        category="c", tags=(tag,))

    tied = [entry("elemental", 3), entry("elemental", 2),
            entry("physical", 3), entry("physical", 2)]
    assert dominant_archetype(tied) == (None, 2)
