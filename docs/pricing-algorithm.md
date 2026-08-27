# How sox Prices an Item

What happens between pressing `Ctrl+C` in game and a number appearing on
the second monitor — the routing, the query, the widening ladder, and the
places it is still wrong.

> **The search is not "find my item".**
> It is *find the cheapest item at least as good as mine*. Every
> constraint is a minimum at our own value and there are no maximums, so
> every listing returned is better than ours on every axis we asked
> about. The cheapest one is therefore a **ceiling** on what we can ask —
> not a comparable sale.

## The path an item takes

```mermaid
flowchart TD
    clip[clipboard text] --> parse["parse — mods · properties · rolls · flags"]
    parse --> class{item class}
    class -- "currency · gem" --> book["exchange book (both sides)"]
    class -- unique --> desc{"index can describe it?"}
    desc -- yes --> book
    desc -- escalated --> build[build query]
    book -- no book --> index["index price (poe2scout)"]
    class -- gear --> score[score the mods]
    score --> qual{"qualifies? or not indexed"}
    qual -- no --> junk["junk — no search"]
    qual -- yes --> build
    class -- "endgame · notable jewel" --> build
    build --> search[search trade2]
    search --> m{"matches ≥ 1?"}
    m -- yes --> fetch["fetch cheapest 10 (up to 30 deep)"]
    fetch --> drop["drop listings that clear our numbers only on runes"]
    drop --> stats["low · 25th · median"]
    stats --> s{"sample ≥ 8?"}
    s -- yes --> price([price])
    m -- no --> widen[widen one rung]
    s -- no --> widen
    widen --> build
```

Anything not sent to the trade search is offered to the bulk exchange
first, and falls back to the index only when there is no book for it. The
ladder is a loop: a rung that finds nothing, or finds too few listings to
mean anything, widens and searches again. The first rung asks for the
whole item; six rungs maximum, one API search each, and a rung that
repeats the one above it costs none.

## Stage by stage

### 01 — Parse the clipboard

The clipboard is the only input — **there is no PoE2 stash API**. The
parser reads mods by kind (prefix, suffix, implicit, fractured,
desecrated, rune, enchant, corruption enhancement), the property block,
requirements, and flags like `Corrupted` and `Twice Corrupted`.

With *Advanced Item Descriptions* on, each mod carries its roll and range
inline — `+24(20-30) to Dexterity`. That range drives roll quality, the
implicit floor, and the pseudo totals. A negative roll prints its range
unsigned — `-30(30-30)% to all Elemental Resistances` — and the sign is
restored on read: taken as written, the floor of a penalty sat above the
roll and was summed into a resistance total no copy could meet.

### 02 — Route: index or search

An index lookup is free; a search costs an API call against a shared rate
limit. Anything the index cannot describe gets searched — and anything
*not* searched is offered to the bulk exchange before the index is asked.

| Class | Route | Why |
|---|---|---|
| Currency, gems | exchange, then index | Fungible; one price describes every copy — and the exchange book is far deeper than the handful of listings the index counts. Stage 03. |
| Endgame (ultimatums, tablets, logbooks…) | always search | No index covers them at all. |
| Waystones | exchange; search from a loot score of 80 | The tier's bulk book prices the commodity; the tooltip totals become a loot score, and a stone worth juicing is searched on them. See stage 05. |
| Jewel allocating a notable | always search | Value is *which* notables; the index reports ~1 ex for all 25,000 Megalomaniacs. |
| Unique | index unless escalated | See below. |
| Rare / magic / normal gear | search if it scores | Rare needs score ≥ 6, or ≥ 4 at ilvl 80+. Everything else ≥ 4. Gear has no exchange book, so an item that does not qualify stays junk. |

A unique escalates from index to search when *any* holds:

- it allocates a notable, or is corrupted, or is absent from the index;
- the index has no mods for it but our copy does;
- the index price is ≥ 5,000 ex;
- it grants a skill — **no unique in the index records one**, so the
  level is invisible to its price;
- **any single roll** sits at the 75th percentile or above — on a unique
  the index prices at 3 ex or more, since at the listing floor a roll has
  nothing to multiply.

That last clause is the best roll, not the mean. A copy with one
near-perfect build-defining roll beside poor filler averages out to
mediocre, and the market does not price it that way.

