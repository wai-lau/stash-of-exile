# What the 0.5 Meta Gears For — Research Behind the Default Mod Allowlist

**Date:** 2026-08-17 · **Patch:** 0.5.4b, league *Runes of Aldur* (started 2026-05-29)

Purpose: derive `src/sox/data/mod_allowlist.toml` from what players actually
gear for, rather than from guesswork. The allowlist decides which rares earn a
live trade search, so a wrong list means either wasted rate budget or missed
value.

## Method

Read the top build for each of the 22 ascendancies, plus extra builds for the
popular ones, and recorded the **gear/stat priority** section of each. Then
resolved every recorded affix against the live
`/api/trade2/data/stats` table so the allowlist ships verified stat ids.

**Aggregate character data was not used.** poe.ninja's builds/profiles API is
closed — it is internal and unsupported, and they note that serving it to third
parties would bypass the choice of players who hid their profiles. Published
build guides were used instead, which also state priorities explicitly.

## League context that reshapes the list

*Runes of Aldur* hit Energy Shield with **64 separate nerfs**, and several
previously dominant builds were removed from viability. Consequences for the
allowlist:

- **Maximum Life is back to being the universal defensive mod.** It gets the
  top defensive weight; ES is demoted from "the only defence" to "one of
  several".
- **Deflection became a headline stat.** Community pricing guides call
  Deflection and Spirit Reservation Efficiency the single most valuable chest
  mods, with Deflection alone able to more than double a chest's worth.
- Evasion and Armour hybrids matter again, since Deflection scales off both.

## Per-ascendancy findings

Popularity at the 2026-08-12 snapshot: Martial Artist ~20%, Gemling
Legionnaire ~16%, Spirit Walker ~12–19%. Those got extra builds read; the
low-population ascendancies got their top build only.

| Ascendancy | Build(s) read | Gear priorities recorded |
|---|---|---|
| Spirit Walker (Huntress) | Twisters, +minion/companion variants | Attack speed (set I); triple flat phys/cold prefixes (set II); crit chance; projectile skill levels; Life → ele res → chaos res → evasion → deflection → ES |
| Martial Artist (Monk) | Whirling Assault, Falling Thunder, Hollow Form | PDPS 550+; 15%+ crit; +levels melee/attack skills; glove flat damage → gain % as extra; crit damage bonus; ES+evasion hybrid bases; 150%+ rarity; 20–35% move speed |
| Gemling Legionnaire (Merc) | Twister, Spark/Comet | Flat elemental damage; mana cost efficiency ("most important affix"); cast speed; ES prefixes; mana on rings; 4–5 res suffixes; 2 attribute suffixes; gem quality |
| Deadeye (Ranger) | Twister, Lightning Arrow | Bow phys + ele; +projectile gem levels; added flat damage; global phys; projectile speed; attack speed; evasion 1200–1500+; deflection from evasion; ele res cap; life |
| Lich (Witch) | Essence Drain, Minion Army | +levels chaos/spell/minion skills; spell/chaos damage; cast speed; max ES; **+50 spirit**; ES recharge; withered/curse magnitude; mana on kill |
| Titan (Warrior) | Whirling Assault | Weapon phys damage; crit chance + damage bonus; AoE; max rage; melee skill levels; res cap → life → armour → **armour also applies to elemental damage**; phys leech as life |
| Witchhunter (Merc) | Oil Grenade | Crossbow phys damage tiers; % phys; added phys; mana on kill; evasion; life; res (incl. chaos); move speed; ring flat ele damage; +15 spirit |
| Stormweaver (Sorc) | Arc | +levels lightning/spell skills; lightning/spell damage; gain % as extra elemental; spell crit; cast speed; 300+ ES; **+47 spirit**; max mana; mana regen/cost efficiency; 25–35% move speed |
| Invoker (Monk) | Falling Thunder | Attack speed; ele damage with attacks; crit chance; % phys; added phys/ele; damage as extra cold; attack skill levels; life → ele res → max ES → evasion |
| Pathfinder (Ranger) | Spiral Volley Poison | Bow phys ("makes or breaks"); +projectile gem levels; attack speed; crit; poison magnitude; ailment magnitude; bow damage; global phys; mana on kill; evasion; life; res |
| Amazon (Huntress) | Lightning Spear | Accuracy; crit chance; +spear/attack skill levels; lightning/phys damage; flat damage to attacks on gloves/rings; phys leeched as mana; evasion→ES; crit damage bonus; move speed |
| Tactician (Merc) | Supporting Fire | +levels minion skills; added ele damage to attacks; minion damage/AoE; max ES; spirit; move speed; res cap; life early |
| Oracle (Druid) | Lunar Assault Werewolf | % phys; added phys/ele; attack speed; +melee skill levels; res cap → recoup → life → ES → armour + armour-applies-to-elemental |
| Shaman (Druid) | Walking Calamity | Weapon phys; added phys; ele adds; +attack/melee gem levels; crit later; max rage; mana leech; res → life → phys taken as element → armour applies to ele/chaos → strength |
| Infernalist (Witch) | Minion Army | +levels minion skills; **+spirit / increased spirit**; minion damage; minion added attack phys; minion attack speed; res (76% cap); +max fire res; ES; life; block; minion reservation efficiency |
| Acolyte of Chayula (Monk) | Poisonburst Arrow | Bow phys; crit; +projectile levels; flat damage on jewellery; attack speed; **chaos res first** (doubled by ascendancy); ES; evasion; move speed |
| Warbringer (Warrior) | Shield Wall | Weapon damage irrelevant — only +attack/melee skill levels, gain % as extra phys, damage vs fully broken armour; shield armour; body life+armour; mana leech; lightning to attacks; res → life → armour → armour-applies-to-elemental |
| Disciple of Varashta (Sorc) | Minion | +levels minion skills; spirit; ES; life; res; ES recharge; runic ward; cast speed; minion damage/crit; presence AoE |
| Ritualist (Huntress) | Bleed | +levels melee skills; bleed; jewellery scaling (extra ring slot amplifies ring/amulet mods 25%) |
| Smith of Kitava (Warrior) | Shield Wall / Boneshatter | Ascendancy grants resistances, freeing gear for damage affixes; life; strength; fire res |
| Blood Mage (Witch) | — | Guide sources returned 403; covered by the shared spell/life core |
| Chronomancer (Sorc) | Ice Nova CoA / Recoup | Crit to 100%; ES stacking; recoup; res during campaign |

