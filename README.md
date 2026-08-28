# SoX — PoE2 item pricer

Prices Path of Exile 2 items from the text the game puts on your clipboard.

```
sox watch
```

## Install

`bin/sox` is a launcher that resolves its own symlink to find this project,
so it runs from any directory:

```
ln -sfn "$PWD/bin/sox" ~/bin/sox
```

Without the symlink, `uv run sox ...` from the project directory works too.

## Watch mode

Park a terminal on a second monitor and run `sox watch`. Copy any item in
game with Ctrl+C and its price appears, with a running session total.
The session starts at once on the exchange snapshot it last saw, fetches
this hour's behind it, and warns when what poe2scout has is hours old:

<pre>════════════════════════════════════════════════════════════════
<b>sox watch</b> 0.1.0+5d4a8af  ·  Runes of Aldur  ·  1 div = 360 ex
clipboard: powershell.exe (Windows clipboard via WSL)
copy an item in game (Ctrl+C) and it is priced here · Ctrl-D to stop
════════════════════════════════════════════════════════════════
14:31:58 <b>Corruption Hold  [Amethyst Ring]</b>
  type       rare · Rings → accessory.ring   ilvl 80
  searched   as minion
             <ins>rare</ins>
             <ins>Minions have #% increased Critical Hit Chance ≥ 30</ins>
             <ins>Minions deal #% increased Damage if you've Hit Recently ≥ 20</ins>
             <ins>Minions have #% increased Attack and Cast Speed ≥ 8</ins>
             <ins>Minions deal #% increased Damage ≥ 20</ins>
  unsearched +13% to Chaos Resistance                 (widened away)
             Adds 9 to 18 Physical Damage to Attacks  (widened away)
             +23% to Chaos Resistance                 (widened away)
  coherence  4 mods cluster on minion  +3
  market     low <b>11 div</b>  ·  25th <b>20 div</b>  ·  median <b>32.5 div</b>
             cheapest 10 of 98 listings, relaxed:1
             found only after widening, so these comparables are weaker than your item — read the price as a floor
────────────────────────────────────────────────────────────────
<ins>total 3,956 ex (11.0 div)</ins>  ·  1 priced  ·  2 searches  ·  3/5 left (10s window)
────────────────────────────────────────────────────────────────
14:32:39 <b>Honour Spiral  [Unset Ring]</b>
  type       rare · Rings → accessory.ring   ilvl 82
  searched   <ins>rare</ins>
             <ins>Grants 1 additional Skill Slot</ins>
             <ins>total elemental resistance ≥ 88</ins>
             <ins>Minions deal #% increased Damage if you've Hit Recently ≥ 10</ins>
  unsearched 13% increased Fire Damage  (widened away)
  coherence  none — the mods serve different builds
  market     low 168 ex (dump)  ·  25th <b>719 ex</b>  ·  median <b>1,798 ex</b>
             cheapest 10 of 21 listings, relaxed:2
             found only after widening, so these comparables are weaker than your item — read the price as a floor
             the low is 11x under the median — read the 25th as the price
────────────────────────────────────────────────────────────────
<ins>total 4,125 ex (11.5 div)</ins>  ·  2 priced  ·  4 searches  ·  11/15 left (60s window)
────────────────────────────────────────────────────────────────</pre>

## What makes it different from an overlay

Exiled Exchange 2 is the mature PoE2 price-check overlay and better at
ergonomics. But by its own documentation you supply the judgement: "You tick
the checkboxes and relevant filters for the item yourself. Choose stats that
synergize well, this knowledge only comes from playing different build
archetypes."

SoX encodes that based on... well, [_my own judgement_](docs/pricing-algorithm.md)
(I read a few guides and try to sell stuff often, but that's about it).

Waystones get their own page — what the market pays for on a stone,
measured, and how to roll one worth paying for: [docs/waystones.md](docs/waystones.md).

## Why the clipboard

**There is no PoE2 stash API.** The OAuth stash endpoint supports only the
`xbox` and `sony` realms — GGG added `poe2` to the Character and League
endpoints and never to Stash — and the legacy `character-window` route
accepts `realm=poe2` while silently returning PoE1 data. So items come in the
way every PoE2 price-check tool takes them: the clipboard.

No login is needed. The trade API answers search and fetch without any
session, so sox holds no credentials at all.

## What it does

| Item | How it is priced |
|---|---|
| Currency, runes, essences, omens, fragments, gems | the game's own Currency Exchange first — fills cannot be faked by bait listings, but must amount to a market: 5,000 ex and twenty units of the item — then the bulk trade book, read from sellers who are online in league, against both exalted and divine, the broader book answering |
| Most uniques | poe2scout index — free, no API call |
| Rares, jewels | trade search for "the cheapest item at least as good as yours", every mod on the item in the first search, each compared at the floor of its roll's tier — a point under your roll is the same good |
| Normal and magic bases | the same search, pinned to the base and floored at its item level |
| Waystones | the tier's bulk exchange book — a stone's search caps at 10,000 matches, a commodity — unless its loot score reaches 80, when it is searched on tier and its five tooltip totals. What separates one stone from another is the loot score from its tooltip totals, coloured by band — grey reroll, blue run it, yellow juice it, orange chase — the Distilled Emotion to instill, with the score one of it buys — and the mods that kill the player, in red, with a count of the merely risky |
| Tablets, relics, charms | trade search; no index covers them |
| Anything allocating a notable | searched by the exact notable — a Megalomaniac, or an amulet carrying an enhancement |

The market row is a **ceiling**, not a comp: every listing returned is at
least as good as your item on every constrained axis, so the cheapest one
bounds what you can ask. The lower quartile and the median come with it,
because a single cheapest listing is as often a dump as a price.

## Data files

`src/sox/data/` is generated from GGG's live tables — never hand-edited:

| File | Contents |
|---|---|
| `mod_allowlist.toml` | Weighted mods: weights, archetype tags, subjects. Every other mod in GGG's trade table is searched too, at no weight, from the live table |
| `notables.toml` | Notable → stat id, for notable-granting jewels |
| `skills.toml` | Granted skill → stat ids, for `Grants Skill: Level N X` |
| `flag_mods.toml` | Value-less mod text → stat id, for mods with no roll to search at |
| `item_bases.toml` | Every base name, for pinning what a normal, magic or unique item IS |
| `base_allowlist.toml` | ilvl tiers, equipment slots, named crafting bases |
| `unique_allowlist.toml` | Build-relevant uniques with escalation thresholds |

Regenerate after a patch:

```
curl -A 'sox' https://www.pathofexile.com/api/trade2/data/stats -o stats.json
python3 scripts/resolve_allowlist.py stats.json > src/sox/data/mod_allowlist.toml
python3 scripts/resolve_notables.py stats.json > src/sox/data/notables.toml
python3 scripts/resolve_skills.py stats.json > src/sox/data/skills.toml
python3 scripts/resolve_flag_mods.py stats.json > src/sox/data/flag_mods.toml
curl -A 'sox' https://www.pathofexile.com/api/trade2/data/items -o items.json
python3 scripts/resolve_bases.py items.json > src/sox/data/item_bases.toml
```

The generators fail loudly rather than dropping an entry they cannot resolve,
and reuse previously resolved ids so a reworded mod cannot silently vanish.
