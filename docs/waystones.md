# Waystones

What a waystone is worth, and how to make one worth more. Researched
2026-08-28 on patch 0.5.4f, Runes of Aldur; 0.5.5 and an economy reset
land in early September and will move every number here. The crafting
half is read from guides and the game's own patch notes; the pricing half
is measured on the trade site through SoX's own client, the way the rest
of [the pricing algorithm](pricing-algorithm.md) was.

## What loot is, since 0.5

Every waystone modifier is one bad thing for you and one fixed loot
line, and the tooltip sums the loot lines into five totals. Buyers read
the five and nothing else, which is why SoX searches a stone on them
(`waystone_filters`) and scores it on four (`LOOT_WEIGHTS`):

| Total | What it does (0.5.0 notes) | Weight |
|---|---|---|
| Monster Rarity | "affects the chances of magic and rare monsters, and the chance for additional modifiers on Rare Monsters" — rares drop the loot | 1.0 |
| Pack Size | multiplies every pack; "now also provide a chance for an additional Rare Monster to appear in Rare Monster packs" | 1.0 |
| Monster Effectiveness | "The bonus to Quantity of Item Dropped and Experience Gain provided by increased Monster Effectiveness has been halved" | 0.5 |
| Item Rarity | upgrades drops, with diminishing returns on top of the character's own | 0.5 |
| Waystone Drop Chance | sustain, not loot; not scored, still searched | — |

The loot lines are fixed per modifier, so the best stone is a matter of
which modifiers it rolled. Per modifier, from poe2db:

| Slot | Modifier | Loot line |
|---|---|---|
| prefix | Monster Damage | Monster Rarity 25 |
| prefix | Rare monsters gain 1 additional modifier | Monster Rarity 25 (and the extra modifier is itself more loot) |
| prefix | Monster Life | Monster Rarity 23 |
| suffix | Armoured | Monster Rarity 18 |
| suffix | Monsters take reduced extra damage from Critical Hits | Monster Rarity 18 |
| suffix | −maximum Player Resistances | Pack Size 10 |
| prefix | Attack/Cast/Movement Speed · extra Projectiles | Pack Size 9 |
| prefix/suffix | Lightning damage · Temporal Chains | Pack Size 8 |
| prefix | Penetration · Chaos damage | Item Rarity 16 · 15 |
| prefix/suffix | Fire damage · Enfeeble | Monster Effectiveness 16 |
| prefix/suffix | Critical Hit Chance · Armour Break · Ailment/Stun Threshold · Area of Effect | drop chance only |

The rest are Pack Size 6–7, Item Rarity 10–14, Effectiveness 13–15.

## The best natural stone

Three prefixes: Monster Damage, Monster Life, Rares +1 modifier — Monster
Rarity 73. Three suffixes: Armoured, reduced crit damage, −max res —
Monster Rarity 36, Pack Size 10. Loot score **119** (chase is 90 up),
tooltip about Monster Rarity 109 / Pack Size 10. It is also a stone that
hits hard, has more life and shrugs crits with your resistances capped
lower; the danger line says so.

## Making one

1. **Tier 15.** Sixteen exists only through corruption (below). Alchemy
   to four modifiers, two Exalts to six. Count before quality: the
   omen step keeps the count and replaces the quality.
2. **Omens.** 0.5.0 *inverted* the three Chaotic omens and added a
   fourth; each now makes the next Chaos Orb "replace all Modifiers on a
   Waystone with Modifiers that do **not** grant" its stat, and "up to 3
   of the above Omens may be used simultaneously". Chaotic Rarity (no
   item rarity) + Chaotic Quantity (no pack size) + Chaotic Effectiveness
   (no effectiveness), one Chaos Orb: all six modifiers reroll from a
   pool of five prefixes and four suffixes, most of them Monster Rarity.
   Two omens (rarity and effectiveness off) leaves a Monster Rarity +
   Pack Size pool — bigger, more misses. Chaotic Monsters excludes
   Monster Rarity and is for someone else.
3. **Instill.** Up to three Distilled Emotions at the Delirium bench; a
   corrupted stone takes none, and an instilled map drops no emotions —
   farm those elsewhere. By the loot weights the order is Paranoia (+15%
   rare monsters, 12% Delirious) over Guilt (+8% pack size), Ire (+20%
   magic monsters) and Greed (+8% item rarity); three Paranoia is +45
   Monster Rarity at 36% Delirious. Fear (rares have a 25% chance of an
   extra modifier) is loot the tooltip does not show; Envy (+30%
   waystones) is sustain. SoX names the best one on the report
   (`instill.py`).
4. **Vaal**, optional, four outcomes at a quarter each: tier ±1 with the
   modifiers rerolled — the only route to 16; lock prefixes or suffixes
   and reforge the other side, which undoes the omen work on that side;
   rare-only extra modifiers up to five prefixes and five suffixes — the
   best stones that exist; nothing. Instill first: a corrupted stone
   cannot be instilled, and an instillation survives corruption on
   amulets (waystones unverified).
5. **Desecrated Abyss modifiers** (Preserved Vertebrae + Well of Souls)
   would add a prefix like "Area is overrun by the Abyssal"; the
   vertebrae are drop-disabled since 0.5.3. Ignore until they return.

Outside the stone, the atlas tree and precursor tablets multiply with it
and matter more; a stone is the last 20–30% of a map's return.

## What the market pays

