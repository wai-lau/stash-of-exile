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
sox watch  ·  Runes of Aldur  ·  1 div = 320 ex
────────────────────────────────────────────────────────────────
20:14:28  Megalomaniac  [Diamond]
          class      unique  ilvl 80
          ceiling    333 ex (1.0 div)   (3 listings, relaxed:3)
          ask        300.1 ex
          searched   as notable
                     - Allocates Barbaric Strength
                     - Allocates Kite Runner
────────────────────────────────────────────────────────────────
total 333 ex (1.0 div)  ·  1 priced  ·  4 searches
```

On WSL the Windows clipboard is read through a single long-lived
powershell.exe, because starting one per poll costs about half a second.
Native Linux (wl-paste, xclip, xsel) and macOS (pbpaste) are polled directly.
Non-item text is ignored, and whatever was on the clipboard before startup is
skipped.

## What makes it different from an overlay

Exiled Exchange 2 is the mature PoE2 price-check overlay and better at
ergonomics. But by its own documentation you supply the judgement: "You tick
the checkboxes and relevant filters for the item yourself. Choose stats that
synergize well, this knowledge only comes from playing different build
archetypes."

sox encodes that. It picks the stats to search on by finding the item's
dominant archetype rather than its heaviest mods, and prints what it chose:

```
  searched   as spell
             - # to Level of all Spell Skills
             - #% increased Cast Speed
             - #% increased Spell Damage
```

Picking by weight alone would have taken Attack Speed over Spell Damage on
that amulet and described a buyer who does not exist.

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

Prices are reported as a **ceiling**, not a comp: every listing returned is at
least as good as your item on every constrained axis, so the cheapest one
bounds what you can ask.

A Megalomaniac the index prices at 1 ex, priced by its notables instead:

```
Megalomaniac  [Diamond]
  class      unique  ilvl 80
  ceiling    1,009 ex (3.0 div)   (4 listings, relaxed:3)
  ask        908 ex (2.7 div)
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
