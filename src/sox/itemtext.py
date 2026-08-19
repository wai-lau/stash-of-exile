"""Parse the item text PoE2 puts on the clipboard with Ctrl+C.

There is no PoE2 stash API, so this is how items get in. The format is
section-based, sections separated by a line of dashes:

    Item Class: Bows
    Rarity: Rare
    Oblivion Strike          <- rare/unique carry a name AND a base line
    Rider Bow
    --------
    Physical Damage: 36-61
    Critical Hit Chance: 5.00%
    --------
    Requires: Level 51, 103 (augmented) Dex
    --------
    Item Level: 80
    --------
    { Prefix Modifier "Shocking" (Tier: 4) - Damage, Elemental, Lightning, Attack }
    Adds 5(1-5) to 82(62-89) Lightning Damage

With "Advanced Item Descriptions" enabled the game also emits the modifier
header shown above and inlines each roll as `actual(min-max)`. That is a
gift: it gives the tier and the roll range without a lookup, so rares can be
roll-scored the same way uniques are.

The output dict deliberately mirrors the trade API's item shape so that
everything downstream (classify, index pricing, candidate scoring) works
unchanged whether an item came from the clipboard or from a trade fetch.
"""

from __future__ import annotations

import re

SEPARATOR = re.compile(r"^-{3,}$")

# "{ Prefix Modifier "Shocking" (Tier: 4) - Damage, Elemental, Attack }"
# "{ Unique Modifier - Defences }"
# "{ Desecrated Suffix Modifier "of Amanamu" (Tier: 1) - ... }"  <- TWO words
# before "Modifier", so the kind must not be a single \w+.
MOD_HEADER = re.compile(
    r"^\{\s*(?P<kind>\w+)(?:\s+\w+)*?\s+Modifier"
    r'(?:\s+"(?P<name>[^"]*)")?'
    r"(?:\s*\(Tier:\s*(?P<tier>\d+)\))?"
    r".*\}$"
)

# "5(1-5)" -> actual 5, range (1, 5).  Also "+57(41-60)", "56(50-70)%"
ROLL = re.compile(r"(-?\d+(?:\.\d+)?)\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)")

# A stat line can carry its own marker, which OVERRIDES the section header:
#   "36% increased Physical Damage (rune)"
#   "Adds 32 to 59 Physical Damage (fractured)"  <- inside a Prefix Modifier block
MOD_MARKER = re.compile(r"\s*\((implicit|fractured|rune|desecrated|enchant|crafted|scourge)\)\s*$", re.I)

# An unrevealed desecrated modifier occupies an affix slot but its stat is
# not yet known, so it is worth nothing until revealed. The whole line IS the
# marker. The exact literal could not be confirmed from GGG data, so a pattern
# family is matched rather than one guessed string: PoE2 wording is
# "Unrevealed Prefix/Suffix Modifier" (cf. the pseudo stats "# Unrevealed
# Prefix Modifiers"), and PoE1 used "Veiled".
UNREVEALED = re.compile(r"^(?:unrevealed|veiled)\b.*modifier\b", re.I)

# Uncut gems put the level in the name: "Uncut Skill Gem (Level 19)".
GEM_LEVEL = re.compile(r"\(Level\s+(\d+)\)")

# frameType values the trade API uses; kept so downstream code sees one shape.
FRAME_BY_RARITY = {
    "normal": 0, "magic": 1, "rare": 2, "unique": 3,
    "gem": 4, "currency": 5,
}

# Header lines that are flags rather than key/value properties.
FLAGS = {
    "corrupted": "corrupted",
    "twice corrupted": "twiceCorrupted",
    "sanctified": "sanctified",
    "mirrored": "mirrored",
    "unidentified": "unidentified",
    "split": "split",
}

# A twice-corrupted item prints only "Twice Corrupted" — never "Corrupted" as
# well — so the plain flag has to be implied or the item reads as untouched.
IMPLIES = {"twiceCorrupted": "corrupted"}

# Section keys that are not item properties.
_SKIP_PROPERTY_KEYS = {"requires", "item level", "stack size", "note"}