### 03 — Price it in bulk, from both sides of the book

Currency never reaches the item search, so nothing cross-checks the index
but the exchange: Chaos Orb sat indexed at **17.6** against a market that
says 32.5. The exchange answers first — the index prices only what the
exchange cannot.

**The game's own fills come first.** poe2scout snapshots the in-game
Currency Exchange hourly, and a fill cannot be faked: an offer nobody
takes never appears there. Masterwork Rune's trade-site book was 748
one-unit "1 Exalted for 1" bait listings while 38,000 ex of the rune
actually changed hands near 260 — and every fill on that exchange is an
instant trade. Below 5,000 ex of turnover the figure is pair noise
(281 ex of one omen "traded at 36"), and the trade-site books answer
instead.

Those books are read from **sellers online in league** (`onlineleague`) —
measured against the alternatives. `any` is where bait lives: the omen
showed 8,417 listings at `any`, a wall of offline 1-ex ghosts over a
~6 ex online market. `securable` (instant buyout) empties the core books
outright — the divine ask book held zero offers there. An offer from a
seller who is present is one you can actually take.

Both ends of a book are junk. The ask book for one Divine Orb opens with
a few tiny trap offers at 1 ex — 59 units of the 18,520 in the book —
and the dearest offer, at 11,000 ex, is not a price either, so `min()`
is never used. Depth is what separates them: a stock-weighted tenth
percentile steps over the traps and lands at 400 ex, where the market's
real depth begins. Read against the bid at 301, the midpoint prints
360.5.

| Decision | Rule | Why |
|---|---|---|
| The statistic | `stock-weighted 10th percentile` | The ratio at which cumulative *stock* first reaches a tenth of the book. Counting listings would let a hundred one-unit dumps outvote a seller holding ten thousand. The 1-exalted divine offers are 0.3% of that book, so a tenth clears them and lands where the market is. |
| The price | `(ask + bid) / 2` | Neither side alone is a price: divine asked 420 and bid 301 while every other source said 358 — the midpoint said 360.5. The bid book is the same market read the other way round (sellers of exalted who want this item), so its units-per-exalted ratio inverts. |
| No bid side | `ask alone` | Most cheap currency has no buyers at all — 1,303 sellers of that omen and not one bid — and there the ask is the only evidence there is. |
| A crossed book | `decline, use the index` | If the bid comes back *above* the ask, one of the two sides is bait and the book does not say which. On Preserved Cranium every one of the 100 offers on the cheapest page read "1 Exalted for 1 Cranium", so the ask returned 1 against a real 500 ex bid and the midpoint printed 250 for an item the index prices at 3,449. Buy at the ask and sell at the bid forever — no market survives that, so the exchange declines rather than guessing. |
| A thin book | `read again in divine, broader answers` | Under 20 units an exalted book cannot support a price, and for a dear item that is because nobody trades it in exalted: Khatal's Rejuvenation held 9 units at 10 ex while the divine side said 908. The broader book — more sellers quoting the item in that currency — answers, never the fatter page: the divine side of every cheap item is a wall of lazy one-divine asks, and comparing any size to it handed a ~6 ex omen to the wrong book at 362. The gate is sound only because of what sits in front of it — fills catch the deep-bait class, online status keeps ghosts out of the depth it measures. Divine itself is exempt: against itself the query says nothing, exactly as exalted's does. |
| Sub-exalted prices | `bundles divide out` | One exalted for 40 Scrolls of Wisdom is 0.025 each. A bundle is how a price below one exalted is expressed at all. |
| The rate table | `divine, chaos repriced` | Prices and the rate that renders them must come from one book. Reading a Divine Orb off the exchange while converting it with the index's rate printed it as `1.12 div` — the same orb measured twice. |
| Coverage | `~780 ids, 14 groups` | Currency, runes, essences, omens, fragments, gems — and waystones, which the index does not price at all. Gear, jewels and uniques have no book, and that is the signal to fall back to the index. |

