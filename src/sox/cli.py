"""Command line entry point.

    sox price          price the item text on stdin (paste, then Ctrl-D)
    sox price -f FILE  price every item in a file, separated by blank lines
    sox leagues        show the current league and the divine rate
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from sox import itemtext, report
from sox.cache import Cache
from sox.config import load_config
from sox.ggg.governor import RateGovernor
from sox.ggg.session import GGGError, GGGSession
from sox.ggg.trade import TradeClient
from sox.scout import ScoutClient
from sox.valuation import candidates
from sox.valuation.allowlists import load_bases, load_mods, load_notables, load_uniques
from sox.valuation.classify import ItemClass, classify, display_name
from sox.valuation.index_pricer import index_price_for
from sox.valuation.mods import build_index
from sox.valuation.query import category_for, explain_selection
from sox.valuation.trade_pricer import price_by_search

ITEM_SEPARATOR = "\n\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sox", description="PoE2 item pricer")
    parser.add_argument("--config", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    price = sub.add_parser("price", help="price item text (Ctrl+C in game, then paste)")
    price.add_argument("-f", "--file", type=Path, help="file of item texts, blank-line separated")
    price.add_argument("--no-trade", action="store_true", help="index only; no trade calls")

    sub.add_parser("leagues", help="show the current league and divine rate")
    return parser


def _read_items(path: Path | None) -> list[str]:
    raw = path.read_text() if path else sys.stdin.read()
    blocks = [b.strip() for b in raw.split(ITEM_SEPARATOR)]
    return [b for b in blocks if "Item Class:" in b or "Rarity:" in b]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)

    cache = Cache(cfg.cache_path)
    scout = ScoutClient(httpx.Client(timeout=30), cache, cfg.user_agent)

    try:
        league = scout.current_league()
        if args.command == "leagues":
            print(f"current league : {league.value} ({league.short})")
            print(f"1 divine       : {league.divine_price_ex:,.1f} exalted")
            return 0

        blocks = _read_items(args.file)
        if not blocks:
            print("error: no item text found. Copy an item in game with Ctrl+C, "
                  "then paste it here.", file=sys.stderr)
            return 2

        index = scout.prices(league.short)
        rates = scout.currency_rates(index)
        mod_index = build_index(load_mods())
        base_rules, unique_rules = load_bases(), load_uniques()
        notables = load_notables()

        trade = None
        if not args.no_trade:
            session = GGGSession(RateGovernor(), httpx.Client(timeout=30), cfg.user_agent)
            trade = TradeClient(session, cache, cfg.league or league.value)

        for n, block in enumerate(blocks):
            if n:
                print()
            print(price_one(block, index, rates, mod_index, base_rules,
                            unique_rules, notables, trade, cache, cfg,
                            league.divine_price_ex))
        return 0
    except (GGGError, httpx.HTTPError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        cache.close()


def price_one(block, index, rates, mod_index, base_rules, unique_rules,
              notables, trade, cache, cfg, divine_ratio) -> str:
    item = itemtext.parse(block)
    item_class = classify(item)
    entry = index_price_for(item, index)

    verdict = candidates.assess(item, entry, mod_index, base_rules, unique_rules)

    if verdict.should_search and trade is not None:
        category = category_for(item)
        if category:
            result = price_by_search(
                item, category, mod_index, notables, trade, cache, rates,
                status=cfg.status, max_searches=cfg.max_searches,
            )
            group, stats = explain_selection(item, mod_index, notables)
            priced = report.PricedItem(
                name=display_name(item), item_class=item_class,
                price_ex=result.ceiling_ex, source="trade" if result.ceiling_ex else "unpriced",
                tag=result.tag, reason=verdict.reason, listings=result.listings,
                suggested_ask_ex=result.suggested_ask_ex,
                searches_used=result.searches_used,
                searched_group=group, searched_stats=stats,
            )
            return report.render(item, priced, divine_ratio)

    if entry is not None:
        priced = report.PricedItem(
            name=display_name(item), item_class=item_class, price_ex=entry.price_ex,
            source="index", tag=None, reason=verdict.reason, quantity=entry.quantity,
        )
        return report.render(item, priced, divine_ratio)

    tag = "unpriced:unknown-class" if item_class is ItemClass.UNKNOWN else "unpriced:no-index"
    priced = report.PricedItem(
        name=display_name(item), item_class=item_class, price_ex=None,
        source="unpriced", tag=tag, reason=verdict.reason,
    )
    return report.render(item, priced, divine_ratio)


if __name__ == "__main__":
    raise SystemExit(main())