def _split_sections(text: str) -> list[list[str]]:
    sections: list[list[str]] = [[]]
    for line in text.splitlines():
        line = line.rstrip()
        if SEPARATOR.match(line.strip()):
            sections.append([])
        elif line.strip():
            sections[-1].append(line)
    return [s for s in sections if s]


def _strip_augmented(value: str) -> str:
    return re.sub(r"\s*\((?:augmented|unmet)\)", "", value).strip()


def split_marker(line: str) -> tuple[str, str | None]:
    """Return (line without its marker, marker) — e.g. "(fractured)"."""
    match = MOD_MARKER.search(line)
    if not match:
        return line.strip(), None
    return MOD_MARKER.sub("", line).strip(), match.group(1).lower()


def gem_level(name: str) -> int | None:
    match = GEM_LEVEL.search(name or "")
    return int(match.group(1)) if match else None


def strip_rolls(text: str) -> str:
    """Turn "Adds 5(1-5) to 82(62-89) Lightning Damage" into the plain line.

    The allowlist matches on plain mod text, so advanced descriptions must be
    reduced to what the game shows without them.
    """
    return ROLL.sub(r"\1", text).strip()


def parse_rolls(text: str) -> list[tuple[float, float, float]]:
    """Every (actual, min, max) triple in an advanced mod line."""
    return [(float(a), float(lo), float(hi)) for a, lo, hi in ROLL.findall(text)]


def parse(text: str) -> dict:
    """Parse clipboard item text into a trade-API-shaped item dict."""
    sections = _split_sections(text)
    if not sections:
        raise ValueError("empty item text")

    item: dict = {
        "itemClass": None,
        "rarity": None,
        "name": None,
        "typeLine": None,
        "baseType": None,
        "identified": True,
        "corrupted": False,
        "twiceCorrupted": False,
        "sanctified": False,
        "mirrored": False,
        "properties": [],
        "implicitMods": [],
        "explicitMods": [],
        "fracturedMods": [],
        "runeMods": [],
        "desecratedMods": [],
        "enchantMods": [],
        "unrevealedMods": [],
        "modTiers": {},
        "modRanges": {},
    }

    header = sections[0]
    rest = sections[1:]

    name_lines: list[str] = []
    for line in header:
        if line.startswith("Item Class:"):
            item["itemClass"] = line.split(":", 1)[1].strip()
        elif line.startswith("Rarity:"):
            item["rarity"] = line.split(":", 1)[1].strip()
        else:
            name_lines.append(line.strip())

    # Rare and unique items print a name line then a base line. Normal and
    # magic print one line, which IS the base (magic wraps it in affixes).
    if len(name_lines) >= 2:
        item["name"] = name_lines[0]
        item["baseType"] = name_lines[1]
        item["typeLine"] = name_lines[1]
    elif name_lines:
        item["baseType"] = name_lines[0]
        item["typeLine"] = name_lines[0]

    rarity = (item["rarity"] or "").lower()
    item["frameType"] = FRAME_BY_RARITY.get(rarity, 0)

    # Uncut gems name their level, and the index keys on exactly that string.
    level = gem_level(item["baseType"] or "")
    if level is not None:
        item["gemLevel"] = level

    for section in rest:
        _parse_section(section, item)

    return item


