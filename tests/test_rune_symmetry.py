"""Runes come off BOTH sides, for DPS as well as for defences.

Defences were already symmetric: our floor is built from our own mods only,
and a listing that clears it on a socketed rune is discarded client-side. DPS
was symmetric the other way — rune-inclusive on both sides — because the API
computes a listing's dps from its displayed damage and nothing stripped it.
Each was internally consistent; together they priced two different items.

The buyer supplies their own runes and the rune carries its own price, so the
rune leaves in both cases.
"""

from pathlib import Path

import pytest

from sox import itemtext
from sox.valuation.query import damage_filters, meets_without_runes

CROSSBOW = itemtext.parse(
    (Path(__file__).parent / "fixtures" / "items" / "HighDamageRareItem.txt").read_text()
)


def listing(phys_avg, aps, rune_mods=(), own_pct=0.0):
    """A trade listing shaped the way the fetch endpoint returns one."""
    mods = []
    if own_pct:
        mods.append(f"{own_pct:g}% increased Physical Damage")
    return {
        "properties": [
            {"name": "Physical Damage", "values": [[f"{phys_avg}-{phys_avg}", 0]]},
            {"name": "Attacks per Second", "values": [[str(aps), 0]]},
        ],
        "explicitMods": mods,
        "runeMods": list(rune_mods),
    }


def test_our_dps_floor_leaves_the_rune_out():
    """414-1043 at 2.07aps is 1508 dps showing; the 36% rune is 141 of it.

    Own physical mods total 251%, the rune adds 36 on top, so the base is
    recovered against 287% and rebuilt against 251%.
    """
    assert damage_filters(CROSSBOW)["dps"]["min"] == pytest.approx(1367.7, abs=0.2)


def test_a_listing_propped_up_by_its_rune_is_not_a_comparable():
    """Shows 1000 dps, is 800 once the buyer's own rune comes off."""
    propped = listing(500, 2.0, rune_mods=["Adds 100 to 100 Physical Damage"])
    assert not meets_without_runes(propped, {"dps": {"min": 900}})


def test_a_listing_that_clears_the_floor_on_its_own_survives():
    clean = listing(475, 2.0)
    assert meets_without_runes(clean, {"dps": {"min": 900}})


def test_a_rune_free_listing_is_unchanged_by_the_pass():
    """No runeMods at all must not perturb the computed dps."""
    clean = listing(500, 2.0)
    assert meets_without_runes(clean, {"dps": {"min": 1000}})


def test_percent_runes_are_removed_multiplicatively_not_by_subtraction():
    """Own 100%, rune 50%, 750 showing.

    Percent mods are multiplicative on a shared base, so the base is
    recovered against 150% and rebuilt against 100%: 750/2.5 = 300, times 2
    is 600. Dividing the total by the rune's own 1.5 would say 500, and
    subtracting half of it would say 375. Both are the wrong item.
    """
    propped = listing(750, 1.0, rune_mods=["50% increased Physical Damage"],
                      own_pct=100)
    assert not meets_without_runes(propped, {"dps": {"min": 700}})
    assert meets_without_runes(propped, {"dps": {"min": 600}})
