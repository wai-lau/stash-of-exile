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

from sox import clipboard, itemtext, report, watch as watch_ui
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
from sox.valuation.rolls import roll_score, roll_score_from_item
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

    watcher = sub.add_parser("watch", help="live-price every item you copy")
    watcher.add_argument("--poll", type=int, default=400,
                         help="clipboard poll interval in milliseconds")
    watcher.add_argument("--no-trade", action="store_true",
                         help="index only; no trade calls")

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

        if args.command == "watch":
            return run_watch(args, cfg, cache, scout, league)

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


def wants_search(verdict, entry, item) -> bool:
    """Search when escalation calls for it, or when nothing else can price it."""
    if verdict.should_search:
        return True
    return entry is None and category_for(item) is not None


def run_watch(args, cfg, cache, scout, league) -> int:
    """Price every item copied to the clipboard, until interrupted."""
    index = scout.prices(league.short)
    rates = scout.currency_rates(index)
    mod_index = build_index(load_mods())
    base_rules, unique_rules = load_bases(), load_uniques()
    notables = load_notables()

    trade = None
    if not args.no_trade:
        session = GGGSession(RateGovernor(), httpx.Client(timeout=30), cfg.user_agent)
        trade = TradeClient(session, cache, cfg.league or league.value)

    print(watch_ui.banner(league.value, league.divine_price_ex,
                          clipboard.describe_backend()), flush=True)
    stats = watch_ui.Session()

    try:
        for text in clipboard.watch(args.poll):
            if not clipboard.looks_like_item(text):
                continue

            # Acknowledge the item before doing any network work. Deciding
            # whether a search is needed costs nothing, so the header can say
            # which is coming.
            try:
                item = itemtext.parse(text)
            except ValueError:
                continue
            name = display_name(item)
            if item.get("name"):
                name = f"{name}  [{item.get('baseType')}]"
            entry_ = index_price_for(item, index)
            verdict = candidates.assess(item, entry_, mod_index, base_rules, unique_rules)
            searching = wants_search(verdict, entry_, item) and trade is not None
            print(watch_ui.detected(name, "searching…" if searching else "index"),
                  flush=True)

            try:
                priced = _price_item(item, index, rates, mod_index, base_rules,
                                     unique_rules, notables, trade, cache, cfg)
            except (GGGError, httpx.HTTPError) as exc:
                # One bad lookup must not end the session; the next copy retries.
                print(watch_ui.error(str(exc)), flush=True)
                continue

            body = report.render(item, priced, league.divine_price_ex)
            stats.record(name, priced.price_ex, priced.searches_used)
            print(watch_ui.body_lines(body), flush=True)
            print(watch_ui.status(stats, league.divine_price_ex), flush=True)
    except KeyboardInterrupt:
        print()
        print(watch_ui.status(stats, league.divine_price_ex))
    return 0


def price_one(block, index, rates, mod_index, base_rules, unique_rules,
              notables, trade, cache, cfg, divine_ratio) -> str:
    item = itemtext.parse(block)
    priced = _price_item(item, index, rates, mod_index, base_rules,
                         unique_rules, notables, trade, cache, cfg)
    return report.render(item, priced, divine_ratio)


def _price_item(item, index, rates, mod_index, base_rules, unique_rules,
                notables, trade, cache, cfg) -> report.PricedItem:
    item_class = classify(item)
    entry = index_price_for(item, index)

    verdict = candidates.assess(item, entry, mod_index, base_rules, unique_rules)

    # The score decides whether an item is WORTH listing, not whether to
    # answer. Copying an item is a request for its price, so anything the
    # index cannot price gets searched regardless of how it scored — refusing
    # would leave the one question that was actually asked unanswered.
    if wants_search(verdict, entry, item) and trade is not None:
        category = category_for(item)
        if category:
            result = price_by_search(
                item, category, mod_index, notables, trade, cache, rates,
                status=cfg.status, max_searches=cfg.max_searches,
            )
            # Explain the rung that actually produced the price, not rung 0.
            group, stats = explain_selection(item, mod_index, notables,
                                             relax=result.relax_used)
            return report.PricedItem(
                name=display_name(item), item_class=item_class,
                price_ex=result.ceiling_ex, source="trade" if result.ceiling_ex else "unpriced",
                tag=result.tag, reason=verdict.reason, listings=result.listings,
                median_ex=result.median_ex, p25_ex=result.p25_ex,
                confidence=result.confidence, skewed=result.skewed,
                relax_used=result.relax_used,
                suggested_ask_ex=result.suggested_ask_ex,
                searches_used=result.searches_used,
                searched_group=group, searched_stats=stats,
            )

    if entry is not None:
        # Free to compute from the item's own advanced descriptions, and it is
        # what tells you whether the index's single number describes YOUR copy.
        roll_pct = roll_score_from_item(item)
        if roll_pct is None:
            roll_pct = roll_score(candidates.item_mods(item), entry.metadata)
        return report.PricedItem(
            name=display_name(item), item_class=item_class, price_ex=entry.price_ex,
            source="index", tag=None, reason=verdict.reason, quantity=entry.quantity,
            roll_pct=roll_pct,
        )

    tag = "unpriced:unknown-class" if item_class is ItemClass.UNKNOWN else "unpriced:no-index"
    return report.PricedItem(
        name=display_name(item), item_class=item_class, price_ex=None,
        source="unpriced", tag=tag, reason=verdict.reason,
    )


if __name__ == "__main__":
    raise SystemExit(main())
