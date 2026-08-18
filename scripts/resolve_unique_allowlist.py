#!/usr/bin/env python3
"""Resolve the build-relevant unique allowlist.

Uniques named by 0.5 build guides are verified against the live trade2 item
table, then enriched with poe2scout index price, listing quantity, and a
computed roll spread (the max/min ratio across the unique's rolled ranges).

The spread is what decides whether the index price can be trusted for a given
copy. A unique with wide ranges is mispriced by any single index number, which
is the Ventor's Gamble problem: the index reports the floor, while a
well-rolled copy sells for orders of magnitude more.

Spread alone is NOT sufficient to justify a search — Thunderfist has a x111
spread and costs ~3 exalted. Escalation requires a wide spread AND a good roll
on our actual copy (or corruption, or a high index price).

Usage:
    python3 scripts/resolve_unique_allowlist.py items.json /tmp/scout/*.json \
        > src/sox/data/unique_allowlist.toml
"""

import glob
import json
import re
import sys

RANGE = re.compile(r"\((\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\)")

def normalize_name(text):
    """Fold case/whitespace so a patch that re-cases a name cannot drop it.

    Mirrors scripts/resolve_allowlist.py::normalize. Item and unique names are
    proper nouns and rarely churn, but the cost of tolerating it is one regex.
    """
    return re.sub(r"\s+", " ", text.casefold()).strip()


# Uniques named outright by the 0.5 build guides, with the build that wants
# them. weight 3 = build-defining/chase, 2 = commonly slotted, 1 = budget pick.
NAMED_UNIQUES = [
    ("Mageblood", 3, "universal chase belt"),
    ("Headhunter", 3, "Titan endgame belt target"),
    ("The Taming", 3, "Gemling/Spirit Walker: biggest single ring upgrade"),
    ("The Brass Dome", 3, "Whirling Assault Titan: mandatory"),
    ("Temporalis", 3, "chase"),
    ("Hyrri's Ire", 2, "Spirit Walker Twisters endgame body"),
    ("Loreweave", 2, "Gemling Twister: Polcirkeln modifier"),
    ("Perfidy", 2, "Gemling Twister: Raven-touched variant"),
    ("Skysliver", 2, "Deadeye/Gemling attack-speed spear set"),
    ("Nazir's Judgement", 2, "Martial Artist budget-to-mid quarterstaff"),
    ("Splinterheart", 2, "Deadeye bow"),
    ("Slivertongue", 2, "Deadeye bow"),
    ("Mist Whisper", 2, "Witchhunter crossbow"),
    ("Adonia's Ego", 3, "Spark/Comet Gemling enabler"),
    ("Maligaro's Virtuosity", 2, "crit damage scaling"),
    ("Ryslatha's Coil", 2, "Titan belt until Headhunter"),
    ("Ventor's Gamble", 2, "roll-dependent: index price is the floor only"),
    ("Sacred Flame", 2, "damage as extra fire; skips penetration"),
    ("Berek's Grip", 1, "budget Gemling ring"),
    ("Berek's Pass", 1, None),
    ("Berek's Respite", 1, None),
    ("Evergrasping Ring", 2, "Lich/Infernalist minion chaos scaling"),
    ("Cloak of Flame", 2, "Oracle: phys taken as fire"),
    ("Sacrosanctum", 2, "Oracle: recoup applies to ES"),
    ("Defiance of Destiny", 2, "Oracle: recovery on hit"),
    ("Goregirdle", 2, "Shaman: armour-applies-to-elemental combo"),
    ("Bones of Ullr", 2, "Disciple of Varashta: spirit boots"),
    ("Enfolding Dawn", 2, "Disciple of Varashta chest"),
    ("Soul Mantle", 2, None),
    ("Waveshaper", 2, "Disciple of Varashta shield"),
    ("Facebreaker", 2, "Martial Artist: Way of the Stonefist interaction"),
    ("Darkness Enthroned", 2, "Gemling belt"),
    ("Lavianga's Spirits", 2, "always-on mana flask, many builds"),
    ("Valako's Roar", 2, "frenzy charge charm"),
    ("Rite of Passage", 2, "Titan charm: rarity + res"),
    ("Split Personality", 2, "Gemling: saves ~15 passive points"),
    ("Megalomaniac", 3, "notable-dependent, hugely variable"),
    ("Heart of the Well", 2, "Invoker jewel"),
]

