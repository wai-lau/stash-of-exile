"""Rendering must survive every price outcome.

A leftover two-value unpack in the unpriced branch crashed on any item that
came back without a price, while every priced item rendered fine.
"""

import pytest

from sox.report import PricedItem, render
from sox.valuation.classify import ItemClass

# Live rates. Chaos is present in the table but never quoted into.
RATES = {"exalted": 1.0, "chaos": 33.4, "divine": 340.6}

ITEM = {"name": "Doom Shield", "baseType": "Tower Shield", "ilvl": 70}
ROWS = (
    ("+96 to maximum Life", 3, "defence"),
    ("+145 to Evasion Rating", None, "filter"),
    ("+8% to Fire Resistance", 0, ""),
)


@pytest.mark.parametrize("tag,price", [
    ("junk", None),
    ("unpriced:no-index", None),
    ("unpriced:unknown-class", None),
    ("unpriced:above-market", None),
    ("exact", 12.5),
    ("relaxed:2", 12.5),
])
def test_render_handles_every_outcome(tag, price):
    priced = PricedItem(
        name="Doom Shield", item_class=ItemClass.GEAR, price_ex=price,
        source="trade" if price else "unpriced", tag=tag, reason="score 4",
        score=4, breakdown=ROWS, listings=3 if price else 0,
        median_ex=price, p25_ex=price, item_class_name="Shields",
        category="armour.shield",
    )
    text = render(ITEM, priced, RATES)
    assert "Doom Shield" in text
    assert "armour.shield" in text


def test_equipment_filter_mods_are_marked_not_dismissed():
    priced = PricedItem(
        name="Doom Shield", item_class=ItemClass.GEAR, price_ex=12.5,
        source="trade", tag="exact", reason="score 4", score=4,
        breakdown=ROWS, listings=9, median_ex=12.5, p25_ex=12.5,
    )
    text = render(ITEM, priced, RATES)
    assert "(filter)" in text, "a mod driving the search must not read as ignored"
    assert "+0  +145 to Evasion Rating" in text


def test_the_market_block_is_the_last_thing_rendered():
    """The score and coherence explain how the number was arrived at; the
    number is what you read off the end."""
    priced = PricedItem(
        name="Doom Shield", item_class=ItemClass.GEAR, price_ex=1600.0,
        source="trade", tag="exact", listings=10, matches=196,
        median_ex=4480.0, p25_ex=2240.0, score=4, breakdown=ROWS,
        searched_group="defence", searched_stats=("# to maximum Life",),
    )
    import re

    body = render(ITEM, priced, RATES).split("\n")
    # Labelled rows open a section; everything else is that section's detail.
    labels = [re.match(r"  (\w+)", l).group(1) for l in body if re.match(r"  \w", l)]
    assert labels[-1] == "market", labels
    assert {"score", "coherence", "searched"} <= set(labels[:-1])


def test_a_waystone_names_the_stats_the_search_rested_on():
    """Every mod on a waystone scores +0, so without this line the report
    shows a market row resting on nothing at all."""
    priced = PricedItem(
        name="Terror Panorama", item_class=ItemClass.ENDGAME, price_ex=12.5,
        source="trade", tag="exact", listings=9, median_ex=12.5, p25_ex=12.5,
        map_stats=("tier 14+", "drop chance 75%+"),
    )
    text = render(ITEM, priced, RATES)
    assert "searched   tier 14+  ·  drop chance 75%+" in text


def test_a_bulk_priced_commodity_says_why_the_search_was_dropped():
    """A waystone rerouted to the bulk book must say what happened to its
    search, or the exchange row reads as if the item were never searchable."""
    priced = PricedItem(
        name="Rotting Navigation", item_class=ItemClass.ENDGAME, price_ex=5.0,
        source="exchange", tag="capped-search", offers=39, stock=5150,
        ask_ex=5.0, item_class_name="Waystones",
    )
    text = render(ITEM, priced, RATES)
    assert "capped" in text and "bulk" in text


def test_a_fills_price_names_the_game_exchange():
    """A price read off the game's own exchange rests on trades, not
    listings, and the row has to say which measurement it is."""
    priced = PricedItem(
        name="Masterwork Rune", item_class=ItemClass.GEM, price_ex=260.0,
        source="exchange", tag=None, quoted="fills", traded_ex=38_000.0,
    )
    text = render(ITEM, priced, RATES)
    assert "game's own exchange" in text
    assert "38,000" in text


