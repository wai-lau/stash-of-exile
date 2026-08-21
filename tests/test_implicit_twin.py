"""A mod the item carries TWICE — once as an implicit, once as an explicit.

An Iron Ring's implicit and its Flaring prefix are both worded "Adds # to #
Physical Damage to Attacks", so both resolve to the same allowlist entry. The
query was built from a map keyed on that entry, and one text overwrote the
other: the Tier-1 prefix — 12 to 31, the reason the ring is worth anything —
was asked for as the implicit's floor of 2.5, twice, and never as itself.

Live that priced a 1-divine ring at 1 exalted: the search described any Iron
Ring with the base implicit and a few resistances, found nine of them, and
took the cheapest.
"""

from pathlib import Path

from sox import itemtext
from sox.valuation.allowlists import load_mods, load_notables
from sox.valuation.mods import build_index
from sox.valuation.query import build_query, category_for

MODS = build_index(load_mods())
NOTABLES = load_notables()
FIXTURES = Path(__file__).parent / "fixtures" / "items"

PHYSICAL = "stat_3032590688"


def _stats(name="RingImplicitTwin.txt", relax=0):
    item = itemtext.parse((FIXTURES / name).read_text())
    query = build_query(item, category_for(item), MODS, NOTABLES, relax=relax)
    out = []
    for group in query["query"]["stats"]:
        out += group.get("filters", [])
    return out


def test_the_explicit_roll_is_searched_as_itself():
    """12 to 31 averages 21.5, and that is what a buyer filters on."""
    wanted = {"id": f"explicit.{PHYSICAL}", "value": {"min": 21.5}}
    assert wanted in _stats()


def test_the_implicit_is_asked_for_once():
    """Twice is once too many: it displaced the explicit it shares wording
    with, and no listing is matched by a filter repeated."""
    ids = [f["id"] for f in _stats()]
    assert ids.count(f"implicit.{PHYSICAL}") == 1


def test_no_filter_is_repeated():
    ids = [f["id"] for f in _stats()]
    assert len(ids) == len(set(ids))


ELEMENTAL = "pseudo.pseudo_total_elemental_resistance"
COLD_RES = "explicit.stat_4220027924"
ALL_RES = "explicit.stat_2901986750"


def test_resistances_are_searched_as_their_total():
    """Two filters ANDed are stricter than the total they add up to.

    +23%(21-25) cold and +16%(15-16) to all elemental total 66 at their floor
    rolls, and a buyer shopping for resistance takes any distribution of it.
    Asking for cold >= 23 AND all-elemental >= 16 instead demands this exact
    pair, a narrower search than the one the pseudo exists to provide.
    """
    ids = {f["id"]: f["value"]["min"] for f in _stats()}
    assert ids.get(ELEMENTAL) == 21 + 15 * 3
    assert COLD_RES not in ids and ALL_RES not in ids


def test_a_total_off_the_archetype_widens_away_first():
    """The ring is an attack ring: its resistances are worth searching, but
    they are not what a buyer of it is shopping for. So the total goes in
    BEHIND the attack mods and is the first thing widening drops — an
    Intelligence total kept ahead of them once moved a 3ex quarterstaff to a
    50ex median.
    """
    ids = [f["id"] for f in _stats(relax=3)]  # cap 2
    assert ELEMENTAL not in ids
    assert f"explicit.{PHYSICAL}" in ids
