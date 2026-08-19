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


def test_armour_uses_the_local_stat_id():
    """"+145 to Evasion Rating" on a helmet is a different stat to the API.

    A helmet worth 20ex priced at 0.2ex because the global id was searched
    and matched nothing, driving the ladder down to a single weak filter.
    """
    from sox.valuation.mods import normalize_mod
    from sox.valuation.query import build_query, category_for, stat_ids_for

    entry = MODS[normalize_mod("+145 to Evasion Rating")]
    assert entry.local_ids, "flat evasion must know its local twin"
    assert stat_ids_for(entry, "armour.helmet") == entry.local_ids
    assert stat_ids_for(entry, "accessory.amulet") == tuple(entry.ids)

    helmet = itemtext.parse(
        "Item Class: Helmets\nRarity: Rare\nDragon Visor\nFreebooter Cap\n"
        "--------\nEvasion Rating: 582\n--------\nItem Level: 81\n--------\n"
        "{ Prefix Modifier }\n+145 to Evasion Rating\n"
    )
    query = build_query(helmet, category_for(helmet), MODS, NOTABLES)
    ids = [f["id"] for f in query["query"]["stats"][0]["filters"]]
    assert entry.local_ids[0] in ids
    assert entry.ids[0] not in ids


def test_local_defences_do_not_leak_onto_jewellery():
    from sox.valuation.mods import normalize_mod
    from sox.valuation.query import stat_ids_for

    for text in ("# to Armour", "# to maximum Energy Shield", "#% increased Attack Speed"):
        entry = MODS[normalize_mod(text)]
        assert stat_ids_for(entry, "accessory.ring") == tuple(entry.ids)
