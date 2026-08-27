"""The instillation path: what three Distilled Emotions do to a waystone's
loot score, one at a time, so the band flip is visible and the third one
can be skipped when it buys nothing.

Waystone effects per emotion (third-party wikis, 0.5.4; the game's own
tooltip is the authority):
    Ire       7% delirious   +20% magic monsters
    Guilt     9%             +8% pack size
    Greed    10%             +8% item rarity
    Paranoia 12%             +15% rare monsters
The other six move nothing the score counts. Up to three per stone, the
same one three times allowed, and a corrupted stone cannot be instilled.
"""

from sox import itemtext
from sox.valuation.instill import EMOTIONS, Instillation, instillation_path

BARE_T15 = """Item Class: Waystones
Rarity: Rare
Ghost Expedition
Waystone (Tier 15)
--------
Revives Available: 0 (augmented)
Item Rarity: +24% (augmented)
Pack Size: +16% (augmented)
Monster Rarity: +37% (augmented)
Waystone Drop Chance: +75% (augmented)
--------
Item Level: 80
--------
Monsters take 27% reduced Extra Damage from Critical Hits
+30% Monster Elemental Resistances
--------
Can be used in a Map Device, allowing you to enter a Map. Waystones can only be used once.
"""


def test_the_path_is_the_best_emotion_three_times_with_the_band_at_each_step():
    path = instillation_path(itemtext.parse(BARE_T15))
    assert isinstance(path, Instillation)
    assert path.blocked is None
    assert path.emotion == "Paranoia"
    # 55.4 + 15 per Paranoia
    assert path.steps == ((1, 70.4, "juice it"), (2, 85.4, "chase"), (3, 100.4, "chase"))
    assert path.delirious == 36


def test_a_corrupted_stone_cannot_be_instilled():
    path = instillation_path(itemtext.parse(BARE_T15 + "--------\nCorrupted\n"))
    assert path.blocked == "corrupted"
    assert path.steps == ()


def test_an_instilled_stone_has_no_path_left():
    text = BARE_T15.replace("+30% Monster Elemental Resistances",
                            "+30% Monster Elemental Resistances\n"
                            "Players in Area are 12% Delirious")
    assert instillation_path(itemtext.parse(text)).blocked == "instilled"


def test_gear_has_no_path():
    assert instillation_path({"itemClass": "Rings", "rarity": "Rare"}) is None


def test_every_emotion_is_scored_by_the_loot_weights():
    """Paranoia feeds monster rarity at weight 1; Greed feeds item rarity at
    a half; Guilt and Ire are pack-size class — more bodies, mostly not
    rares — at 0.4."""
    gains = {name: e.gain for name, e in EMOTIONS.items()}
    assert gains == {"Paranoia": 15.0, "Ire": 8.0, "Greed": 4.0, "Guilt": 3.2}