def test_a_capped_search_says_its_low_is_a_sample():
    """Measured live: tier 15+ alone floored at 3 ex while the strictly
    narrower tier 15+, rarity 24+ floored at 1 — impossible in one market.
    Past 10,000 matches the trade engine truncates BEFORE sorting, so the
    cheapest of a capped search is the floor of a sample."""
    priced = PricedItem(
        name="Forgotten Intent", item_class=ItemClass.ENDGAME, price_ex=3.0,
        source="trade", tag="exact", listings=10, matches=10_000,
        median_ex=3.0, p25_ex=3.0,
    )
    assert "sample" in render(ITEM, priced, RATES)


def test_an_uncapped_search_needs_no_sample_note():
    priced = PricedItem(
        name="Forgotten Intent", item_class=ItemClass.ENDGAME, price_ex=3.0,
        source="trade", tag="exact", listings=10, matches=9_999,
        median_ex=3.0, p25_ex=3.0,
    )
    assert "sample" not in render(ITEM, priced, RATES)


def test_a_price_is_quoted_in_one_currency():
    """"1,600 ex (5.0 div)" made you convert in your head to compare against a
    market that quotes divine. In PoE2 chaos sits BETWEEN exalted and divine,
    so it is a real middle rung rather than a fraction."""
    from sox.report import fmt_price

    assert fmt_price(0.5, RATES) == "0.5 ex"
    assert fmt_price(26.5, RATES) == "26.5 ex"
    # Chaos is in the rate table but is never quoted into: nobody prices an
    # item in chaos, so it would be a conversion the reader has to undo.
    assert fmt_price(120.0, RATES) == "120 ex"
    assert fmt_price(1600.0, RATES) == "4.7 div"
    assert fmt_price(150000.0, RATES) == "440 div"
    assert fmt_price(None, RATES) == "—"
    # No divine rate yet: fall through rather than crash.
    assert fmt_price(1600.0, {"exalted": 1.0}) == "1,600 ex"


def test_a_market_row_is_quoted_in_one_unit():
    """"low 9 ex · 25th 32.23 ex · median 3.5 chaos" is ascending, but you
    have to know chaos is 33 ex to see it. The row exists to be compared
    across, so it gets one scale."""
    from sox.report import fmt_row

    assert fmt_row([9.0, 32.23, 117.0], RATES) == ["9 ex", "32.23 ex", "117 ex"]
    assert fmt_row([1600.0, 2240.0, 4480.0], RATES) == [
        "4.7 div", "6.58 div", "13.15 div"]
    # Anchored on the low: the largest would render this row in divine and
    # turn the low into "0.03 div".
    assert fmt_row([9.0, 32.23, 4480.0], RATES)[0] == "9 ex"
    assert fmt_row([None, None, None], RATES) == ["—", "—", "—"]


def test_a_divine_market_row_wears_the_colour_inside_out():
    """The unit a price is quoted in is information; so is spotting it.

    A watch session scrolls, and the divine prices are the ones worth
    catching. Inverting the row it already uses beats introducing a second
    colour that then has to be learned.
    """
    from sox import report

    rates = {"exalted": 1.0, "divine": 358.0}
    div = report.render({"typeLine": "Mace"}, PricedItem(
        name="Mace", item_class=ItemClass.GEAR, price_ex=716.0, source="trade",
        tag="exact", listings=10, median_ex=1074.0), rates)
    ex = report.render({"typeLine": "Mace"}, PricedItem(
        name="Mace", item_class=ItemClass.GEAR, price_ex=12.0, source="trade",
        tag="exact", listings=10, median_ex=20.0), rates)
    assert report.MARKET_DIV in div and "2 div" in div
    assert report.MARKET_DIV not in ex and report.MARKET in ex


def test_only_the_amounts_are_lit():
    """"low", "25th" and "median" are labels. A row lit end to end makes them
    compete with the numbers they name."""
    from sox import report

    row = next(line for line in report.render({"typeLine": "Mace"}, PricedItem(
        name="Mace", item_class=ItemClass.GEAR, price_ex=12.0, source="trade",
        tag="exact", listings=10, p25_ex=18.0, median_ex=20.0,
    ), {"exalted": 1.0, "divine": 358.0}).splitlines() if "market" in line)

    for label in ("low", "25th", "median", "·"):
        assert f"{report.RESET}{label}" not in row, label
    assert row.count(f"{report.MARKET}12 ex{report.RESET}") == 1
    assert f"{report.MARKET}18 ex{report.RESET}" in row
    assert f"{report.MARKET}20 ex{report.RESET}" in row
    assert "  market     low " in row, "the labels carry no escape of their own"


