"""Instilling a waystone: the one Distilled Emotion that lifts its loot score
most, and where it lands.

Delirium's currency, ten emotions; up to three go onto a waystone at the
Instill interface — a corrupted stone takes none — and four of them move
the totals the loot score counts. Third-party wikis for 0.5.4; the game's
own tooltip is the authority:

    Ire       7% delirious   +20% magic monsters
    Guilt     9%             +8% pack size
    Greed    10%             +8% item rarity
    Paranoia 12%             +15% rare monsters

The score is linear, so the best emotion is the best emotion every time;
the report names it once with the score and band one of it buys.
"""

from __future__ import annotations

from dataclasses import dataclass

from sox.valuation.query import LOOT_WEIGHTS, category_for, loot_score, loot_verdict


@dataclass(frozen=True)
class Emotion:
    delirious: int   # "Players in Area are N% Delirious"
    line: str        # the waystone line it adds, as the wikis word it
    gain: float      # loot-score points, from LOOT_WEIGHTS


# Magic monsters are pack-size class for the score: more bodies, mostly not
# rares. Paranoia's rare monsters are monster rarity, the loot stat.
EMOTIONS = {
    "Paranoia": Emotion(12, "+15% rare monsters", round(15 * LOOT_WEIGHTS["Monster Rarity"], 1)),
    "Ire": Emotion(7, "+20% magic monsters", round(20 * LOOT_WEIGHTS["Pack Size"], 1)),
    "Greed": Emotion(10, "+8% item rarity", round(8 * LOOT_WEIGHTS["Item Rarity"], 1)),
    "Guilt": Emotion(9, "+8% pack size", round(8 * LOOT_WEIGHTS["Pack Size"], 1)),
}


@dataclass(frozen=True)
class Instillation:
    emotion: str | None
    delirious: int
    blocked: str | None      # corrupted | instilled
    score: int | None        # the loot score with one of the emotion on
    verdict: str | None


def _mod_texts(item: dict) -> list[str]:
    return (list(item.get("explicitMods") or []) + list(item.get("implicitMods") or [])
            + list(item.get("enchantMods") or []))


def instillation(item: dict) -> Instillation | None:
    """The emotion to instill, and the score and band one of it buys.

    None for anything but a waystone. A corrupted stone takes no emotion;
    an instilled one ("Players in Area are N% Delirious") has had its turn.
    """
    if category_for(item) != "map.waystone":
        return None
    base = loot_score(item)
    if base is None:
        return None
    if item.get("corrupted"):
        return Instillation(None, 0, "corrupted", None, None)
    if any("delirious" in text.lower() for text in _mod_texts(item)):
        return Instillation(None, 0, "instilled", None, None)

    name, emotion = max(EMOTIONS.items(), key=lambda pair: pair[1].gain)
    score = int(base[0] + emotion.gain + 0.5)   # half up, as loot_score rounds
    return Instillation(name, emotion.delirious, None, score, loot_verdict(score))
