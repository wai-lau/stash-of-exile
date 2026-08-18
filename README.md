# sox — PoE2 item pricer

Prices Path of Exile 2 items from the text the game puts on your clipboard.

```
# copy an item in game with Ctrl+C, then:
uv run sox price          # paste, then Ctrl-D
uv run sox price -f items.txt
uv run sox leagues
```

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
| Currency, gems, most uniques | poe2scout index — free, no API call |
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
| `mod_allowlist.toml` | 405 mods with weights, archetype tags, subjects |
| `notables.toml` | 874 notable → stat id, for notable-granting jewels |
| `base_allowlist.toml` | ilvl tiers, 19 slots, named crafting bases |
| `unique_allowlist.toml` | 38 build-relevant uniques with escalation thresholds |

Regenerate after a patch:

```
curl -A 'sox' https://www.pathofexile.com/api/trade2/data/stats -o stats.json
python3 scripts/resolve_allowlist.py stats.json > src/sox/data/mod_allowlist.toml
python3 scripts/resolve_notables.py stats.json > src/sox/data/notables.toml
```

The generators fail loudly rather than dropping an entry they cannot resolve,
and reuse previously resolved ids so a reworded mod cannot silently vanish.

## Tests

```
uv run --with pytest python -m pytest -q
```

35 tests, no network calls. Item fixtures are real clipboard captures.
