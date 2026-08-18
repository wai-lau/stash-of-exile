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
    classify.py        item -> currency | gem | unique | rare | magic | normal
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

### Uniques — three tiers, assigned automatically

Index price alone is wrong for uniques whose value depends on rolls or
corruption: a well-rolled Ventor's Gamble is worth many Divine while a
bad one is worth ~20 Exalted. No curated chase-list is needed, because
the index ships the ranges.

1. Parse `ItemMetadata` mods into per-mod `(min, max)` ranges.
2. Score our copy: each actual value → percentile within its range →
   aggregate **roll score**.
3. Escalate to a live trade search if **any** of:
   - `corrupted: true`
   - `CurrentPrice` ≥ 5000 ex (chase uniques)
   - spread ≥ 2.0 **and** our roll ≥ 75th percentile (the Ventor's case)
4. Otherwise take `CurrentPrice` from the index. No call.

Spread on its own is deliberately **not** enough. Measured against live
data, 219 of 449 priced uniques (49%) spread ≥ 2.0, and Thunderfist
spreads ×111 while selling for ~3 ex — a wide range on a worthless item
stays worthless. Requiring a good roll on *our* copy is what keeps the
budget on items where the index is actually wrong about us.

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
20 rares, 15 bases, 10 uniques per run.

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
search if   score >= 6
       or  (score >= 4 and ilvl >= 80)
```

This follows the community pricing rule that a lone T1 mod rarely sells —
value comes from three or more potent affixes serving the same build. A
pile of weight-1 resistances therefore does not qualify, by design. A
**coherence bonus** applies when most of the score sits in one category,
since concentrated mods imply a real buyer.

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

## Testing

- Recorded fixtures of stash JSON and trade responses, POESESSID scrubbed.
  No live calls in the default suite.
- Rate governor unit-tested against synthetic headers, including a 429 storm.
- Mod mapper tested on real mod text → expected stat id, including the
  ambiguous cases it must refuse.
- Roll scorer tested against known `(min-max)` ranges.
- One opt-in integration test hits poe2scout only — no auth, no secrets.

## Out of scope

Live overlay, in-game integration, automatic listing/pricing of items,
multi-account support, PoE1 support, any web UI. `sox diff` compares two
snapshots; anything richer waits for a later iteration.