Every endpoint gets its **own rate governor**. Search answers
`trade-search-request-limit` at 5:10:60, fetch answers
`trade-fetch-request-limit` at 12:4:10, the exchange answers
`trade-exchange-request-limit` at 5:15:60 — and the `-state` counters
show they are separate budgets: a search's count does not move across
fetches. A governor holds one rule set and one request history, so one
shared between search and fetch charged every fetch to the search window
and gated each call by whichever endpoint answered last — half the
searches GGG allows, spent pacing the wrong thing. The session keeps a
governor per endpoint, keyed by the trade2 path segment.

The governor also **takes GGG's count over its own**. The limit is per
IP, and the `x-rate-limit-ip-state` header (`hits:period:restricted`
per clause) counts what a local history cannot see: the trade site open
in a browser, an overlay, a second sox, and the run before this one —
restart `sox watch` and the last five minutes of searches are still on
the clock, against a `30:300` clause whose penalty is 1,800 seconds.
Each response reconciles the history to the server's count — padding
hits it missed just outside the next shorter window, where the server
says they are not — and a restriction in progress is waited out rather
than earned twice. The watch status line prints what the tightest
clause has left. A lockout longer than a minute — the `30:300` clause
hands out 1,800 seconds — is never slept: the call is refused, nothing
queues behind it, and every copy is priced with what is left (the
index, the bulk book, a waystone's loot score) while the status line
counts the lockout down.
Static ids are cached for a week, books for six hours.

Books are league-scoped, and so is the index: sox reads the **softcore**
league by default and `--hardcore` reaches the other one. Two entries
report `IsCurrent` at once — a league and its hardcore twin — and the
index marks hardcore three ways only inconsistently, so all three are
checked rather than taking whichever is listed first. With `--no-trade`
there is no exchange either, and everything falls back to the index.

| Output row | Printed when |
|---|---|
| `exchange 32.5 ex (1,303 offers, 18,520 in stock)` | Always. Depth is the evidence here, not a listing count. |
| `bid 301 · ask 420` | Both sides exist. The spread is the honest width of the answer. |
| `ask only — nobody is bidding for these` | The book has offers but no bids. |
| `thin book — few units are actually offered` | Under 20 units in stock. |
| `the game's own exchange — 84,602 ex of it traded in the last snapshot` | The price rests on completed in-game trades, not listings — the one measurement bait cannot touch. |
| `the item search capped at 10,000 and reads a sample — this is the bulk book instead` | Past 10,000 matches the trade engine sorts only a kept sample — measured live, tier 15+ floored at 3 ex while its own subsets floored at 1 ex and at 1 transmute — so a capped search has described a commodity and its bulk book answers instead. A capped search with no book keeps the trade answer, branded `every search past the cap reads a sample`. |

### 04 — Score the mods

The score answers one question: *is this item worth an API call?* It
never becomes the price. Four things add to it, and a rare needs 6 before
anything is searched.

Two scored items, one bar unit per point:

- **spell wand · ilvl 82** — mods +6 (+3 spell skills · +2 spell damage
  · +1 mana · +1 more mana, capped, scores 0), coherence +2, open
  affixes +2, base +1 → **11 → search**
- **blank rare · ilvl 79** — four weight-1 mods; the cap allows two of
  them, and open affixes pay nothing below a score of 4 → **2 → junk, no
  search**

The threshold is the only place the score is used — above it the item
gets a query built, below it the tool spends nothing.

| Contribution | Range | Rule |
|---|---|---|
| Mods | `3 / 2 / 1 each` | Matched against an allowlist generated from build guides: 3 build-defining, 2 strong, 1 supporting. Weight-1 mods total **at most 2** — four low-tier mods make an item worth *less*, since they occupy affixes a buyer would craft into. Rune mods and unrevealed desecrated mods are not scored. The allowlist decides weight, not what can be searched: every other mod in GGG's trade stats table is searched at weight 0 — in the first rung, tagged by the same rules as the allowlist so it coheres with the weighted mods serving its buyer, and widened away by class first and weight second. Map and monster wordings are refused, as the generator refuses them: a waystone's mods are difficulty, searched through its totals, and as floors they would ask for a harder stone. Glyph Beads: two amulet mods the allowlist never named printed as `(unsearchable)`, and the first rung searched an item two mods short of the one in hand. |
| Coherence | `0 … 3` | Cluster size − 1, capped at 3. Needs two mods on one archetype; a group of one is not a cluster. |
| Open affixes | `0 … 3` | Room left to craft. Rare holds 6 affixes, magic 2, normal none. With a weight-3 mod present, up to 3 points; otherwise 1, and only once the mods already score 4. **Zero on corrupted or mirrored items** — their empty slots are permanently empty. An unrevealed mod still occupies its slot. |
| Base — rare | `0 or 1` | A single point when the base's own score reaches 4. On a rare the mods are the item; the base is a tiebreak. |
| Base — magic, normal | `added whole` | ilvl 82+ is 3, 81 is 2, 80 is 1; a named crafting base and a `Runeforged`/`Runemastered` prefix add 2 each; a base the guides say to skip loses 3. On an unmodded item the base *is* the value. |

| Rarity | Searched when | Why that number |
|---|---|---|
| Rare | `score ≥ 6, or ≥ 4 at ilvl 80+` | Two strong mods and a cluster, or a high-ilvl base worth crafting on. |
| Magic, normal | `score ≥ 4` | These price as bases, and the base score carries in full. |
| Unique, notable jewel, endgame | `not scored` | Routed in stage 02 — the score never enters it. |

#### Coherence: does one buyer want the whole item?

Coherence is separate from the score — it is the clustering of mods onto
one buyer archetype, and it decides *which stats get searched* in the
next stage. The item's own defence type votes: an Energy Shield chest is
an ES item before a mod is read. Generic value never elects the buyer:
a resistance mod's "elemental" tag once let three res rolls crown an
elemental buyer on a minion ring — widening then dropped the minion mod
its 5-49 div comparables shared — so a defence-tagged mod votes only
within the defence family, and the umbrella groups every build pays into
(defence, resistance, life) are not electable at all.

| Archetype | Votes | |
|---|---|---|
| energy-shield | 3 | dominant, +2 |
| spell | 1 | not a cluster |
| resistance | 1 | not a cluster |

Two ES mods plus the chest's own vote. Counts tie constantly — a hybrid
weapon with two elemental mods and two physical ones is 2–2 — so ties
break on **summed weight**: two build-defining mods describe a buyer
better than one build-defining and one supporting. If the weights tie
too, the item genuinely serves both and claiming either is worse than
reporting no cluster.

### 05 — Build the query

Coherence decides *which* stats to search on. This is the judgement the
mature overlays leave to the player.

| Filter | Bound | Rule |
|---|---|---|
| Category, rarity | `exact` | Every rarity searches itself — a rare and a normal of the same base are different goods. |
| Item level | `min` | **Only on a normal or magic base**, where the level is the good itself — and as a floor, since a higher level rolls everything a lower one can. Pinned exactly, a Bloodstone Amulet with Spirit and +1 minion skills found nothing at four rungs while every ilvl-40 copy was at least as good. A rare is bought on its mods and a unique on its roll, so neither carries the filter at all. |
| Quality | `min` | **Gems only.** Currency takes anything else to 20%. |
| Requirements (str, dex, int) | `max` | A requirement is a cost. An item demanding less is easier to equip and *is* a comparable. **Never the level**: it follows the mods rather than the base — a Bloodstone Amulet with Spirit and +1 minion skills searched at "level ≤ 36" found nothing at four rungs — and a level the buyer is past is not a cost anyone pays. |
| Defences (ar, ev, es, ward, block, spirit) | `min` | The displayed total, runes removed. On a rare, rebuilt at the mods' floor rolls; on a unique, kept at the actual roll — the roll is all that separates copies. |
| DPS | `min` | Total only, **runes removed**, the same as the defences. Not pdps/edps: splitting it pins the damage source. |
| Granted skill | `min` | At its own level. Exempt from the widening ladder. |
| Pseudo totals | `min` | Summed at each contributor's *floor*. A buyer filters total resistance, not its sources. A stat GGG totals is **always** searched as that total and never as its mods: two filters ANDed are stricter than the sum they make, so searching them apart narrows what the total exists to widen. A total that serves no buyer of this item is still searched, behind the archetype: defence totals (resistances, life) are generic value every buyer pays for and outlive the mods serving some other buyer, while an off-archetype attribute total — the Intelligence that once moved a 3ex quarterstaff to a 50ex median — sits at the very back and is dropped first. |
| Explicit mods | `min` | The floor of the roll's own range — a point lower is the same good at the same tier. Measured on a five-mod ring: minimums at the exact rolls matched 0 listings (every near-copy rolled a point under somewhere; leech 7.76 against 7.81) and the ladder fell through to a 1 ex junk floor, while the same-tier market sat at 3–20 ex. Added-damage mods filter on the average of the two numbers, at the actual roll — the range of the average is not known. On a unique, the actual roll — there is no same-tier near-copy of a unique, only a worse one, and the roll is what escalated it. |
| Implicits | `min` | At the *floor* of the range — the implicit comes with the base, and the buyer wants the base. |
| A wording the item carries twice | `min` | An Iron Ring's implicit and its Flaring prefix are both "Adds # to # Physical Damage to Attacks". With no pseudo to total them — the trade table has none for damage — each is searched under its own group id at its own bound: the prefix at our roll, the implicit at its floor. |
| Waystone | *search from 80* | A waystone under the juice-it band is not searched: a stone's search caps at 10,000 matches — a commodity — and the exchange carries that commodity by tier (Waystone (Tier 15) held 6,957 online units), so a search only ever spent a call to learn what the book already said. The price is the tier's bulk book. What separates one stone from another is its tooltip — since 0.5 every mod carries a fixed loot line and the tooltip sums them into item rarity, pack size, monster rarity, monster effectiveness — scored as loot: `1.0 × monster rarity + 1.0 × pack size + 0.5 × effectiveness + 0.5 × item rarity`, effectiveness exact from the game's glossary (1% more quantity per 2%), pack size a point of drops per point since it multiplies every pack, the rest judgement, banded under 50 reroll, 50 run it, 65 juice it, 85 chase, and the band is worn as a colour — grey, blue, yellow, orange, bold from yellow up. Under it, the Distilled Emotion to instill: the score is linear, so it is the same every time — Paranoia, +15 monster rarity — and the line gives the score and band one of it buys. Scores are integers and the band is the integer's, or a 64.6 prints as 65 and bands as "run it". A corrupted stone takes none; an instilled one has had its turn. A `danger` line calls out the mods that kill the player, red, one per row — −max resistances, penetration, extra chaos, less recovery, the desecrated no-damage and Marked for Death — since the score says nothing about whether the map can be run. The merely risky tier (extra elemental, more monster damage, crit, speed, projectiles, curses, less cooldown recovery, flask charges, ground patches) is counted in a dim row, not shown: it is most of every stone. From 80 up the stone is searched on tier and the five totals as floors — a comparable at least as good on all five is what the book by tier cannot say; when nothing that good is listed, the book is reported as the floor. |
| Corrupted, sanctified | `= No` | Only when ours is neither. Once touched, the whole market is comparable again. |
| Attacks/sec, crit, sockets | — | Traded off against damage rather than added to it. |

Anything covered by an equipment filter stops being a stat filter, or it
is asked for twice. Stat ids are group-scoped: `implicit.`, `enchant.`
and `explicit.` are different tables for the same numeric stat, and
asking the wrong one silently matches nothing.

### 06 — Widen until the sample means something

**Minimums are never lowered** below the roll's own tier. Searching under
the tier asks what *worse* items are worth, which answers a different
question and drags the price down. Widening drops whole filters instead,
in a fixed survival order, front kept longest: *identity* (unique flags,
and notables the item rolled — a Megalomaniac's cannot be changed), the
*archetype* totals and mods the dominant buyer filters on, *anointed
notables* (an anoint can be re-anointed, so it is a mod, not identity),
*generic value* (defence totals and mods every buyer pays for), and
*unrelated* last — a mod serving some other buyer, like attack damage on
a minion ring, constrains the comparables while describing nobody who
would buy this item, and is the first thing given up. Within a tier a
desecrated mod leads whatever the weights say — it was bone-crafted onto
the item on purpose, the strongest buyer signal the item carries: on a
minion ring the desecrated minion mod is what its 5-49 div comparables
shared, while the weight table preferred the fire roll beside it. Then
weight, then roll quality, then the game's own mod tier.

| Rung | Stats kept | What remains |
|---|---|---|
| 0 | all | The whole item, every searchable mod at your own roll. Tagged `exact` — the only rung that describes the item you are holding. On an item carrying four stats or fewer this IS rung 1, and the repeat is skipped rather than searched. |
| 1 | 4 | The weakest cohering mods fall away first. |
| 2 | 3 | ⋮ |
| 3 | 2 | ⋮ |
| 4 | 1 | One stat left. |
| 5 | 0 | Category, rarity, requirements and the equipment totals — plus the pinned item level when the item is a craft base. On a weapon that is DPS plus requirements, which is how you would search by hand. Notable jewels still keep one notable. |

A rung is accepted the moment its sample is *firm* — at least 8 matches.
Fewer than 8 is kept as a fallback but the ladder keeps going; fewer
than 3 is labelled `!! GUESS` in the output.

### 07 — Filter the listings, then take statistics

The cheapest 10 listings are fetched, reading up to 30 deep when drops
thin the sample. A listing that clears our floor — defences or DPS —
*only* because of its socketed runes is discarded: the buyer sockets
their own, so it is a worse item wearing our numbers. On one Forgotten
Warden, 20 of the first 30 matches were rune-inflated.

From what survives come three numbers — the low, the 25th percentile and
the median — and one derived from them: the **suggested ask**, 0.9 × the
low. Undercutting the cheapest comparable is what actually sells an
item. The ask is computed on every priced item and carried through to
the report, but *no row currently prints it*; the market row is what you
read.

In an ordinary market the ask sits just under the low: low 4.7 div,
25th 6.58, median 13.15 → ask 4.23 = 0.9 × low. When one dump listing
sits far below the body of the market — low 0.2, 25th 32, median 117 —
the low is one person dumping, not the market (`low × 10 < median` is
the test), and an ask derived from it tells you to give the item away,
so the quartile becomes the basis instead: ask 29 = 0.9 × 25th, not
0.18. The low is still what the row leads with and what the session
total counts, so the row marks it — `low 1 ex (dump)`, dimmed beside the
two lit prices — and the line under the market block says how far under
the median it sits.

The sample size is reported as confidence, and a weak one is printed
*above* the number rather than under it — buried as a footnote, the
number gets believed anyway.

| Matches | Confidence | What is printed |
|---|---|---|
| ≥ 8 | firm | The ladder stops here — this rung is the answer. |
| 3 – 7 | thin | `!! THIN` — kept as a fallback while the ladder keeps widening. |
| < 3 | very-thin | `!! GUESS … this is NOT a price`. With three results the cheapest is easily a mispriced outlier: a quarterstaff worth ~3 ex once reported 320 ex. |
| 0, every rung | — | `no comparable listing` — nothing at least as good is listed. Those are the items most worth pricing by hand, so they are flagged, never zeroed. |

A price found only after widening carries its rung in the output, and
the comparables behind it are weaker than the item — that price reads as
a floor rather than a ceiling. Results are cached under a hash of the
query, so re-copying the same item replays the answer and costs no API
call.

## Reading the output

<pre>Forgotten Warden  [Primal Markings]
  type       unique · Body Armours → armour.chest   ilvl 84
  searched   <ins>Forgotten Warden · unique · Primal Markings</ins>
             <ins>Grants Skill: Level 20 Spirit Vessel</ins>
             <ins>total dexterity ≥ 20</ins>
             <ins>Companions have #% increased maximum Life ≥ 30</ins>
  unsearched 40% increased Thorns damage                                                             (widened away)
             +76 to Deflection Rating per 50 missing Energy Shield                                   (widened away)
             14% of Damage from Deflected Hits is taken from Damageable Companion's Life before you  (widened away)
  coherence  none — the mods serve different builds
  market     low <b>360 ex</b>  ·  25th <b>719 ex</b>  ·  median <b>1,079 ex</b>
             cheapest 19 of 1,153 listings, exact
             1 skipped — met your numbers only with their runes</pre>

The title is painted in the game's own rarity colour — yellow rare,
blue magic, orange unique, white normal — and the `type` row says the
word, since colour does not grep and a junk item is never searched, so
nothing else would.

The blue lines under `searched` (underlined here) are the query itself.
The first names the market the search is scoped to — the rarity, and
the name and base when they pin, so a unique searched by name says so:
`Oaksworn · unique · Sigil Crest Shield`. Then each stat filter the
priced rung sent, with the floor it asked at: the pseudo sums
(`total dexterity ≥ 20` is a +24 roll at the floor of its range) and
the skill at its own level. On an item with a dominant archetype the
row reads `searched as minion` first — the buyer the whole widening
ladder is ordered around, named from the item's own mods so it holds
at every rung; this chest serves no single buyer, so the query stands
alone. `unsearched`, bright red in the terminal, is the rest of the
item — the mods the price does NOT account for: `(widened away)` means rung 0 asked for it and the
rung that priced had dropped it, `(unsearchable)` means no filter
exists for it at all — a value-less mod with no flag id, or a wording
GGG's own table lacks; a mod the allowlist merely never weighted is
still searched. The market prices are lit amber (bold here), and
the market row is last because the rows above it explain how the
number was reached.

## Caveats

Ordered by how likely each is to hand you a wrong number.

### Nothing cross-checks the exchange either

**unvalidated.** The index drifted — 26.5 ex on an omen the book sold
at 1, 17.6 on a Chaos Orb the book says is 32.5 — precisely because
currency never reached the trade search and nothing contradicted it.
Bulk pricing fixes that reading, and inherits the same shape of risk:
the tenth percentile and the ask/bid midpoint are chosen constants,
checked against a handful of live books. A tenth clears the trap offers
on a book 18,520 units deep; on a shallow one it can still land on a
single seller, which is what the `thin book` row is for.

### Uniques and gear still price off the index, unchecked

**sharp edge.** The exchange carries only what trades in bulk. A unique
that does not escalate takes its index price with nothing to contradict
it — the same position currency was in before the book was wired up.
The escalation rules in stage 02 are what limit the exposure, not a
second source.

### The trade item-search cannot price stackables

**known wrong.** Sorted by price ascending, *Divine Orb* — worth
~360 ex — shows listings of "1 exalted" for a stack of 10. The bottom of
the item search is junk for anything stackable, which is why bulk items
are priced off the exchange book instead and why the item search is not
a fallback for them.

### The suggested ask is computed and then not shown

**sharp edge.** `suggested_ask_ex` is derived on every priced item —
0.9 × the low, or 0.9 × the quartile on a skewed market — and reaches
the report object, but no output row renders it. The market row is
complete without it, so nothing is wrong on screen; the number is simply
doing no work.

### A craft base is pinned to one item level

**sharp edge.** On a normal or magic item `ilvl` takes a min *and* a max
and survives every rung of the ladder, because an ilvl 79 base is a
different product from an ilvl 82 one rather than a cheaper copy. The
market for a single level can be thin. Rares and uniques carry no item
level filter at all, which is what lets a genuinely comparable weapon a
few levels lower be seen — a hand-built search for one mace once
surfaced an ilvl-75 listing sox could not. Augmentable sockets are still
not filtered.

### Advanced Item Descriptions must be on

**sharp edge.** Without inline ranges there is no roll quality, no
implicit floor, and no way to tell an implicit from an explicit — the
`{ Implicit Modifier }` header is what classifies it. Pricing degrades
toward a stricter search rather than a wrong one, but it degrades.

### The number is a ceiling, not a sale

**by design.** Every listing matched is better than ours on every
constrained axis, and being *listed* is not being *sold*. Nobody may
have paid the low. This is the whole basis of the method and the reason
the 25th percentile and median are shown beside it.

### The weights are hand-assigned

**unvalidated.** Mod weights come from reading build guides, not from
regression against realised prices. Coherence tags likewise. They order
the search sensibly; they are not calibrated. Of the endgame item
classes only waystones have been price-verified live — the
tier-and-item-rarity rule was measured against the market — and the rest
never have been.

### Prices are cached

**stale data.** A search result is replayed for 12 hours and the index
for 6, keyed on the exact query. A cached row is labelled, but it can be
a day behind a moving market. Rate limits are the reason: `5:10:60`
searches per window, and the longer clauses bite harder — `30:300` is six
searches a minute sustained, one item at six rungs.

---

Generated from the implementation in `src/sox/valuation/` —
`candidates.py` routes, `query.py` builds, `trade_pricer.py` widens and
averages. Where this page and the code disagree, the code is right and
this page is stale.
