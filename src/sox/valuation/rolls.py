"""Roll ranges and how good a copy is within them.

This is what makes a unique's index price usable. The index reports one
number, which for a wide-rolling unique is the floor: Ventor's Gamble indexes
at ~7ex across 26,747 listings while a good one sells for many Divine.
"""

from __future__ import annotations

import re

RANGE = re.compile(r"\((\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\)")
# Signed. "-30% to all Elemental Resistances" read as 30 is the opposite
# claim about the item, and a search built on it asks for what no copy has.
NUMBER = re.compile(r"(-?\d+(?:\.\d+)?)")

MOD_KEYS = ("explicit_mods", "implicit_mods")

# Score for a range whose floor is 0, e.g. "+(0-80) to maximum Life". The
# max/min ratio is undefined but the swing is total, so it must rank high.
ZERO_FLOOR_SPREAD = 10.0


def parse_ranges(mod_text: str) -> list[tuple[float, float]]:
    return [(float(lo), float(hi)) for lo, hi in RANGE.findall(mod_text)]


def parse_values(mod_text: str) -> list[float]:
    return [float(n) for n in NUMBER.findall(mod_text)]


def _ranges_from_structured(mod: dict) -> list[tuple[float, float]]:
    out = []
    for sub in mod.get("mods") or []:
        for magnitude in sub.get("magnitudes") or []:
            try:
                lo, hi = float(magnitude["min"]), float(magnitude["max"])
            except (KeyError, TypeError, ValueError):
                continue
            out.append((lo, hi))
    return out


def _all_ranges(metadata: dict) -> list[tuple[float, float]]:
    ranges: list[tuple[float, float]] = []
    for key in MOD_KEYS:
        for mod in metadata.get(key) or []:
            if isinstance(mod, dict):
                ranges.extend(_ranges_from_structured(mod))
            else:
                ranges.extend(parse_ranges(mod))
    return ranges


def spread_of(metadata: dict) -> float:
    """Widest swing across the item's rolled ranges.

    High spread means the index price cannot describe a specific copy. It is
    NOT on its own a reason to spend a search: Thunderfist spreads x111 and
    sells for ~3ex, so even a perfect copy is worth ~3ex.
    """
    ratios = []
    for lo, hi in _all_ranges(metadata):
        if hi <= lo:
            continue
        ratios.append(hi / lo if lo > 0 else ZERO_FLOOR_SPREAD)
    return max(ratios) if ratios else 1.0


# A range like "(70-100)" and a rolled "76" must reduce to the same shape so
# a mod can be matched to its template by text.
_RANGE_TOKEN = re.compile(r"\(\s*-?\d+(?:\.\d+)?\s*-\s*-?\d+(?:\.\d+)?\s*\)")
_NUMBER_TOKEN = re.compile(r"-?\d+(?:\.\d+)?")


def _shape(text: str) -> str:
    """Mod text with every number and range collapsed, for template matching."""
    text = _RANGE_TOKEN.sub("#", text)
    text = _NUMBER_TOKEN.sub("#", text)
    return re.sub(r"\s+", " ", text.casefold()).strip()


def roll_percentiles(item_mods: list[str], metadata: dict) -> list[float]:
    """Our percentile within each range the item can roll, one per mod.

    Matched by mod TEXT, not by position. Zipping the two lists in order
    silently misaligns whenever our copy carries a mod the index template does
    not — a corruption enhancement, an enchant — and then compares every roll
    against the wrong range. A twice-corrupted Forgotten Warden scored its
    Dexterity against the Evasion range that way.

    The mean also hides the copy that matters: a unique whose one
    build-defining roll is near-perfect and whose filler rolls are poor
    averages out to mediocre, while the market prices it on the roll people
    actually buy it for.
    """
    templates: dict[str, list[tuple[float, float]]] = {}
    for key in MOD_KEYS:
        for mod in metadata.get(key) or []:
            if isinstance(mod, dict):
                for span in _ranges_from_structured(mod):
                    templates.setdefault(_shape(str(mod)), []).append(span)
            else:
                ranges = parse_ranges(mod)
                if ranges:
                    templates.setdefault(_shape(mod), []).append(ranges[0])

    percentiles = []
    for text in item_mods:
        spans = templates.get(_shape(text))
        values = parse_values(text)
        if not spans or not values:
            continue
        lo, hi = spans.pop(0)
        if hi <= lo:
            continue
        percentiles.append(min(max((values[0] - lo) / (hi - lo), 0.0), 1.0))
    return percentiles


def roll_score(item_mods: list[str], metadata: dict) -> float | None:
    """Mean percentile of our values within the ranges the item can roll."""
    percentiles = roll_percentiles(item_mods, metadata)
    if not percentiles:
        return None
    return sum(percentiles) / len(percentiles)


def roll_percentiles_from_item(item: dict) -> list[float]:
    """Percentiles from the item's OWN advanced descriptions.

    Exact where the index template is approximate: the ranges come from the
    same line as the roll, so nothing can misalign.
    """
    out = []
    for actual, lo, hi in (item.get("modRanges") or {}).values():
        if hi > lo:
            out.append(min(max((actual - lo) / (hi - lo), 0.0), 1.0))
    return out


def roll_score_from_item(item: dict) -> float | None:
    """Roll score using the item's OWN advanced descriptions.

    PoE2 inlines `actual(min-max)` when Advanced Item Descriptions is on, so
    a rare can be roll-scored without any index lookup at all.
    """
    ranges = item.get("modRanges") or {}
    percentiles = []
    for actual, lo, hi in ranges.values():
        if hi <= lo:
            continue
        percentiles.append(min(max((actual - lo) / (hi - lo), 0.0), 1.0))
    if not percentiles:
        return None
    return sum(percentiles) / len(percentiles)
