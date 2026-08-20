# sox — PoE2 item pricer

Prices Path of Exile 2 items from the text the game puts on your clipboard.

```
sox watch          # live feed: every item you copy gets priced
sox price          # one-shot: paste, then Ctrl-D
sox price -f items.txt
sox leagues
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
game with Ctrl+C and its price appears, with a running session total:

```
════════════════════════════════════════════════════════════════
sox watch 0.1.0+425bb6d  ·  Runes of Aldur  ·  1 div = 382 ex
clipboard: powershell.exe
copy an item in game (Ctrl+C) and it is priced here · Ctrl-D to stop
════════════════════════════════════════════════════════════════
18:04:07 Megalomaniac  [Diamond]
            type       Jewels → jewel   ilvl 80
            searched   as notable
            score      0
                       +0  Allocates Barbaric Strength
                       +0  Allocates Kite Runner
            coherence  none — the mods serve different builds
            market     low 4.72 div  ·  25th 4.72 div  ·  median 8.97 div
                       cheapest 10 of 759 listings, relaxed:3
────────────────────────────────────────────────────────────────
total 1,803 ex (4.7 div)  ·  1 priced  ·  2 searches
────────────────────────────────────────────────────────────────
```

On WSL the Windows clipboard is read through a single long-lived
powershell.exe, because starting one per poll costs about half a second.
Native Linux (wl-paste, xclip, xsel) and macOS (pbpaste) are polled directly.
Non-item text is ignored, whatever was on the clipboard before startup is
skipped, and one copy is priced once — a clipboard that reads the same twice
is the same copy, not a second one.

The banner names the commit it is running. A feed cannot pick up a fix while
it is up: the module is imported once and the PowerShell watcher is a child
process started with it, so a change to either needs the session restarted.

## What makes it different from an overlay

Exiled Exchange 2 is the mature PoE2 price-check overlay and better at
ergonomics. But by its own documentation you supply the judgement: "You tick
the checkboxes and relevant filters for the item yourself. Choose stats that
synergize well, this knowledge only comes from playing different build
archetypes."

sox encodes that. It picks the stats to search on by finding the item's
dominant archetype rather than its heaviest mods, prints what it chose, and
marks every mod the price actually rests on:

```
Corruption Hold  [Amethyst Ring]
  type       Rings → accessory.ring   ilvl 80
  searched   as minion
  score      12
             +0  +13% to Chaos Resistance                        (implicit)
             +2  Minions deal 22% increased Damage               (minion)
             +2  Adds 9 to 18 Physical Damage to Attacks
             +2  Minions have 37% increased Critical Hit Chance   (minion)
             +2  Minions have 10% increased Attack and Cast Speed (minion)
             +2  +23% to Chaos Resistance
             +0  Minions deal 25% increased Damage if you've Hit Recently
  coherence  3 mods cluster on minion  +2
  market     low 5 div  ·  25th 8 div  ·  median 10 div
             cheapest 10 of 175 listings, relaxed:1
```

Picking by weight alone would have kept that chaos resistance roll and
dropped the minion crit and the minion attack speed, describing a buyer who
does not exist. It priced the ring at 65 ex.

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
| Currency, runes, essences, omens, fragments, gems | bulk exchange book, priced from both sides |
| Most uniques | poe2scout index — free, no API call |
| Rares, bases, jewels | trade search for "the cheapest item at least as good as yours" |
| Waystones, tablets, relics, charms | trade search; no index covers them |
| Notable jewels (Megalomaniac) | searched by the exact notables they allocate |

The market row is a **ceiling**, not a comp: every listing returned is at
least as good as your item on every constrained axis, so the cheapest one
bounds what you can ask. The lower quartile and the median come with it,
because a single cheapest listing is as often a dump as a price.

A Megalomaniac the index prices at 1 ex, priced by its notables instead:

```
Megalomaniac  [Diamond]
  type       Jewels → jewel   ilvl 80
  searched   as notable
  score      0
             +0  Allocates Barbaric Strength
             +0  Allocates Kite Runner
  market     low 5 div  ·  25th 5 div  ·  median 9.5 div
             cheapest 10 of 759 listings, relaxed:3
```

## Data files

`src/sox/data/` is generated from GGG's live tables — never hand-edited:

| File | Contents |
|---|---|
| `mod_allowlist.toml` | Searchable mods with weights, archetype tags, subjects |
| `notables.toml` | Notable → stat id, for notable-granting jewels |
| `skills.toml` | Granted skill → stat ids, for `Grants Skill: Level N X` |
| `flag_mods.toml` | Value-less mod text → stat id, for mods with no roll to search at |
| `base_allowlist.toml` | ilvl tiers, equipment slots, named crafting bases |
| `unique_allowlist.toml` | Build-relevant uniques with escalation thresholds |

Regenerate after a patch:

```
curl -A 'sox' https://www.pathofexile.com/api/trade2/data/stats -o stats.json
python3 scripts/resolve_allowlist.py stats.json > src/sox/data/mod_allowlist.toml
python3 scripts/resolve_notables.py stats.json > src/sox/data/notables.toml
python3 scripts/resolve_skills.py stats.json > src/sox/data/skills.toml
python3 scripts/resolve_flag_mods.py stats.json > src/sox/data/flag_mods.toml
```

The generators fail loudly rather than dropping an entry they cannot resolve,
and reuse previously resolved ids so a reworded mod cannot silently vanish.

## Tests

```
uv run --with pytest python -m pytest -q
```

No network calls. Item fixtures are real clipboard captures.