# Escalation thresholds, seeded from the live index distribution.
THRESHOLDS = {
    "chase_price_ex": 5000,
    "swing_ratio": 2.0,
    "roll_score_percentile": 0.75,
    "illiquid_quantity": 20,
}


def spread(item):
    """Max/min ratio across every rolled range on the unique."""
    meta = item.get("ItemMetadata") or {}
    ratios = []
    for key in ("explicit_mods", "implicit_mods"):
        for mod in meta.get(key) or []:
            if isinstance(mod, dict):
                for sub in mod.get("mods") or []:
                    for mag in sub.get("magnitudes") or []:
                        try:
                            lo, hi = float(mag["min"]), float(mag["max"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        if lo > 0 and hi > lo:
                            ratios.append(hi / lo)
                continue
            for lo, hi in RANGE.findall(mod):
                lo, hi = float(lo), float(hi)
                if lo > 0 and hi > lo:
                    ratios.append(hi / lo)
    return max(ratios) if ratios else 1.0


def main():
    items = json.load(open(sys.argv[1]))
    known = {
        normalize_name(e["name"]): (e["name"], g["id"], e.get("type"))
        for g in items["result"]
        for e in g["entries"]
        if e.get("name")
    }

    scout = {}
    for pattern in sys.argv[2:]:
        for path in glob.glob(pattern):
            data = json.load(open(path))
            if isinstance(data, dict):
                for it in data.get("Items", []):
                    scout[it["Name"]] = it

    out = [
        "# Build-relevant uniques for sox.",
        "# GENERATED by scripts/resolve_unique_allowlist.py — every name verified",
        "# against the live trade2 item table, enriched from the poe2scout index.",
        "#",
        "# index_price/quantity/spread are a SNAPSHOT for threshold tuning. The tool",
        "# recomputes them from live scout data at runtime; do not treat as current.",
        "",
        "[thresholds]",
    ]
    out.append("# Escalate a unique to a live trade search when ANY holds:")
    out.append("#   corrupted, OR index_price >= chase_price_ex,")
    out.append("#   OR (spread >= swing_ratio AND our roll >= roll_score_percentile)")
    out.append("# Spread alone is deliberately NOT enough: Thunderfist spreads x111 at ~3ex.")
    for key, value in THRESHOLDS.items():
        out.append(f"{key} = {value}")
    out.append("")

    missing, resolved = [], 0
    for name, weight, note in NAMED_UNIQUES:
        hit = known.get(normalize_name(name))
        if hit is None:
            missing.append(name)
            continue
        resolved += 1
        # Write the table's canonical spelling, not whatever we typed.
        name, group, base = hit
        out += ["[[unique]]", f'name = "{name}"', f"weight = {weight}"]
        out.append(f'group = "{group}"')
        if base:
            out.append(f'base = "{base}"')
        entry = scout.get(name)
        if entry:
            price = entry.get("CurrentPrice")
            if price is not None:
                out.append(f"index_price_ex = {round(price, 2)}")
            out.append(f"quantity = {entry.get('CurrentQuantity') or 0}")
            out.append(f"spread = {round(spread(entry), 2)}")
        else:
            out.append("# not present in the scout index snapshot")
        if note:
            out.append(f'note = "{note}"')
        out.append("")

    print("\n".join(out))
    total = len(NAMED_UNIQUES)
    print(f"# {resolved}/{total} named uniques verified; {len(scout)} priced by scout", file=sys.stderr)
    if missing:
        print("# UNRESOLVED (not in the live item table):", file=sys.stderr)
        for name in missing:
            print(f"#   {name}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