## What this produced

Cross-build convergence is strong. The mods almost every build named:

1. **+to Level of all `<X>` Skills** — the single best scaler on jewellery and
   weapons across every archetype (melee, projectile, spell, minion, chaos,
   elemental). Weight 3.
2. **Maximum Life** — universal post-nerf. Weight 3.
3. **Spirit** (`+to Spirit`, `% increased Spirit`, reservation efficiency) —
   gates buff and minion builds outright. Weight 3.
4. **Flat added damage** (phys and each element, both plain and to-attacks) —
   the core weapon/jewellery prefix everywhere. Weight 3/2.
5. **Crit chance + crit damage bonus** — near-universal. Weight 3.
6. **Deflection conversion mods** — the league's premium chest stat. Weight 3.
7. **Movement speed** — a hard gate on boots. Weight 3.
8. **Resistances** — individually cheap (weight 1), but valuable as the
   `pseudo` totals (weight 2); **maximum** resistances are rare and pricey
   (weight 3).
9. **Armour also applies to Elemental/Chaos Damage** — armour-stacker enabler.
   Weight 3.

Result: **92 mods across 13 categories, all 92 resolved to verified stat ids**
(30 at weight 3, 43 at weight 2, 19 at weight 1). Generated by
`scripts/resolve_allowlist.py`, which fails loudly on any text that does not
match the live table — it will not emit an unverified id.

Two capitalization traps the resolver caught, both real:

- `Adds # to # Physical Damage to Attacks` (capital D) vs
  `Adds # to # Fire damage to Attacks` (lowercase d).
- `#% increased chance to Shock` is lowercase "chance".

These are no longer hand-maintained hazards — matching now folds case,
whitespace and a leading `+`, and resolved ids are locked by a slug derived
from our own text so rewording cannot drop an entry. See "Surviving patches"
in the spec.

Chasing that down exposed a worse problem than capitalization: **the same mod
text can map to several stat ids**. `# to Spirit`, `#% increased Spirit` and
`# to maximum Runic Ward` each have two, with no distinguishing field:

```json
{"id": "explicit.stat_3981240776", "text": "# to Spirit", "type": "explicit"}
{"id": "explicit.stat_2704225257", "text": "# to Spirit", "type": "explicit"}
```

The first version of the resolver silently took whichever came first — a coin
flip on two weight-3 mods. They now emit an OR group across both ids.

## Crafting bases and item level

Build hunting surfaced a second market the mod allowlist does not cover:
players buy **white and magic bases to craft on**, and there the price driver is
item level and base class, not mods.

