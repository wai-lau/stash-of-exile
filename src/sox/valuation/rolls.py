"""Roll ranges and how good a copy is within them.

This is what makes a unique's index price usable. The index reports one
number, which for a wide-rolling unique is the floor: Ventor's Gamble indexes
at ~7ex across 26,747 listings while a good one sells for many Divine.
"""

from __future__ import annotations

import re

RANGE = re.compile(r"\((\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\)")
NUMBER = re.compile(r"(\d+(?:\.\d+)?)")

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


def roll_score(item_mods: list[str], metadata: dict) -> float | None:
    """Mean percentile of our values within the ranges the item can roll."""
    templates = []
    for key in MOD_KEYS:
        for mod in metadata.get(key) or []:
            if isinstance(mod, dict):
                templates.extend(_ranges_from_structured(mod))
            else:
                ranges = parse_ranges(mod)
                if ranges:
                    templates.append(ranges[0])

    values = []
    for text in item_mods:
        found = parse_values(text)
        if found:
            values.append(found[0])

    percentiles = []
    for (lo, hi), value in zip(templates, values):
        if hi <= lo:
            continue
        percentiles.append(min(max((value - lo) / (hi - lo), 0.0), 1.0))

    if not percentiles:
        return None
    return sum(percentiles) / len(percentiles)


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
