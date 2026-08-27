"""The instillation: the one Distilled Emotion that lifts a waystone's loot
score most, and the score and band it lands on.

Waystone effects per emotion (third-party wikis, 0.5.4; the game's own
tooltip is the authority):
    Ire       7% delirious   +20% magic monsters
    Guilt     9%             +8% pack size
    Greed    10%             +8% item rarity
    Paranoia 12%             +15% rare monsters
The other six move nothing the score counts. A corrupted stone cannot be
instilled; an instilled one has had its turn.
"""

from sox import itemtext
from sox.valuation.instill import EMOTIONS, Instillation, instillation
from sox.valuation.query import loot_score

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


def test_the_best_emotion_and_where_it_lands():
    path = instillation(itemtext.parse(BARE_T15))
    assert path == Instillation(emotion="Paranoia", delirious=12, blocked=None,
                                score=70, verdict="juice it")


def test_the_band_is_judged_on_the_number_shown():
    """Live: "×2 → 65 (run it)". The score printed as 65 was 64.6 underneath
    and the band was judged on that. Scores are integers, and the band is
    the integer's."""
    stone = BARE_T15.replace("Item Rarity: +24%", "Item Rarity: +25%") \
                    .replace("Pack Size: +16%", "Pack Size: +18%") \
                    .replace("Monster Rarity: +37%", "Monster Rarity: +30%")
    item = itemtext.parse(stone)
    # 30 + 12.5 + 7.2 = 49.7 -> 50
    assert loot_score(item) == (50, "run it")
    assert instillation(item).score == 65
    assert instillation(item).verdict == "juice it"


def test_a_corrupted_stone_cannot_be_instilled():
    path = instillation(itemtext.parse(BARE_T15 + "--------\nCorrupted\n"))
    assert path.blocked == "corrupted" and path.emotion is None


def test_an_instilled_stone_has_had_its_turn():
    text = BARE_T15.replace("+30% Monster Elemental Resistances",
                            "+30% Monster Elemental Resistances\n"
                            "Players in Area are 12% Delirious")
    assert instillation(itemtext.parse(text)).blocked == "instilled"


def test_gear_has_no_instillation():
    assert instillation({"itemClass": "Rings", "rarity": "Rare"}) is None


def test_every_emotion_is_scored_by_the_loot_weights():
    """Paranoia feeds monster rarity at weight 1; Greed feeds item rarity at
    a half; Guilt and Ire are pack-size class — more bodies, mostly not
    rares — at 0.4."""
    gains = {name: e.gain for name, e in EMOTIONS.items()}
    assert gains == {"Paranoia": 15.0, "Ire": 8.0, "Greed": 4.0, "Guilt": 3.2}
