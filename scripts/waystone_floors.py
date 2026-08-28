"""Live waystone floors: what the trade site asks at each minimum, so the
loot weights and the search threshold can be checked against the market.

    uv run python scripts/waystone_floors.py floors.json

Twenty-five searches through SoX's own client and governor — about five
minutes at the search limits. The 2026-08-28 run is written up in
docs/waystones.md; re-run after a patch or a league and update the page.
"""
import json, sys, time
from pathlib import Path
import httpx
from sox.cache import Cache
from sox.cli import _ggg_session
from sox.config import load_config
from sox.ggg.trade import TradeClient
from sox.scout import ScoutClient
from sox.valuation.trade_pricer import _median, _percentile

OUT = Path(sys.argv[1])
cfg = load_config()
cache = Cache(cfg.cache_path)
scout = ScoutClient(httpx.Client(timeout=30), cache, cfg.user_agent)
league = scout.current_league(cfg.hardcore)
rates = scout.currency_rates(scout.prices(league.short))
session = _ggg_session(cfg, lambda s, r: print(f"  … waiting {s:.0f}s ({r})", flush=True))
trade = TradeClient(session, cache, cfg.league or league.value)
print("league", league.value, "divine", rates.get("divine"), flush=True)

def query(tier, corrupted=False, rarity="rare", **mins):
    mf = {"map_tier": {"min": tier, "max": tier}}
    mf.update({k: {"min": v} for k, v in mins.items()})
    return {"query": {
        "status": {"option": cfg.status},
        "filters": {
            "type_filters": {"filters": {"category": {"option": "map.waystone"},
                                         "rarity": {"option": rarity}}},
            "map_filters": {"filters": mf},
            "misc_filters": {"filters": {"corrupted": {"option": "true" if corrupted else "false"}}},
        },
        "stats": [{"type": "and", "filters": []}],
    }, "sort": {"price": "asc"}}

CASES = [
    ("T15 rare, any", dict(tier=15)),
    ("T15 MR≥40", dict(tier=15, map_rare_monsters=40)),
    ("T15 MR≥60", dict(tier=15, map_rare_monsters=60)),
    ("T15 MR≥80", dict(tier=15, map_rare_monsters=80)),
    ("T15 MR≥100", dict(tier=15, map_rare_monsters=100)),
    ("T15 PS≥15", dict(tier=15, map_packsize=15)),
    ("T15 PS≥25", dict(tier=15, map_packsize=25)),
    ("T15 PS≥35", dict(tier=15, map_packsize=35)),
    ("T15 IR≥50", dict(tier=15, map_iir=50)),
    ("T15 IR≥80", dict(tier=15, map_iir=80)),
    ("T15 IR≥120", dict(tier=15, map_iir=120)),
    ("T15 ME≥30", dict(tier=15, map_magic_monsters=30)),
    ("T15 ME≥50", dict(tier=15, map_magic_monsters=50)),
    ("T15 DC≥80", dict(tier=15, map_bonus=80)),
    ("T15 DC≥120", dict(tier=15, map_bonus=120)),
    ("T15 DC≥160", dict(tier=15, map_bonus=160)),
    ("T15 MR≥60 PS≥20", dict(tier=15, map_rare_monsters=60, map_packsize=20)),
    ("T15 MR≥80 PS≥20 IR≥50", dict(tier=15, map_rare_monsters=80, map_packsize=20, map_iir=50)),
    ("T15 corrupted rare, any", dict(tier=15, corrupted=True)),
    ("T15 corrupted MR≥60", dict(tier=15, corrupted=True, map_rare_monsters=60)),
    ("T16 rare, any", dict(tier=16, corrupted=True)),
    ("T16 MR≥60", dict(tier=16, corrupted=True, map_rare_monsters=60)),
    ("T16 PS≥25", dict(tier=16, corrupted=True, map_packsize=25)),
    ("T16 MR≥80 PS≥20", dict(tier=16, corrupted=True, map_rare_monsters=80, map_packsize=20)),
    ("T15 magic, any", dict(tier=15, rarity="magic")),
]

results = []
for label, kw in CASES:
    try:
        qid, hashes, total = trade.search(query(**kw))
        listings = trade.fetch(qid, hashes[:10]) if hashes else []
    except Exception as exc:  # noqa
        print(f"{label:28} ERROR {type(exc).__name__}: {exc}", flush=True)
        results.append({"label": label, "error": str(exc)}); continue
    prices = sorted(p for p in (l.to_exalted(rates) for l in listings) if p is not None)
    row = {"label": label, "matches": total, "n": len(prices),
           "low": prices[0] if prices else None,
           "p25": _percentile(prices, 0.25) if prices else None,
           "median": _median(prices) if prices else None,
           "prices": prices}
    results.append(row)
    print(f"{label:28} matches {total:5}  low {row['low'] or 0:8.1f}  p25 {row['p25'] or 0:8.1f}  med {row['median'] or 0:8.1f} ex", flush=True)
    OUT.write_text(json.dumps({"league": league.value, "divine_ex": rates.get("divine"),
                               "taken": time.time(), "rows": results}, indent=1))
print("done", flush=True)
