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


def test_a_book_read_against_divine_says_so():
    """A price in exalted read off a divine book is not the same measurement
    as one read off an exalted book, and the row has to say which it is —
    the exalted book for this one held nine units and priced it at 10 ex."""
    priced = PricedItem(
        name="Khatal's Rejuvenation", item_class=ItemClass.GEM, price_ex=908.0,
        source="exchange", tag=None, offers=12, stock=340, ask_ex=908.0,
        quoted="divine", item_class_name="gem",
    )
    text = render(ITEM, priced, RATES)
    assert "divine" in text.split("exchange", 1)[1]


def test_an_exalted_book_needs_no_note():
    priced = PricedItem(
        name="Omen", item_class=ItemClass.GEM, price_ex=1.0, source="exchange",
        tag=None, offers=1303, stock=6654, ask_ex=1.0, quoted="exalted",
    )
    assert "against divine" not in render(ITEM, priced, RATES)