def _parse_section(section: list[str], item: dict) -> None:
    joined = " ".join(section).lower()

    # Flag-only sections such as a lone "Corrupted".
    for line in section:
        key = line.strip().lower()
        if key in FLAGS:
            if key == "unidentified":
                item["identified"] = False
            else:
                item[FLAGS[key]] = True
                implied = IMPLIES.get(FLAGS[key])
                if implied:
                    item[implied] = True

    if section[0].startswith("Item Level:"):
        item["ilvl"] = int(re.sub(r"\D", "", section[0]) or 0)
        return
    if section[0].startswith("Stack Size:"):
        raw = section[0].split(":", 1)[1].split("/")[0]
        item["stackSize"] = int(re.sub(r"\D", "", raw) or 1)
        return
    if section[0].startswith("Requires"):
        return
    note = NOTE_LINE.match(section[0].strip())
    if note:
        # Keep it: it is the seller's own asking price, worth reporting.
        item["note"] = note.group(1).strip()
        return
    if "modifier" in joined and "{" in joined:
        _parse_mod_section(section, item)
        return
    if any(line.strip().lower() in FLAGS for line in section):
        return

    # Property section, e.g. "Energy Shield: 44 (augmented)".
    #
    # A property can share a section with real mods: a unique that grants a
    # skill prints "Grants Skill: Level 20 Spirit Vessel" at the head of its
    # explicit block. Lines that are not properties fall through to the mod
    # pass below, because treating the whole section as properties on the
    # strength of one colon dropped every mod under it.
    matched_property = False
    leftover = []
    for line in section:
        if ":" not in line:
            leftover.append(line)
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), _strip_augmented(value)
        if key.lower() in _SKIP_PROPERTY_KEYS or not value:
            continue
        item["properties"].append({"name": key, "values": [[value, 0]]})
        matched_property = True

    # A section with no colons and no modifier header is either an implicit
    # (when advanced descriptions are off) or unique flavour text. Only lines
    # that look like a stat are kept.
    for line in (leftover if matched_property else section):
            stripped, marker = split_marker(line)
            # Checked BEFORE _looks_like_mod: "Unrevealed Suffix Modifier"
            # carries no number, so the mod-shape filter would drop it and the
            # affix slot it occupies would look free.
            if UNREVEALED.match(stripped):
                item["unrevealedMods"].append(stripped)
                continue
            if not _looks_like_mod(line):
                continue
            field = _KIND_TO_FIELD.get(marker or "", "explicitMods")
            plain = strip_rolls(stripped)
            item[field].append(plain)
            # Advanced descriptions inline the roll range here too, not only
            # inside a "{ ... Modifier }" block. Missing them left desecrated
            # and rune mods with no roll quality, which decides both the roll
            # score and which mods a search keeps.
            rolls = parse_rolls(stripped)
            if rolls:
                actual, lo, hi = max(rolls, key=lambda r: r[2] - r[1])
                item["modRanges"][plain] = (actual, lo, hi)


# A copied trade listing carries the seller's asking price as a note. It is
# not a modifier and must not be scored as one.
NOTE_LINE = re.compile(r"^Note:\s*(.*)$", re.I)


def _looks_like_mod(line: str) -> bool:
    """A stat line contains a number or a known stat verb; flavour text does not."""
    if NOTE_LINE.match(line.strip()):
        return False
    if any(ch.isdigit() for ch in line):
        return True
    return bool(re.search(r"\b(cannot|immune|gain|grants|has|have)\b", line, re.I))


_KIND_TO_FIELD = {
    "prefix": "explicitMods",
    "suffix": "explicitMods",
    "explicit": "explicitMods",
    "implicit": "implicitMods",
    "unique": "explicitMods",
    "fractured": "fracturedMods",
    "rune": "runeMods",
    "desecrated": "desecratedMods",
    "enchant": "enchantMods",
    "crafted": "explicitMods",
}


def _parse_mod_section(section: list[str], item: dict) -> None:
    """Advanced descriptions: a header line then one or more stat lines."""
    field = "explicitMods"
    tier: int | None = None

    for line in section:
        header = MOD_HEADER.match(line.strip())
        if header:
            kind = (header.group("kind") or "").lower()
            field = _KIND_TO_FIELD.get(kind, "explicitMods")
            tier = int(header.group("tier")) if header.group("tier") else None
            continue

        stripped, marker = split_marker(line)
        if UNREVEALED.match(stripped):
            # Occupies an affix slot, contributes nothing knowable.
            item["unrevealedMods"].append(stripped)
            continue
        plain = strip_rolls(stripped)
        if not plain:
            continue
        # A per-line marker wins over the block header: a fractured mod can
        # appear inside a Prefix Modifier block and is still fractured.
        target = _KIND_TO_FIELD.get(marker, field) if marker else field
        item[target].append(plain)
        if tier is not None:
            item["modTiers"][plain] = tier
        rolls = parse_rolls(line)
        if rolls:
            # Store the widest span on the line; that is what roll scoring uses.
            actual, lo, hi = max(rolls, key=lambda r: r[2] - r[1])
            item["modRanges"][plain] = (actual, lo, hi)
