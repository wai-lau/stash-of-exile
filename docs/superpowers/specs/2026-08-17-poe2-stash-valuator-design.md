# PoE2 Stash Valuator (`sox`) — Design

**Date:** 2026-08-17
**Status:** Approved, pending implementation plan

## Purpose

Price every item in my PoE2 stash and tell me what the stash is worth,
which tabs hold the value, and which items are worth listing. Local CLI,
personal use, single account.

## Verified constraints

All of the following were confirmed by live calls against the real
services on 2026-08-17, not assumed.

### The official API cannot read PoE2 stashes

The [GGG developer reference](https://www.pathofexile.com/developer/docs/reference)
lists `GET /stash[/<realm>]/<league>` (scope `account:stashes`) as **PoE1
only**. Realm support is PC / Xbox / Sony; PoE2 is not among them. Same
for `/public-stash-tabs` and `/league-account`. Only `/character`
supports the poe2 realm.

**Consequence:** the OAuth route is closed for this project. Stash reads
go through the legacy session endpoint (`character-window/get-stash-items`
with realm `poe2`) authenticated by a `POESESSID` cookie — the same route
the existing PoE2 tools use. This is an unofficial endpoint and may break
without notice; the design isolates that risk in one module.

### Trade API (unofficial, Cloudflare-fronted)

- `POST /api/trade2/search/poe2/<league>` → `{id, result: [hashes]}`
- `GET  /api/trade2/fetch/<up to 10 hashes, csv>?query=<id>` → listings
- `GET  /api/trade2/data/filters` → filter metadata (no auth required)
- `GET  /api/trade2/data/stats` → stat id tables (no auth required)

Rate limits are advertised only on live responses via `X-Rate-Limit-*`
headers; there is no endpoint that reports them up front. The client must
learn them from the first response.

Confirmed filter groups and ids:

| Group | Ids that matter here |
|---|---|
| `type_filters` | `category` (64 ids: `armour.chest`, `weapon.crossbow`, `accessory.ring`, …), `rarity` (`normal`, `magic`, `rare`, `unique`, `nonunique`, …), `ilvl`, `quality` |
| `equipment_filters` | `ar`, `ev`, `es`, `ward`, `spirit`, `block`, `rune_sockets`, `damage`, `aps`, `crit`, `dps`, `pdps`, `edps` |
| `misc_filters` | `corrupted`, `identified`, `mirrored`, `stack_size`, `gem_level`, … |
| `status_filters` | `status`: `available`, `securable`, `onlineleague`, `online`, `any` |

Stat tables: `pseudo` 36 entries, `explicit` 3097, `implicit` 182,
`rune` 569, plus fractured / crafted / enchant / desecrated / sanctum /
skill.

### Price index — poe2scout (no auth, no key)

Their published Swagger is misconfigured (it points at the Swagger
petstore demo), so endpoints were read from the repo source and then
confirmed live:

```
GET https://api.poe2scout.com/poe2/Leagues
GET https://api.poe2scout.com/poe2/Leagues/{league}/Items/Categories
GET https://api.poe2scout.com/poe2/Leagues/{league}/Currencies/ByCategory?category=…
GET https://api.poe2scout.com/poe2/Leagues/{league}/Uniques/ByCategory?category=…
```

`category` is a **required** query param. Unique categories: `accessory`,
`armour`, `flask`, `jewel`, `map`, `weapon`, `sanctum`. Currency
categories: `currency`, `fragments`, `runes`, `essences`, `ultimatum`,
`expedition`, `ritual`, `vaultkeys`, `breach`, `abyss`, `uncutgems`,
`lineagesupportgems`, `delirium`, `incursion`, `idol`, `verisium`, `vaal`.

Current league at time of writing: **Runes of Aldur** (`runes`), base
currency Exalted Orb, 1 Divine = 336.5 Exalted. League is resolved at
runtime via `IsCurrent`, never hardcoded.

Unique entries carry mod **roll ranges** and liquidity, which the
valuation design depends on:

```json
{
  "Name": "Mageblood", "Type": "Utility Belt",
  "ItemMetadata": { "explicit_mods": ["+(17-23)% to Chaos Resistance", "..."] },
  "CurrentPrice": 1427363.6, "CurrentQuantity": 5, "IsChanceable": false
}
```

Their README asks for a `User-Agent` with contact info for sustained use.
We comply.

## Architecture

Python 3.12, `uv`. Package `sox`.

```
sox/
  config.py            league override, account, tab selection, filter rules, budgets
                       (TOML at ~/.config/sox/config.toml)
  secrets.py           POESESSID resolution; redaction helpers
  ggg/
    session.py         HTTP client + rate governor. ALL GGG traffic passes here.
    stash.py           legacy character-window endpoints: tab list, tab items
    trade.py           trade2 search / fetch / data.filters / data.stats
  scout.py             poe2scout index client
  valuation/
    classify.py        item -> currency | gem | unique | jewel | gear | endgame | unknown
    index_pricer.py    item -> scout index entry -> price
    rolls.py           parse "(min-max)" ranges; score an item's actual rolls
    candidates.py      selects which items earn a live trade search
    mods.py            mod text -> trade2 stat id (pseudo-preferred)
    query.py           build a trade2 query + the relaxation ladder
    trade_pricer.py    search -> fetch -> aggregate -> priced result
  report.py            ranked table + JSON snapshot
  cli.py               sox tabs | sox value | sox diff
```

**Load-bearing boundary:** nothing above `ggg/session.py` knows about
rate limits, and nothing can reach GGG without going through it.

## Pricing strategy

Strategy is chosen **per item**, not per rarity. Most items never cost an
API call; the search budget is spent only where it changes the answer.

### Bulk (no API cost)

Currency, fragments, runes, essences, **gems**, and the long tail of
uniques are priced from the cached scout index. A handful of cached calls
covers the entire stash.

Gems are their own value class and must not be lumped into "currency":

- **Lineage Support Gems** (`lineagesupportgems`, 75 items) are breach-boss
  drops and include some of the most valuable items in the game —
  Uul-Netol's Embrace indexes at **150,826 ex**, above Mageblood. These
  are frequently sitting in a stash tab unrecognised.
- **Uncut gems** (`uncutgems`, 42 items) are priced *by level*: an Uncut
  Skill Gem (Level 20) is ~1,595 ex while a Level 4 is ~200 ex. Gem level
  is therefore part of the index lookup key, not a detail.

Both come from `Currencies/ByCategory`, so they cost no trade API calls.

### Endgame items — no index exists, search or flag

A coverage audit (`docs/research/2026-08-17-coverage-audit.md`) found five
tradeable classes with **zero index coverage**. They are moddable or
tiered, so they are priced by search, never by index:

| Class | Search as | Key filters |
|---|---|---|
| Waystones (T1–T16) | `map.waystone` | `map_tier`, `map_packsize`, `map_iir`, `map_bonus` |
| Tablets (8 kinds) | `map.tablet` | mods |
| Skill/support gems (784 unindexed) | `gem.activegem` / `gem.supportgem` / `gem.metagem` | `gem_level`, `quality` |
| Sanctum relics (non-unique) | `sanctum.relic` | mods |
| Charms (non-unique) | `flask.charm` | mods |

**Wombgift** (5 entries) has neither an index price nor a clean category
mapping. It is reported as `unpriced:unknown-class` rather than guessed at
or silently zeroed.

Without this, a stash holding high-tier waystones or level-20 gems would
be valued at zero for those items — an error that under-reports, which is
the direction least likely to be noticed.

`card` appears among the 64 trade categories but has no items in PoE2 —
vestigial, no handling needed.

### Jewels — the index is actively wrong here

Jewels are the hardest class to price and the one the index handles worst.
They have no defences and no meaningful base value, so **their entire
worth is the mod combination** — and for the notable-granting jewels, the
index does not describe them at all:

| Unique jewel | Index price | Listings | Mods in index metadata |
|---|---|---|---|
| Megalomaniac | **1 ex** | 24,992 | **0** |
| Heart of the Well | **1 ex** | 30,000 | **0** |
| Prism of Belief | 1 ex | 22,978 | 1 |
| Voices | 349,123 ex | 146 | 1 |

A Megalomaniac's value is which notables it allocates; a good one is worth
divines and a bad one is vendor trash. The index reports one number for
all of them, and because its metadata carries no roll ranges the spread
metric returns 1.0, so the roll-based escalation rule never fires. Left
alone, the tool would price every Megalomaniac at 1 ex and never look.

Three mechanisms fix this.

**1. Notables are searchable, so search them.** 875 notables exist as exact
stat ids of the form `explicit.stat_2954116742|<notable id>` (e.g.
`Allocates Barbaric Strength`). Any item carrying an `Allocates …` mod is
**always** escalated, and the query is built from those exact ids — which
is how the trade site prices these. Index price and spread are ignored for
these items; both are meaningless.

**2. A general rule for "the index cannot describe this".** If a unique's
index metadata carries no mods but our copy has mods, the index is not
describing our item and the price is not evidence about it. Escalate.
This catches Megalomaniac and Heart of the Well structurally, without a
special-case list.

**3. Rare jewels get a mod-only search.** No defence filters, no ilvl
minimum — `category: jewel` plus stat minimums. Jewel-staple mods that the
gear-centric allowlist lacked are now included: `#% increased Damage`,
`#% increased Attack Damage`, `#% increased Elemental/Chaos Damage`, and
the weapon-scoped family (`Damage with Bows` / `Bow Skills` / `Crossbows` /
`Quarterstaves` / `Spears` / `Maces`). The allowlist is 103 mods.

Jewels get their **own search budget** (default 15) rather than competing
in the `rares` bucket, since a stash typically holds many of them and they
are cheap to search.

### Uniques — three tiers, assigned automatically

Index price alone is wrong for uniques whose value depends on rolls or
corruption: a well-rolled Ventor's Gamble is worth many Divine while a
bad one is worth ~20 Exalted. No curated chase-list is needed, because
the index ships the ranges.

1. Parse `ItemMetadata` mods into per-mod `(min, max)` ranges.
2. Score our copy: each actual value → percentile within its range →
   aggregate **roll score**.
3. Escalate to a live trade search if **any** of:
   - the item carries an `Allocates …` mod (notable-granting jewel)
   - the index metadata has no mods but our copy does — the index is not
     describing our item, so its price is not evidence about it
   - `corrupted: true`
   - `CurrentPrice` ≥ 5000 ex (chase uniques)
   - spread ≥ 2.0 **and** our roll ≥ 75th percentile **and**
     `CurrentPrice` ≥ 50 ex (the Ventor's case)
4. Otherwise take `CurrentPrice` from the index. No call.

Spread on its own is deliberately **not** enough. Measured against live
data, 219 of 449 priced uniques (49%) spread ≥ 2.0, and Thunderfist
spreads ×111 (`(1-111)% increased Evasion and Energy Shield`) while
selling for ~3 ex — a wide range on a worthless item stays worthless, and
a *perfect* copy of it is still worth ~3 ex. Requiring a good roll on our
copy is not sufficient either: a well-rolled Thunderfist satisfies both
the spread and roll clauses. The **price floor** is what stops the swing
rule spending a search slot on it.

**Zero-floor ranges count as maximally swingy.** Ventor's Gamble rolls
`+(0-80) to maximum Life` and `+(0-20) to Spirit` — the difference between
a copy with the mod and a copy effectively without it. The ratio is
undefined, so these score a fixed high value rather than being skipped.
Skipping them (an earlier bug) left the metric blind to the exact case it
exists to catch: Ventor's scored 2.5 off its weakest mod instead of 10.

`src/sox/data/unique_allowlist.toml` carries the 38 uniques 0.5 build
guides name outright (verified against the live item table, enriched with
index price / listing quantity / spread). They get priority within the
unique search budget. Listing quantity is retained as a liquidity signal:
Temporalis has 41 listings against Mageblood's 5,808, so its index number
is far weaker evidence and is reported as such rather than as fact.

Escalated uniques search by name + base + the roll constraints that
actually drive price, so a high-roll copy and a floor copy get different
answers.

### Rares, magic, and normal bases — "cheapest thing at least as good"

We cannot assume a similar item exists on trade, and we are not trying to
find *our* item. We search for **items at least as good as ours on the
axes that matter**, with open-ended minimums:

- **item type**, never item name — `type_filters.category`
- **defences** we care about — `es` / `ar` / `ev` at our item's value,
  `min` only, no `max`
- **only the sought-after mods** — each at our item's value, `min` only

```jsonc
{ "query": {
    "status": { "option": "online" },
    "filters": {
      "type_filters":      { "filters": { "category": {"option": "armour.chest"},
                                          "rarity":   {"option": "nonunique"},
                                          "ilvl":     {"min": 82} } },
      "equipment_filters": { "filters": { "es": {"min": 412}, "ar": {"min": 180} } }
    },
    "stats": [ { "type": "and", "filters": [
      { "id": "pseudo.pseudo_total_elemental_resistance", "value": {"min": 78} },
      { "id": "explicit.stat_3299347043",                 "value": {"min": 96} }
    ]}]
} }
```

**Interpreting the result.** Every listing returned is ≥ ours on every
constrained axis. The cheapest one is therefore a **ceiling** on our ask
— a strictly better item is already listed at that price. The report says
`ceiling`, with a suggested ask below it. It does not pretend to be a
comparable-sales price.

Normal and magic items are first class, not junk. White and magic bases
sell on base type + item level (+ quality, + rune sockets), which needs
**no mod→stat mapping at all** — these are the cheapest and most reliable
searches in the tool. Magic items add their mods only when those mods
clear the same allowlist rares use.

### Relaxation ladder

Zero results is information, not an error: it means nothing that good is
currently listed.

1. Full strictness → ≥5 results ⇒ done, tag `exact`
2. Too few → relax: drop the lowest-weight mod, or scale minimums to 90%
   then 75%. Up to 3 steps, each costing one search, tag `relaxed:N`
3. Still nothing → tag `unpriced:above-market` and flag for manual
   review. These are the items most worth looking at by hand.

### Mod mapping

`mods.py` normalizes a stash mod's numbers to `#` and looks the template
up in the `explicit` table (3097 entries), preferring **pseudo** stats
where one exists (`pseudo.pseudo_total_elemental_resistance`,
`pseudo_total_life`, …) — the same aggregation the trade site uses.
Ambiguous match ⇒ skip that mod. The tool never guesses a stat id.

### Candidate selection

`candidates.py` is one selector across all rarities, with a **per-class
search budget** so bases cannot starve rares. Defaults (configurable):
20 rares, 15 bases, 15 jewels, 10 uniques per run.

**Crafting bases** are scored from `src/sox/data/base_allowlist.toml`
(generated by `scripts/resolve_base_allowlist.py`, category ids and base
names verified against the live item/filter tables). Item level is the
primary axis, because it decides which mod tiers a buyer can craft:

```
base_score = ilvl_weight + slot_weight + named_base_weight + runeforged_bonus
search if base_score >= 4
```

| Axis | Values |
|---|---|
| ilvl | 82 → 3 (all T1 mods roll), 81 → 2, 80 → 1, below → 0 |
| slot | 19 tracked categories, 2–3 by defence budget |
| named base | 14 bases guides call out by name (+1/+2) |
| rune family | +2 — `Runeforged` (428 bases) and `Runemastered` (218 bases, carrying 214 of 464 uniques); both are distinct items from their plain twins |

An `avoid_base` list carries bases guides warn against (e.g. Dreaming
Quarterstaff — higher base damage, 0% base crit), so budget is not spent
on them.

`attribute_rule` records that an armour base's attribute type gates its
premium mods — Str unlocks *% of Armour also applies to Elemental
Damage*, Dex unlocks *Deflection from Evasion*, Int body armour unlocks
*faster start of ES Recharge*. A high-ilvl base of the wrong attribute
type cannot roll the mod that would make it valuable.

Base searches need **no mod mapping at all** — category + ilvl + rarity +
defence minimums is an exact query — so they are the cheapest and most
reliable searches in the tool.

Rares qualify by **score**, not by pattern match. The default mod
allowlist ships at `src/sox/data/mod_allowlist.toml` — 92 mods in 13
categories, each with a verified trade2 stat id and a weight (3 =
build-defining, 2 = strong, 1 = supporting). It is generated by
`scripts/resolve_allowlist.py` from the live stats table; see
`docs/research/2026-08-17-meta-mod-research.md` for how the weights were
derived from the current meta.

```
score = sum(weight of each allowlist mod present on the item)
      + coherence_bonus
      + open_affix_bonus
search if   score >= 6
       or  (score >= 4 and ilvl >= 80)
```

This follows the community pricing rule that a lone T1 mod rarely sells —
value comes from three or more potent affixes serving the same build. A
pile of weight-1 resistances therefore does not qualify, by design;
weight-1 mods contribute at most 2 in total, because an item with four or
more low-tier mods is worth *less* — they occupy affix slots a buyer would
otherwise craft into.

### Coherence — many mods serving one archetype

Every allowlist mod is tagged with the archetypes it serves (`attack`,
`spell`, `minion`, `projectile`, `melee`, `crit`, `life`, `es`, `armour`,
`evasion`, `defence`, `spirit`, `elemental`, `chaos`, `physical`, …).
The allowlist is **405 mods**, built from a hand-curated core plus whole
families expanded by pattern against the live stats table (250 skill-level
variants alone), so coverage keeps up when GGG adds to a family.
Coherence counts how many of an item's mods share one archetype:

```
top = size of the largest archetype group among the item's matched mods
coherence_bonus = min(top - 1, 3)
```

Tags, not allowlist categories. The categories cannot express this: a real
build's mods **span** categories, while one category can hold mods for two
unrelated builds. Measured against real mod sets, the category proxy fired
on exactly the wrong items:

| Item | Category proxy | Archetype count |
|---|---|---|
| `+3 Melee Skills` + `+3 Spell Skills` | coherent ✗ | attack×1, spell×1 → **+0** ✓ |
| proj levels + attack speed + flat phys | not coherent ✗ | attack×2 → **+1** ✓ |
| spell levels + cast speed + spell damage | not coherent ✗ | spell×3 → **+2** ✓ |
| 5-mod caster item | — | spell×5 → **+3** ✓ |

Tagging is conservative: a mod that could serve either delivery gets no
delivery tag rather than a guessed one.

**Tags are not stripped — mods record a `subject` instead.** *"Minions have
increased Attack Speed"* genuinely *is* an attack-speed mod; it just belongs
to the minion. Each mod therefore keeps its full tags and records whose stat
it modifies (`self`, `minion`, `companion`, `totem`). Coherence groups
non-self subjects by subject, so a minion attack mod never stacks with the
player's own attack mods, and no information is thrown away.

**Minion buyers are not interchangeable**, so subject alone is too coarse.
Verified against 0.5 guides:

- Companion Spirit Walker wants `+Level of all Minion Skills` and `Allies in
  your Presence deal increased Damage`, but scales mainly off the **player's
  main-hand weapon** through Catha's Balance.
- Spectre/Reaver builds prefer flat added physical on the sceptre over a
  high-rolled *"Allies have #% increased Damage"*.
- Attack minions (snipers, reavers, companions) and caster minions (skeleton
  mages) share no attack-speed / cast-speed mods.

So each minion mod is **universal** (every minion build wants it — skill
levels, minion damage, minion life) or bound to a **subtype** (`attack`,
`caster`, `companion`). Universal mods count toward every subtype; subtype
mods count only toward their own. Measured:

| Jewel | Coherence |
|---|---|
| 4 universal minion mods | `minion×4` → **+3** |
| 2 companion + 2 universal | `minion:companion×4` → **+3** |
| 2 attack-minion + 2 caster-minion | `minion:attack×2` → **+1** |
| 2 attack-minion + 2 universal | `minion:attack×4` → **+3** |

A jewel split across attack and caster minions scores below a focused one,
which is the correct answer and what plain subject grouping got wrong.

No conflict penalty is applied. An item carrying both `+Melee Skills` and
`+Spell Skills` simply earns no coherence bonus rather than being marked
down.

### Open affix space

Remaining affix space is part of what a buyer pays for: an item with two
strong mods and four open slots is a **crafting base**, while the same two
mods surrounded by four junk mods is finished goods. Affix capacity is 6
for rare (3 prefix + 3 suffix), 2 for magic, 0 for normal.

```
open = capacity(rarity) - distinct mods present
open_affix_bonus = min(open, 3)  if the item carries a weight-3 mod
                 = min(open, 1)  if score >= 4
                 = 0             otherwise
```

Gating on an existing premium mod is deliberate: a blank rare with one
junk mod also has open slots, and is not worth a search. What sells is a
**locked-in high-tier mod with room left to craft**, which is also why a
`fractured` mod earns the full bonus — fracturing is what guarantees the
good mod survives further crafting.

**Corrupted and mirrored items score no open-affix bonus at all.** Neither
can be modified further, so their empty slots are permanently empty. This
is not a tuning choice; treating a corrupted item as a craft base is
simply wrong, and it would systematically over-value the corrupted uniques
the tool already flags for other reasons.

Normal-rarity items are excluded from this bonus because their value is
*entirely* open affix space, which the base score (ilvl + base type +
rune family) already measures. Adding both would double-count.

Weights are league-scoped. Re-run the generator after any patch that
rebalances defence layers — the 0.5 Energy Shield nerfs are exactly the
kind of change that invalidates them.

Budget is counted in **searches, not items** — one item costs 1–4 calls
including relaxation steps and the fetch.

## Data flow (`sox value`)

1. Resolve league from scout `/poe2/Leagues` (`IsCurrent`); config may override
2. Load POESESSID → session
3. Fetch tab list; select tabs (all, or `--tab`)
4. Per tab: fetch items → `classify`
5. Bulk-price currency / fragments / uniques from the cached index — 0 GGG calls
6. `candidates.py` selects search-worthy items per class, within budget
7. Per candidate: mods → stat ids → build query → search → fetch → aggregate,
   applying the relaxation ladder as needed
8. Aggregate → ranked table + JSON snapshot at
   `~/.local/share/sox/snapshots/<timestamp>.json`

Values reported in Exalted **and** Divine (ratio from the index).

Cost per cold run: ~4 scout calls + 1 tab list + N tab fetches +
≤ budgeted searches × ~2. Bounded and predictable. Warm runs are far
cheaper — cache hits cost nothing.

## Rate governor

Parse `X-Rate-Limit-Rules`, then for each rule `X-Rate-Limit-<Rule>`
(`limit:period:restriction`) and `X-Rate-Limit-<Rule>-State`
(`current:period:restricted`). Maintain a token bucket per rule and sleep
*before* issuing a call that would breach one. On `429`, honor
`Retry-After` and halve concurrency. Because rules are only discoverable
from a live response, the session's first call is deliberately a cheap
one, made to learn the limits.

## Cache

SQLite at `~/.local/share/sox/cache.sqlite`.

| Table | Contents | TTL |
|---|---|---|
| `filters_data`, `stats_data` | trade2 metadata | 7d |
| `index_price` | scout entries | 6h |
| `trade_price` | keyed by canonical query hash | 12h |

## Secret handling

`POESESSID` is a full account session token: whoever holds it is logged
in as the account owner, without a password. Therefore:

- Read from env `POESESSID`, else `~/.secrets`. Never from a repo file.
- Held in memory only. Sent only to `www.pathofexile.com`.
- Redacted from every log line, exception message, and snapshot.
- Never written to the cache DB or to any snapshot.
- `sox` refuses to run if the file it was read from is group- or
  world-readable.

## Error handling

| Condition | Behavior |
|---|---|
| Expired session (302 to login) | Clear message, stop. No retry loop. |
| Cloudflare 403 | Stop; instruct to refresh the cookie. |
| 429 | Honor `Retry-After`, halve concurrency, resume. |
| Unknown mod text | Skip that mod, record it in the report. |
| Scout unreachable | Price uniques from last cache, mark results stale. |
| Legacy stash endpoint changed shape | Fail loudly in `ggg/stash.py` only. |
| Special tab shape (currency/map/gem tabs) | Parse explicitly. A tab that reads as empty must raise, never silently contribute zero. |
| Item class with no price path | Report `unpriced:unknown-class`; never value at zero. |

## Testing

- Recorded fixtures of stash JSON and trade responses, POESESSID scrubbed.
  No live calls in the default suite.
- Rate governor unit-tested against synthetic headers, including a 429 storm.
- Mod mapper tested on real mod text → expected stat id, including the
  ambiguous cases it must refuse.
- Roll scorer tested against known `(min-max)` ranges.
- One opt-in integration test hits poe2scout only — no auth, no secrets.

## Surviving patches — how the data files stay correct

The allowlists are generated from GGG's live tables, so the risk is that a
patch reworded a mod and entries silently vanish. Three layers, in order:

1. **Locked ids.** Every entry carries a `slug` derived from *our* canonical
   text, plus the resolved `ids`. On regeneration, an id that still exists
   in the table is reused as-is. GGG can reword a mod freely — the lock
   holds. This is the layer that matters most, because stat ids are the
   stable identifier and text is the volatile one.
2. **Normalized matching.** When there is no lock, matching folds case,
   whitespace, and a leading `+`. Measured against the live table this
   introduces **zero** new ambiguity, so the capitalization traps
   (`Physical Damage to Attacks` vs `Fire damage to Attacks`) can no longer
   break a build.
3. **Loud failure.** If neither works, the generator exits non-zero and
   names the entry. A mod that genuinely no longer exists stops the build
   rather than disappearing from the allowlist.

**Ambiguity is preserved, never resolved by guessing.** Several stat ids can
share one mod text — `# to Spirit`, `#% increased Spirit` and
`# to maximum Runic Ward` each have two. Those entries carry all ids and
`ambiguous = true`, and the query emits an OR group across them.

**No cross-group fallback.** The same text also exists under `fractured`,
`crafted` and `desecrated`, but those match only items carrying the mod *as*
fractured/crafted/desecrated. Substituting one would skew every search built
from it, so resolution is restricted to `pseudo` and `explicit`.

`tests/test_resolver_drift.py` proves each layer by mutating a recorded copy
of the live table the way a patch would — re-casing, adding a `+`, rewording
entirely, and removing a mod outright — then asserting the allowlist still
resolves, or fails loudly when it genuinely cannot. Fixtures are recorded and
gzipped under `tests/fixtures/`; the suite makes no network calls.

## Value-affecting item flags

These change price and must be read from the stash JSON, not ignored:
`corrupted`, `mirrored`, `fractured`, `desecrated`, quality, and socketed
runes / soul cores. **Mirrored items cannot be modified further**, so they
must never be priced as an equivalent normal item. Each has a
`misc_filters` equivalent when a search is needed.

Currency value is **per-unit × stack size** — the report multiplies rather
than counting a stack of 500 as one item.

## Out of scope

Live overlay, in-game integration, automatic listing/pricing of items,
multi-account support, PoE1 support, any web UI. `sox diff` compares two
snapshots; anything richer waits for a later iteration.

**Character equipment and inventory** are also out of scope for v1 — gear
worn by characters is not in stash tabs. Worth noting that `/character` is
one of the few official OAuth endpoints that *does* support the poe2
realm, so this is a cheap addition later.
