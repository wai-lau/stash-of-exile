"""Every mod GGG's table knows is searchable, allowlisted or not.

Glyph Beads, live: "37% increased Evasion Rating" and "24% increased Global
Armour, Evasion and Energy Shield" on a rare amulet printed as
(unsearchable) — neither is in the allowlist, so neither had a stat id —
and the first rung searched an item two mods short of the one in hand.
The allowlist decides WEIGHT; the trade stats table decides what can be
searched at all.
"""

import gzip
import json
from pathlib import Path

from sox import itemtext
from sox.valuation.allowlists import load_mods, load_notables
from sox.valuation.candidates import item_mods
from sox.valuation.mods import build_index, match_mod, score_mods, unlisted_mods
from sox.valuation.query import build_query, category_for

STATS = json.load(gzip.open(
    Path(__file__).parent / "fixtures" / "trade2_stats.json.gz", "rt"))
LISTED = load_mods()
UNLISTED = unlisted_mods(STATS, LISTED)
INDEX = build_index(LISTED + UNLISTED)
NOTABLES = load_notables()

EVASION = "explicit.stat_2106365538"          # global, the amulet's
EVASION_LOCAL = "explicit.stat_124859000"     # "(Local)" twin, armour's
HYBRID = "explicit.stat_1177404658"           # Global Armour, Evasion and ES

GLYPH_BEADS = """Item Class: Amulets
Rarity: Rare
Glyph Beads
Lunar Amulet
--------
Requirements:
Level: 60
--------
Item Level: 81
--------
+24(20-30) to maximum Energy Shield (implicit)
--------
{ Prefix Modifier "Agile" (Tier: 4) }
37(33-38)% increased Evasion Rating
{ Prefix Modifier "Sturdy" (Tier: 3) }
24(20-24)% increased Global Armour, Evasion and Energy Shield
{ Suffix Modifier "of the Salamander" (Tier: 4) }
+38(36-40)% to Fire Resistance
{ Suffix Modifier "of the Walrus" (Tier: 4) }
+37(36-40)% to Cold Resistance
{ Suffix Modifier "of the Storm" (Tier: 5) }
+33(31-35)% to Lightning Resistance
{ Suffix Modifier "of Plunder" (Tier: 3) }
16(15-18)% increased Rarity of Items found
"""


def filters_at(relax):
    item = itemtext.parse(GLYPH_BEADS)
    query = build_query(item, category_for(item), INDEX, NOTABLES, relax=relax)
    out = []
    for f in query["query"]["stats"][0]["filters"]:
        out.append((f.get("id"), (f.get("value") or {}).get("min")))
    for group in query["query"]["stats"][1:]:
        out.append((tuple(f["id"] for f in group["filters"]),
                    (group["filters"][0].get("value") or {}).get("min")))
    return out


def test_an_unlisted_mod_resolves_from_the_stats_table():
    entry = match_mod("37% increased Evasion Rating", INDEX)
    assert entry is not None and entry.weight == 0
    assert entry.ids == [EVASION]
    assert entry.local_ids == (EVASION_LOCAL,), "the (Local) twin rides along"
    assert match_mod("24% increased Global Armour, Evasion and Energy Shield",
                     INDEX).ids == [HYBRID]


def test_the_allowlist_keeps_its_say_over_weight():
    """An allowlisted wording is never shadowed by the table's copy of it."""
    assert match_mod("+45 to maximum Life", INDEX).weight == 3
    assert all(e.text != "# to maximum Life" for e in UNLISTED)


def test_a_wording_with_several_ids_keeps_them_all():
    """As the allowlist does for "# to Spirit": an OR group, never a guess."""
    texts = {}
    for entry in next(g for g in STATS["result"] if g["id"] == "explicit")["entries"]:
        texts.setdefault(entry["text"], []).append(entry["id"])
    twins = {t: ids for t, ids in texts.items()
             if len(ids) > 1 and not t.endswith(" (Local)")}
    unlisted = {e.text: e for e in UNLISTED}
    checked = [t for t in twins if t in unlisted]
    assert checked, "the fixture has no unlisted wording with two ids"
    for text in checked:
        assert sorted(unlisted[text].ids) == sorted(twins[text])


def test_the_first_rung_carries_every_mod_at_its_floor():
    ids = dict(filters_at(0))
    assert ids[EVASION] == 33, "the amulet's global id, at the range floor"
    assert ids[HYBRID] == 20


def test_unlisted_mods_are_tagged_and_cohere_like_any_other():
    """The hybrid names armour, evasion and energy shield: it belongs with
    the evasion roll and the ES implicit, not in the unrelated bin. Live it
    was widened away while the evasion roll it coheres with was kept —
    weight 0 and no tags left roll percentile to decide between them."""
    entry = match_mod("24% increased Global Armour, Evasion and Energy Shield", INDEX)
    assert set(entry.tags) >= {"armour", "evasion", "es", "defence"}
    assert set(match_mod("37% increased Evasion Rating", INDEX).tags) >= {"evasion", "defence"}
    stats = {stat for stat, _ in filters_at(2)}
    assert HYBRID in stats, "coheres with evasion and ES, so it survives widening"


def test_unlisted_mods_do_not_score():
    mods = item_mods(itemtext.parse(GLYPH_BEADS))
    assert score_mods(mods, INDEX) == score_mods(mods, build_index(LISTED))
