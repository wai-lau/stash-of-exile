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

    _, at_rung_2 = RELAX_STEPS[2]
    _, stats = explain_selection(ITEM, MODS, NOTABLES, relax=2)
    assert len(stats) <= at_rung_2