def _traded(**kwargs):
    fields = dict(
        name="Behemoth Finger", item_class=ItemClass.GEAR, price_ex=1.0,
        source="trade", tag="exact", listings=9, matches=9,
        median_ex=45.0, p25_ex=20.0, confidence="firm",
        item_class_name="Rings", category="accessory.ring",
    )
    return PricedItem(**{**fields, **kwargs})


def test_a_dump_listing_is_marked_in_the_row_it_sits_in():
    """1 ex against a 45 ex median is somebody dumping, not the market.

    The row leads with that 1 ex and the session total counts it, so the row
    itself has to say which of its three numbers to read.
    """
    text = render(ITEM, _traded(skewed=True), RATES)
    market = next(line for line in text.splitlines() if "market" in line)
    assert "(dump)" in market
    assert "45x" in text.replace("×", "x"), "say how far under it sits"


def test_the_dump_note_comes_after_the_market_row():
    """Above the row it separated the reader from the prices; the numbers are
    the answer and the note is how to read them."""
    lines = render(ITEM, _traded(skewed=True), RATES).splitlines()
    row = next(i for i, l in enumerate(lines) if l.startswith("  market "))
    note = next(i for i, l in enumerate(lines) if "read the 25th" in l)
    assert note > row


def test_an_ordinary_sample_is_not_flagged():
    text = render(ITEM, _traded(median_ex=3.0, skewed=False), RATES)
    assert "dump" not in text and "read the 25th" not in text


def test_a_divine_exchange_price_wears_the_unit_not_a_note():
    """A price read off the divine book used to carry a "priced against
    divine" line. The unit already says it — "2.67 div" IS that fact — so the
    row is dressed like the market row instead: lit, and inverted on div."""
    from sox import report

    priced = PricedItem(
        name="Khatal's Rejuvenation", item_class=ItemClass.GEM, price_ex=908.0,
        source="exchange", tag=None, offers=12, stock=340, ask_ex=908.0,
        quoted="divine", item_class_name="gem",
    )
    text = render(ITEM, priced, RATES)
    assert "priced against" not in text
    assert f"{report.MARKET_DIV}2.67 div{report.RESET}" in text


def test_an_exalted_exchange_price_is_lit_like_the_market_row():
    from sox import report

    priced = PricedItem(
        name="Omen", item_class=ItemClass.GEM, price_ex=1.0, source="exchange",
        tag=None, offers=1303, stock=6654, ask_ex=1.0, quoted="exalted",
    )
    text = render(ITEM, priced, RATES)
    assert "against divine" not in text
    assert f"{report.MARKET}1 ex{report.RESET}" in text
    assert report.MARKET_DIV not in text


def test_the_query_renders_under_searched_as():
    priced = PricedItem(
        name="Doom Shield", item_class=ItemClass.GEAR, price_ex=12.5,
        source="trade", tag="relaxed:1", reason="score 4", score=4,
        breakdown=ROWS, listings=9, median_ex=12.5, p25_ex=12.5,
        searched_group="minion", searched_stats=("Minions deal #% increased Damage",),
        query_stats=("Minions deal #% increased Damage ≥ 20",
                     "total chaos resistance ≥ 30"),
    )
    text = render(ITEM, priced, RATES)
    as_row = text.index("searched   as minion")
    assert text.index("Minions deal #% increased Damage ≥ 20") > as_row
    assert "total chaos resistance ≥ 30" in text


def test_the_query_renders_without_an_archetype_too():
    """A twice-corrupted chest has no dominant buyer, so there is no
    "searched as" row — the query must not vanish with it."""
    priced = PricedItem(
        name="Doom Shield", item_class=ItemClass.GEAR, price_ex=12.5,
        source="trade", tag="exact", reason="score 4", score=4,
        breakdown=ROWS, listings=9, median_ex=12.5, p25_ex=12.5,
        query_stats=("Grants Skill: Level 20 Spirit Vessel",
                     "Companions have #% increased maximum Life ≥ 40"),
    )
    text = render(ITEM, priced, RATES)
    assert "searched   " in text
    assert "Grants Skill: Level 20 Spirit Vessel" in text
    assert "Companions have #% increased maximum Life ≥ 40" in text
