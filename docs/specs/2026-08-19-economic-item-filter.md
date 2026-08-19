# An item filter priced by the inventory slot

**Status:** design, awaiting review
**Date:** 2026-08-19
**League measured:** Runes of Aldur (1 div = 360.5 ex by the exchange midpoint)

## The rule

A unit of inventory space is worth at least 1 exalted. Show an item only if
it clears that per slot.

```
slot_value = price_ex × stack_factor / (width × height)
stack_factor = max_stack_size for a stackable, else 1

SHOW iff slot_value >= 1.0 ex  AND  price_ex >= unit_floor      (default 0.1)
```

The unit floor is what stops a full-stack assumption from justifying a single
Scroll of Wisdom. Forty of them are worth 1 ex of space; the one on the floor
is worth 0.025 and will not become forty.

### Exempt: use value is not market value

Never touched by the layer, at any price:

Gold · Quest Items · Instance Local Items · Waystones · Pinnacle Keys ·
Vault Keys · Expedition Logbooks · Djinn Barya · Inscribed Ultimatum

You map with these. Pricing them asks the wrong question, and the answer
would be "leave the waystone on the floor".

## What a filter can actually express

Two constraints from the syntax, both load-bearing:

- **`BaseType`, never a unique's name.** A rule for `Silk Robe` shows
  Temporalis and every trash Silk Robe unique alike. So a base is shown when
  its *best* unique clears the bar. 121 of 372 unique-bearing bases survive.
- **First match wins**, and `Continue` resumes matching rather than locking a
  verdict in. There is no "stop, this one is shown" — so the layer cannot let
  an item through by matching it. It can only stay silent.

Confirmed available: `TwiceCorrupted`, `AnyEnchantment`, `Mirrored`,
`Quality`, `Sockets`, `AreaLevel`, `ItemLevel`, `StackSize`, `WaystoneTier`,
`UnidentifiedItemTier`, with `<` `<=` `>` `>=` `==`.

## Shape: a Hide-only layer prepended to NeverSink

`sox filter` emits `[SOX ECONOMIC LAYER]` followed by the NeverSink file
verbatim, into one combined `.filter`.

The layer contains **only Hide rules**. Anything clearing the bar matches
nothing in it, falls through, and keeps NeverSink's styling, sounds and
minimap icons. Three consequences, all wanted:

- no restyling work, and no second opinion about what a good drop looks like
- NeverSink updates stay compatible — drop in a new version, regenerate
- the layer is auditable on its own: every line in it hides something, and
  the header says why

Every Hide rule carries escape guards, so an exceptional copy of a hidden
base still shows:

```
    TwiceCorrupted False
    AnyEnchantment False
    Mirrored False
    Quality < 21
    Sockets < 2          # gear only
    AreaLevel >= 65      # the campaign is left alone
```

Rules are grouped one per (class-group × guard-set) with a long
`BaseType == "..." "..."` list — NeverSink's own idiom, and what keeps the
layer a few dozen blocks rather than a few thousand.

## Three commands

### `sox filter` — emit the layer

```
sox filter --base HMM.filter -o SOX.filter
           [--slot-value 1.0] [--unit-floor 0.1] [--min-area-level 65]
           [--dry-run]
```

Instant: reads the cached tables below and the index, emits, done. `--dry-run`
prints per-category counts and the ten items nearest the threshold, which is
where a wrong price does visible damage.

The generated header records league, divine rate, thresholds, the age of each
input table, and how many bases each section hides.

### `sox bulk` — price everything the exchange carries

780 ids across 14 groups: Currency 40, Fragments 32, Verisium 28, Runes 213,
Expedition 40, Vaal 51, Delirium 29, Breach 35, Ritual 75, Abyss 16,
Essences 84, UncutGems 45, LineageSupportGems 76, Waystones 16.

Reuses `price_by_exchange`, so each id costs two calls (both sides of the
book) or one with `--ask-only`.