**Item level breakpoints.** Endgame crafting targets **ilvl 82**, where every
Tier 1 modifier can roll. 81 unlocks most (e.g. +5 to Lightning Spell Skills on
wands gates at 81+); 80 is marginal. Sources disagree at the edges — some slot
guides cite 78–79 as the floor for helmets/boots, and certain armour mods are
cited as gating at 85+. Treated as a graded score rather than a single cliff,
because the true gate is per-mod, not global.

**Attribute type gates premium mods.** A base's attribute class decides which
top mods can appear on it:

| Base attribute | Unlocks |
|---|---|
| Strength | `#% of Armour also applies to Elemental Damage` |
| Dexterity | `Gain Deflection Rating equal to #% of Evasion Rating` |
| Intelligence (body) | faster start of Energy Shield Recharge |

So a perfect ilvl 82 base of the wrong attribute type cannot roll the mod that
would make it worth buying. The scorer encodes this rather than treating all
ilvl 82 bases alike.

**Runeforging (new in 0.5)** produces a parallel base for **427 item types** —
`Runeforged <base>` is a distinct item from its plain twin, and worth tracking
separately.

**Named bases from the guides** (all 15 verified against the live item table):
Sinister and Skullcrusher Quarterstaff; Winged, Soaring, Akoyan, Seaglass and
Spiked Spear; Omen Sceptre; Primed Quiver; Pearlescent Amulet; Ancestral,
Kamasan and Sorcerous Tiara; Time-Lost Diamond. Guides explicitly warn off
**Dreaming Quarterstaff** — higher base damage but 0% base crit — so it is
recorded as an `avoid_base`.

A useful sanity result: four names the guides mentioned (Nazir's Judgement,
Splinterheart, Slivertongue, Mist Whisper) did **not** resolve as bases,
correctly — they are uniques, and the resolver filters on entries without a
`name` field. They are tracked in the unique allowlist below instead.

**Two rune families, not one.** 0.5 produces `Runeforged` (428 bases, the
crafting-target variants) *and* `Runemastered` (218 bases), and the latter is
what **214 of the game's 464 uniques** are built on. Both are tracked.

## Uniques the meta actually uses

The guides named uniques constantly, and those names are the shortlist worth
pricing precisely. 38 verified against the live item table, enriched from the
poe2scout index, in `src/sox/data/unique_allowlist.toml`.

**The index price is a floor, not a price.** poe2scout reports one number per
unique, but many uniques roll across wide ranges, so that number reflects the
common (bad) copy:

| Unique | Index price | Spread | Listings |
|---|---|---|---|
| Ventor's Gamble | 7 ex | ×2.5 | 26,747 |
| Mageblood | 135,417 ex | ×3.0 | 5,808 |
| Headhunter | 29,279 ex | ×3.0 | 1,096 |
| Temporalis | 1,428,643 ex | ×6.0 | 41 |
| Thunderfist | 3 ex | ×111 | 10,179 |

Ventor's Gamble is the case that motivated this: 7 exalted is what a floor copy
fetches, while a good one sells for many Divine. Any tool that reports 7 ex for
your copy is simply wrong.

**Spread alone must not trigger a search.** Thunderfist spreads ×111 and is
worth ~3 exalted — wide ranges on a worthless item stay worthless. Of 449
priced uniques, **219 (49%) have spread ≥ 2.0**, so a spread-only rule would
blow the entire search budget on junk. Escalation therefore requires:

```
corrupted
  OR index_price >= 5000 ex                       (chase uniques)
  OR (spread >= 2.0 AND our roll >= 75th pct)     (the Ventor's case)
```

Everything else takes the index price directly, at zero API cost.

Liquidity is recorded too: Temporalis has 41 listings against Mageblood's
5,808, so its index number is far less trustworthy — thin markets get flagged
rather than reported as fact.

Base scoring lives in `src/sox/data/base_allowlist.toml`:

```
base_score = ilvl_weight + slot_weight + named_base_weight + runeforged_bonus
search if base_score >= 4
```

19 slot categories are tracked, all verified against
`type_filters.category`. One trap caught: quarterstaves are **`weapon.warstaff`**
in the trade taxonomy, not `weapon.quarterstaff`.

## Gems are a value class of their own

Chasing down what `Uul-Netol's Embrace` actually is (the Titan guide cites it
for "Fully Broken Armour") turned up an item class the first pass missed
entirely. It is a **Lineage Support Gem** — a breach-boss drop granting 40% of
Physical as Extra Chaos and Break Armour equal to 20% of Chaos dealt — and it
is the most expensive one in the game:

