"""Load the generated data files.

Produced by scripts/resolve_*.py against GGG's live tables. Do not hand-edit;
regenerate instead.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class ModEntry:
    ids: list[str]
    slug: str
    text: str
    weight: int
    category: str
    tags: tuple[str, ...] = ()
    local_ids: tuple[str, ...] = ()
    subject: str = "self"
    minion_subtype: str | None = None
    ambiguous: bool = False


@dataclass(frozen=True)
class BaseRules:
    ilvl_tiers: list[tuple[int, int]]
    slots: dict[str, int]
    named: dict[str, int]
    avoid: set[str] = field(default_factory=set)
    rune_prefixes: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class UniqueRules:
    thresholds: dict[str, float]
    entries: dict[str, int]


def _read(name: str) -> dict:
    with (DATA / name).open("rb") as fh:
        return tomllib.load(fh)


def load_mods() -> list[ModEntry]:
    raw = _read("mod_allowlist.toml")
    entries = []
    for category in raw.get("category", []):
        for mod in category.get("mod", []):
            entries.append(ModEntry(
                ids=list(mod["ids"]),
                slug=mod["slug"],
                text=mod["text"],
                weight=int(mod["weight"]),
                category=category["name"],
                tags=tuple(mod.get("tags", ())),
                local_ids=tuple(mod.get("local_ids", ())),
                subject=mod.get("subject", "self"),
                minion_subtype=mod.get("minion_subtype"),
                ambiguous=bool(mod.get("ambiguous", False)),
            ))
    return entries


def load_bases() -> BaseRules:
    raw = _read("base_allowlist.toml")
    tiers = sorted(
        ((int(t["min"]), int(t["weight"])) for t in raw.get("ilvl_tier", [])),
        key=lambda pair: -pair[0],
    )
    return BaseRules(
        ilvl_tiers=tiers,
        slots={s["category"]: int(s["weight"]) for s in raw.get("slot", [])},
        named={b["name"]: int(b["weight"]) for b in raw.get("named_base", [])},
        avoid={b["name"] for b in raw.get("avoid_base", [])},
        rune_prefixes={f["prefix"]: int(f["bonus_weight"]) for f in raw.get("rune_family", [])},
    )


def load_uniques() -> UniqueRules:
    raw = _read("unique_allowlist.toml")
    return UniqueRules(
        thresholds={k: float(v) for k, v in (raw.get("thresholds") or {}).items()},
        entries={u["name"]: int(u["weight"]) for u in raw.get("unique", [])},
    )


def load_notables() -> dict[str, str]:
    """Notable name -> trade2 stat id. Prices Megalomaniac-class jewels."""
    return dict(_read("notables.toml").get("notable", {}))