**Cost is the design constraint.** `trade-exchange-request-limit` is
`5:15:60,10:90:300,30:300:1800` — 6 calls a minute sustained. So:

| sweep | calls | wall clock |
|---|---|---|
| two-sided, all ids | 1560 | ~4.3 h |
| `--ask-only`, all ids | 780 | ~2.2 h |
| `--verify-below 10ex` | ~400 | ~1.1 h |

Resumable, with per-row timestamps, into `~/.local/share/sox/bulk.toml`. A
rerun refreshes only stale rows. This is a thing you start and walk away
from, once a week — not something `sox filter` triggers.

### `sox bases` — price the crafting bases

```
sox bases [--ilvls 82] [--rarities normal,magic] [--status any] [--resume]
```

For each (base, rarity, ilvl) it runs one trade search, unidentified, and
takes the cheapest listing as that base's commodity floor. Listings carry
`w`/`h`, so the sweep also collects base dimensions — which is the only
source for them on bases no unique uses.

Scope: the ~155 bases NeverSink already curates as crafting bases, parsed out
of the `--base` filter itself rather than hardcoded, at ilvl 82. That is 310
searches, about 52 minutes at the search endpoint's 6/min. `--ilvls
79,80,81,82` widens it to ~3.4 h.

Writes `~/.local/share/sox/base_prices.toml`. Resumable.

**Until that file exists, every rare, magic and normal gear item at
AreaLevel >= 65 is hidden.** That is the strict default, and it is the
honest one: an unpriced base has not earned a slot.

## Data sources

| what | source | notes |
|---|---|---|
| currency, runes, essences, omens, fragments, gems, waystones | `sox bulk` → exchange | index as fallback for ids the exchange lacks |
| uniques | poe2scout index | grouped by base; the base's value is its best unique |
| item dimensions | base64 in the icon URL | `{"w":2,"h":3}`; verified on 449 uniques |
| stack sizes | index `ItemMetadata.max_stack_size` | 1 to 300 |
| rare/magic/normal bases | `sox bases` → trade search | plus `w`/`h` from the listings |

## Emission order

```
[SOX ECONOMIC LAYER]
  header comment: league, rates, thresholds, table ages, counts
  Hide  uniques by base type        (guarded)
  Hide  bulk-priced items below bar  (guarded)
  Hide  gear not on the base allowlist (guarded)
[NEVERSINK FILTER, verbatim]
```

Order within the layer is irrelevant — the three sets are disjoint by class.
Order *against* NeverSink is everything: the layer must come first, or its
Hides never run.

## Testing

- Pure functions — `slot_value`, icon-dimension decode, guard emission — unit
  tested against the gzipped fixtures already in `tests/fixtures`.
- A golden-file snapshot of the emitted layer for a fixed input table, so a
  change in what gets hidden is visible in review rather than in game.
- A syntax lint over every emitted block: known keywords, known operators,
  balanced quoting. Cheap, and it catches the class of bug that silently
  disables a filter.
- A drift test in the style of `test_resolver_drift.py`: fail loudly if the
  index drops `max_stack_size`, or the icon URL stops carrying `w`/`h`.

## Known limits, accepted

- **The index floors trash uniques at exactly 1.0 ex**, so no 2×4 weapon base
  can reach 8 ex/slot. Widowhail — a real build item, quantity 16,120, priced
  at that floor — is hidden. Correct under the rule, and a real consequence.
- **A base is judged by its best unique.** Bases carrying one jackpot and
  forty duds stay visible, and most of what they show is a dud.
- **Tables go stale between sweeps.** The header prints their age; nothing
  auto-refreshes, because a 4-hour sweep must never start because you ran
  `sox filter`.
- **Rares are hidden wholesale until `sox bases` has run.** Deliberate.

## Out of scope

Restyling, sounds, minimap icons, leveling rules, anything NeverSink already
does well. The layer subtracts; it does not decorate.
