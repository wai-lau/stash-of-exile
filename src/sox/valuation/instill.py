"""Instilling a waystone: what three Distilled Emotions do to its loot score.

Delirium's currency, ten emotions; up to three go onto a waystone at the
Instill interface — a corrupted stone takes none — and four of them move
the totals the loot score counts. Third-party wikis for 0.5.4; the game's
own tooltip is the authority:

    Ire       7% delirious   +20% magic monsters
    Guilt     9%             +8% pack size
    Greed    10%             +8% item rarity
    Paranoia 12%             +15% rare monsters

The score is linear, so the best three are the best one three times. What
varies by stone is where the band flips, so the path is printed a step at
a time and the third emotion can be skipped when it buys nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from sox.valuation.query import LOOT_WEIGHTS, category_for, loot_score, loot_verdict


@dataclass(frozen=True)
class Emotion:
    delirious: int   # "Players in Area are N% Delirious", per emotion, additive
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

SLOTS = 3


@dataclass(frozen=True)
class Instillation:
    emotion: str | None
    delirious: int                                   # after all three
    blocked: str | None                              # corrupted | instilled
    steps: tuple[tuple[int, float, str], ...]        # (count, score, verdict)


def _mod_texts(item: dict) -> list[str]:
    return (list(item.get("explicitMods") or []) + list(item.get("implicitMods") or [])
            + list(item.get("enchantMods") or []))


def instillation_path(item: dict) -> Instillation | None:
    """The best emotion, one, two and three times, with the band at each.

    None for anything but a waystone. A corrupted stone takes no emotion;
    an instilled one ("Players in Area are N% Delirious") has had its turn.
    """
    if category_for(item) != "map.waystone":
        return None
    base = loot_score(item)
    if base is None:
        return None
    if item.get("corrupted"):
        return Instillation(None, 0, "corrupted", ())
    if any("delirious" in text.lower() for text in _mod_texts(item)):
        return Instillation(None, 0, "instilled", ())

    name, emotion = max(EMOTIONS.items(), key=lambda pair: pair[1].gain)
    score, _ = base
    steps = tuple(
        (n, round(score + n * emotion.gain, 1), loot_verdict(score + n * emotion.gain))
        for n in range(1, SLOTS + 1)
    )
    return Instillation(name, emotion.delirious * SLOTS, None, steps)
