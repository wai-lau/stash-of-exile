"""A handful of listings is not a market.

A quarterstaff worth about 3ex once reported 320ex: three listings matched,
and the cheapest of those three was a far better item priced at a divine.
Pricing must keep widening until the sample means something.
"""

from pathlib import Path

from sox import itemtext
from sox.cache import Cache
from sox.ggg.trade import Listing
from sox.valuation.allowlists import load_mods, load_notables
from sox.valuation.mods import build_index
from sox.valuation.query import category_for
from sox.valuation.trade_pricer import MIN_SAMPLE, price_by_search

MODS = build_index(load_mods())
NOTABLES = load_notables()
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
        return f"q{self.searches}", [f"h{i}" for i in range(count)]

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


def test_only_cohering_mods_are_searched():
    """Non-cohering mods are dropped, not used to pad the query."""
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
    assert "#% increased Attack Speed" not in stats


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
    assert query["query"]["filters"]["equipment_filters"]["filters"]["ev"] == {"min": 457}


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
    assert equipment_minimum(item, "Evasion Rating", flat, percent) == 485 - 145 + 117

    # With no roll range reported, the total stands as-is.
    plain = itemtext.parse(
        "Item Class: Helmets\nRarity: Rare\nX\nFreebooter Cap\n"
        "--------\nEvasion Rating: 485\n--------\nItem Level: 81\n"
    )
    assert equipment_minimum(plain, "Evasion Rating", flat, percent) == 485


def test_weapon_damage_is_left_out_of_equipment_filters():
    """Damage, attack speed and sockets are deliberately not constrained."""
    from sox.valuation.query import DEFENCE_PROPERTIES, build_query, category_for

    assert not {"damage", "aps", "crit", "rune_sockets"} & {
        fid for fid, _, _ in DEFENCE_PROPERTIES.values()
    }
    staff = itemtext.parse(
        "Item Class: Quarterstaves\nRarity: Rare\nX\nBolting Quarterstaff\n"
        "--------\nPhysical Damage: 54-219\nAttacks per Second: 1.40\n"
        "--------\nItem Level: 81\n--------\n"
        "{ Prefix Modifier }\nAdds 121 to 183 Cold Damage\n"
    )
    query = build_query(staff, category_for(staff), MODS, NOTABLES)
    assert "equipment_filters" not in query["query"]["filters"]


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


def test_default_status_includes_offline_sellers():
    """PoE2 trade is asynchronous, so most of the market is offline.

    Verified live on one query: status=online returned 1 listing while
    status=any returned 918, cheapest 1 exalted. Pricing off the online
    slice put a 3ex item in the hundreds.
    """
    from sox.config import Config
    from sox.valuation.query import build_query, category_for

    assert Config().status == "any"
    query = build_query(ITEM, category_for(ITEM), MODS, NOTABLES)
    assert query["query"]["status"]["option"] == "any"


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


def test_every_mod_source_feeds_the_pseudo_total():
    """A buyer filtering on total life does not care where the life came from.

    Explicit, desecrated, rune, fractured and implicit all contribute to the
    same total, so all of them must be summed.
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
    assert totals["pseudo.pseudo_total_life"] == 20 + 96 + 40 + 15


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