Measured 2026-08-28 through SoX's own trade client
(`scripts/waystone_floors.py`): Runes of Aldur, instant-buyout listings
only, rare, the tier pinned, uncorrupted unless said, the cheapest ten of
however many matched. Exalted; a divine was 354.

| Search | Matches | Cheapest ten |
|---|---|---|
| T15, anything | 10,000 | 1 · 1 · 1 |
| T15 monster rarity 40+ | 10,000 | 1 · 1 · 2 |
| T15 monster rarity 60+ | 3,681 | 19 · 20 · 23 |
| T15 monster rarity 80+ | 834 | 195 · 299 · 300 |
| T15 monster rarity 100+ | 533 | 378 · 378 · 384 |
| T15 pack size 15+ / 25+ | 10,000 | 1 · 1 · 1 / 1 · 2 · 2 |
| T15 pack size 35+ | 629 | 2 · 100 · 100 |
| T15 item rarity 50+ | 4,749 | 5 · 5 · 5 |
| T15 item rarity 80+ | 148 | 260 · 275 · 300 |
| T15 item rarity 120+ | 0 | — |
| T15 effectiveness 30+ | 4,083 | 1 · 6 · 18 |
| T15 effectiveness 50+ | 604 | 249 · 280 · 295 |
| T15 drop chance 80+ | 10,000 | 1 · 1 · 1 |
| T15 drop chance 120+ | 59 | 120 · 180 · 200 |
| T15 drop chance 160+ | 0 | — |
| T15 monster rarity 60+ and pack size 20+ | 97 | 30 · 100 · 100 |
| T15 monster rarity 80+, pack size 20+, item rarity 50+ | 0 | — |
| T15 corrupted, anything | 10,000 | 3 · 3 · 3 |
| T15 corrupted, monster rarity 60+ | 10,000 | 10 · 18 · 19 |
| T16, anything | 10,000 | 99 · 100 · 110 |
| T16 monster rarity 60+ | 5,260 | 130 · 183 · 190 |
| T16 pack size 25+ | 10,000 | 100 · 110 · 111 |
| T16 monster rarity 80+ and pack size 20+ | 0 | — |
| T15 magic, anything | 10,000 | 1 · 1 · 1 |

(low · 25th · median. 10,000 is the search engine's cap, not a count.)

**A fifteen is a commodity.** Ten thousand listings at 1 ex, magic or
rare, and the bulk book holds 6,170 of them at the same price; corrupted
adds 2. The stone is worth 1 ex until one of its totals nears the top of
its range, and then it is worth about a divine — whichever total it is.

**Price is a cliff on one total, not a sum of five.** Monster rarity 40
is 1 ex, 60 is 20, 80 is 200–300, 100 is 380. Item rarity 50 is 5, 80 is
260–350. Effectiveness 30 is 1–35, 50 is 250–300. Drop chance 80 is 1,
120 is 120–400 on 59 listings. Each total's top costs the same 0.8–1.1
div regardless of what it does for loot: buyers pay for the scarcity of
the roll. The exception is pack size, whose top (35) is 100 ex with a
2-ex listing under it — "pack size is ineffective" is what the guides
say, and the market agrees, whatever the game's own notes say about
extra rares per pack.

**Combinations have no comparables.** Monster rarity 60 with pack size
20 is 97 listings at 30–120 ex, more than either alone; add item rarity
50 to a monster rarity 80 and nothing is listed, on a fifteen or a
sixteen. The stones worth chasing are priced by nobody, which is why the
search reports the tier's book as a floor when it finds nothing.

**Sixteen is 100 ex on its own.** Ten thousand of them from 99; monster
rarity 60 lifts that to 130–200, pack size 25 to 110. The tier is most
of the price, and a total adds less on top of it than it does on a
fifteen — a sixteen with monster rarity 60 is under the fifteen with
monster rarity 80.

**The sixteen's bulk book is bait.** Its cheapest offer is one AFK
account with 63 stones at 1 ex — 68% of the book's stock — under real
asks at 70, 120, 170, 240 and 400. A stock-weighted low quantile lands on
that wall, so the exchange stage prices a Tier 16 at 1 ex against a
search floor of 99. poe2scout's fills carry no waystones, so nothing
catches it; the fifteen's book is honest only because 1 ex is its real
price.

### What changed on the algorithm's side (2026-08-28)

- **The search is for selling.** The owner runs the stones worth
  running and sells the ones that look good and are not, so the search
  opens only on the second kind: loot under 70 (`RUN_LOOT`) with a
  single total at its cliff — monster rarity 60, pack size 35, item
  rarity 80, effectiveness 50, drop chance 120 (`SALE_CLIFFS`,
  `for_sale`), and the search floors on those totals alone — five floors
  at once matched nothing where item rarity 80 alone was 148 listings.
  The loot row names the total that sells it. A stone from
  70 is priced off the tier's book, whatever it rolled — the search was
  only ever going to price what is not for sale. The cliffs are this
  league's; re-run the script and move them.
- **The loot score is about loot and the price is about scarcity**, and
  they disagree on pack size. Both readings stay: the score says whether
  to run it, the search says what it sells for.
- **A Tier 16 is never priced off the bulk book.** It is searched
  whatever it scores; when nothing at least as good is listed, the floor
  is one more search on the tier alone — a 10,000-match search with a
  real floor — and a capped search keeps its sample rather than falling
  to the book.