| Lineage Support Gem | Index price | Listings |
|---|---|---|
| Uul-Netol's Embrace | 150,826 ex | 3 |
| Garukhan's Resolve | 130,002 ex | 11 |
| Seraph's Heart | 97,983 ex | 8 |
| Atziri's Communion | 96,651 ex | 22 |

That top entry prices **above Mageblood** (135,417 ex). Sibling lineage gems
sit in the same `gem` group: Tul's Avalanche, Tul's Stillness, Esh's Prowess,
Esh's Radiance, Hand of Chayula.

**Uncut gems price by level**, which is easy to get wrong:

| Item | Index price |
|---|---|
| Uncut Skill Gem (Level 20) | 1,595 ex |
| Uncut Spirit Gem (Level 20) | 1,591 ex |
| Uncut Spirit Gem (Level 4) | 200 ex |

An 8× spread across levels of the same item means **gem level belongs in the
index lookup key**, not as a display detail.

Both classes come from scout's `Currencies/ByCategory`
(`lineagesupportgems`, `uncutgems`), so they cost zero trade API calls — but
only if `classify.py` recognises gems as their own class rather than folding
them into currency. The original classification (`currency | unique | rare |
magic | normal`) had no gem class and would have left six-figure items
unpriced.

## Scoring rule

Community pricing guidance is consistent that a single T1 mod rarely makes an
item valuable — it takes **three or more potent affixes useful to the same
build**. So candidate selection scores rather than pattern-matches:

```
score = sum(weight of each allowlist mod present on the item)
search if   score >= 6
       or  (score >= 4 and ilvl >= 80)
```

Two weight-3 mods, or three weight-2s, clears the bar. A pile of weight-1
resistances does not, which is the intended behaviour — that is a vendor item.

**Coherence matters too:** mods concentrated in one category (e.g. three
minion mods) indicate a real buyer, while three unrelated mods usually do not.
Implementation applies a coherence bonus when the top category holds most of
the score.

## Known gaps

- Blood Mage and Chronomancer gear detail is thin (403s and video-only guides).
  Both are B-tier; their needs fall inside the shared spell/life/ES core, so the
  allowlist is unlikely to miss value. Worth revisiting if either rises.
- Weights are seeded from guide *priority ordering*, not from realised sale
  prices. Once snapshots accumulate, weights should be re-tuned against what
  actually sold.
- The list is league-scoped. A new patch — particularly another defence-layer
  rebalance like the 0.5 ES nerfs — invalidates the weights, not the mechanism.
  Re-run `scripts/resolve_allowlist.py` after any patch.

## Sources

- [Maxroll PoE2 build guides](https://maxroll.gg/poe2/build-guides) — per-build gear/stat priority sections
- [PoE2Hub tier list](https://poe2hub.net/builds/tier-list/) — 22-ascendancy tier map
- [Mobalytics PoE2 builds](https://mobalytics.gg/poe-2/builds) — Martial Artist / Chronomancer variants
- [poe.ninja API docs](https://poe.ninja/docs/api) — confirms builds API is closed
- [IGGM 0.5 best gear affixes](https://www.iggm.com/news/poe-2-patch-0-5-0-best-gear-affixes-guide-top-mods-for-every-equipment-slot) — per-slot affix guidance
- [expcarry rare pricing guide](https://expcarry.com/poe-2-trading-price-guide) — the "3+ potent affixes" pricing rule
- [Steam: spotting high-value items](https://steamcommunity.com/sharedfiles/filedetails/?id=3415604016) — Deflection / Spirit Reservation Efficiency valuation
- [Maxroll: how to craft in PoE2](https://maxroll.gg/poe2/resources/how-to-craft-in-path-of-exile-2) — ilvl 82 crafting target
- [Mobalytics SSF crafting fundamentals](https://mobalytics.gg/poe-2/guides/ssf-crafting) — base selection and ilvl breakpoints
- [MMOExp ES helmet crafting](https://www.mmoexp.com/News/path-of-exile-2-currency-guide-how-to-craft-a-600-energy-shield-helmet-for-profit-in-poe-2.html) — Ancestral vs Kamasan Tiara pricing
- `https://www.pathofexile.com/api/trade2/data/items` — authoritative base names
- `https://www.pathofexile.com/api/trade2/data/stats` and `/data/filters` — authoritative stat ids and filter ids
