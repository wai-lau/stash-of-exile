# Coverage Audit — What the Valuator Would Have Missed

**Date:** 2026-08-17 · **Patch:** 0.5.4b, league *Runes of Aldur*

Prompted by discovering that Lineage Support Gems were unclassified despite
Uul-Netol's Embrace indexing above Mageblood. If one six-figure class slipped
through, others likely had too. This is a full sweep of the live taxonomy
against what the design actually prices.

## Method

Enumerated every group in `/api/trade2/data/items` (10 groups) and every
`type_filters.category` (64 ids), then checked each against the set of names
poe2scout can price (**1,499 distinct names** across 15 currency categories and
7 unique categories). Anything in the game but not in that set is a hole.

## Item classes with full index coverage

| Class | Source | Count | Top price seen |
|---|---|---|---|
| Currency | `Currencies/ByCategory?category=currency` | 38 | Mirror of Kalandra — 1,547,365 ex |
| Vault keys | `vaultkeys` | 8 | Trialmaster's Reliquary Key — 169,772 ex |
| Incursion | `incursion` | 11 | Jiquani's Thesis — 148,927 ex |
| Expedition | `expedition` | 32 | Carved Majesty — 49,480 ex |
| Runes / soul cores | `runes` | 142 | Aldur's Legacy — 45,397 ex |
| Fragments | `fragments` | 17 | Azmeri Reliquary Key — 27,487 ex |
| Ritual | `ritual` | 50 | Raven-Touched Shard — 23,757 ex |
| Abyss | `abyss` | 15 | Kurgal's Gaze — 17,291 ex |
| Breach | `breach` | 26 | Refined Sibilant Catalyst — 1,458 ex |
| Essences | `essences` | 82 | Lesser Essence of Alacrity — 997 ex |
| Delirium, ultimatum, vaal, idol, verisium | — | 96 | — |
| Lineage Support Gems | `lineagesupportgems` | 75 | Uul-Netol's Embrace — 150,826 ex |
| Uncut gems | `uncutgems` | 42 | Uncut Skill Gem (L20) — 1,596 ex |
| Uniques | `Uniques/ByCategory` × 7 | 449 | The Last Flame — 1,883,232 ex |

**Omens are covered** — 40 omen-named entries appear in the priced set, so they
need no special handling.

## Holes found

Five classes exist in game, are tradeable, and have **no index price at all**.
All are moddable or tiered, so all must be priced by trade search.

### 1. Waystones — the largest hole

`Waystone (Tier 1)` … `Waystone (Tier 16)` are rare/magic items carrying
mods, and none are indexed. Scout's `map` category covers **unique** maps only.
High-tier waystones with good modifiers are routinely traded.

Priced by search: `map.waystone` + `map_filters` (`map_tier`, `map_packsize`,
`map_iir`, `map_bonus`, `map_rare_monsters`, `map_magic_monsters`) — all
verified present in the live filter list.

### 2. Tablets

`Abyss`, `Breach`, `Delirium`, `Expedition`, `Irradiated`, `Temple`,
`Overseer`, `Ritual` Tablet — atlas-modifying items with mods. No index price.
Priced by search under `map.tablet`.

### 3. Active and support skill gems

**784 of 905 gem entries have no index price.** Scout indexes uncut gems and
lineage supports only; ordinary skill/support gems are absent. A level 20 /
20% quality gem is a real trade item.

Priced by search: `gem.activegem` / `gem.supportgem` / `gem.metagem` with
`misc_filters.gem_level` and `type_filters.quality`.

### 4. Non-unique Sanctum relics

`Urn`, `Amphora`, `Vase`, `Seal`, `Coffer`, `Tapestry`, `Incense` Relic carry
mods and are unindexed. Priced by search under `sanctum.relic`.

### 5. Non-unique charms

`Thawing`, `Topaz`, `Amethyst`, `Golden`, `Staunching`, `Antidote`, `Dousing`,
`Grounding` Charm. Build guides explicitly call for charm loadouts (Titan wants
Rite of Passage; Martial Artist lists six charm varieties), so rare charms have
buyers. Priced by search under `flask.charm`.

### 6. Wombgift — unknown class, flag it

A `wombgift` group exists with five entries (`Ornate`, `Banded`, `Revelatory`,
`Lavish`, `Signet Wombgift`). No index price, and no `type_filters.category`
maps to it cleanly. Rather than guess, the tool reports these as
`unpriced:unknown-class` so they surface for manual review instead of being
silently valued at zero.

## Non-hole: divination cards

`card` appears in the 64 trade categories, but the item table has **no card
group and zero card entries**. The category is vestigial — PoE2 has no
divination cards. No work needed; recorded so it is not re-investigated.

## Structural gaps (not item classes)

Beyond taxonomy, four things affect value and were unaddressed:

1. **Special stash tab shapes.** Currency/map/gem tabs return a different JSON
   shape from normal tabs on the legacy endpoint. `ggg/stash.py` must handle
   them or those tabs read as empty — which would look like a working run.
2. **Character equipment and inventory.** Gear on characters is not in stash
   tabs. `/character` is one of the few official endpoints that *does* support
   the poe2 realm, so this is cheap to add later.
3. **Value-affecting item flags.** `corrupted`, `mirrored`, `fractured`,
   `desecrated`, quality, and socketed runes/soul cores all move price and all
   have `misc_filters` equivalents. Mirrored items in particular are
   untradeable-as-crafted and must not be priced as if they were normal.
4. **Stack size.** Currency value is per-unit × stack; the report must
   multiply, and `misc_filters.stack_size` exists if a search is ever needed.

## Net effect

Before this audit the design priced currency, uniques, and gear, and would have
silently valued at zero: every waystone, every tablet, 784 gem types, all
non-unique relics and charms, and the wombgift class. For a stash holding
high-tier waystones or a stack of level-20 gems, that is a materially wrong
total, and wrong in the direction of under-reporting — the failure mode least
likely to be noticed.
