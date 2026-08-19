"""Command line entry point.

    sox price          price the item text on stdin (paste, then Ctrl-D)
    sox price -f FILE  price every item in a file, separated by blank lines
    sox leagues        show the current league and the divine rate
"""

from __future__ import annotations

import argparse
import sys
import _thread
import threading
from dataclasses import replace
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
from sox.valuation.mods import build_index, explain_score
from sox.valuation.rolls import (
    roll_percentiles,
    roll_percentiles_from_item,
    roll_score,
    roll_score_from_item,
)
from sox.valuation.query import (
    category_for,
    defence_mod_texts,
    explain_selection,
    granted_skill_text,
    searchable_implicits,
    searched_item_texts,
)
from sox.valuation.trade_pricer import price_by_search

ITEM_SEPARATOR = "\n\n"

# How long an item may take before the feed says it is working on it.
SLOW_SEARCH_SECONDS = 0.6


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sox", description="PoE2 item pricer")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--hardcore", action="store_true",
                        help="read the hardcore league instead of softcore")
    sub = parser.add_subparsers(dest="command", required=True)

    price = sub.add_parser("price", help="price item text (Ctrl+C in game, then paste)")
    price.add_argument("-f", "--file", type=Path, help="file of item texts, blank-line separated")
    price.add_argument("--no-trade", action="store_true", help="index only; no trade calls")
    price.add_argument("--force", action="store_true",
                       help="search even items scored as not worth it")

    watcher = sub.add_parser("watch", help="live-price every item you copy")
    watcher.add_argument("--poll", type=int, default=400,
                         help="clipboard poll interval in milliseconds")
    watcher.add_argument("--no-trade", action="store_true",
                         help="index only; no trade calls")
    watcher.add_argument("--force", action="store_true",
                         help="search even items scored as not worth it")

    sub.add_parser("leagues", help="show the current league and divine rate")
    return parser


def _read_items(path: Path | None) -> list[str]:
    raw = path.read_text() if path else sys.stdin.read()
    blocks = [b.strip() for b in raw.split(ITEM_SEPARATOR)]
    return [b for b in blocks if "Item Class:" in b or "Rarity:" in b]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    if getattr(args, "force", False):
        cfg = replace(cfg, force=True)

    cache = Cache(cfg.cache_path)
    scout = ScoutClient(httpx.Client(timeout=30), cache, cfg.user_agent)

    try:
        league = scout.current_league(hardcore=args.hardcore or cfg.hardcore)
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
            def announce(seconds: float, reason: str) -> None:
                print(f"… waiting {seconds:.0f}s ({reason})", file=sys.stderr, flush=True)

            session = GGGSession(RateGovernor(on_wait=announce),
                                 httpx.Client(timeout=30), cfg.user_agent)
            trade = TradeClient(session, cache, cfg.league or league.value)

        for n, block in enumerate(blocks):
            if n:
                print()
            print(price_one(block, index, rates, mod_index, base_rules,
                            unique_rules, notables, trade, cache, cfg))
        return 0
    except (GGGError, httpx.HTTPError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        cache.close()


def _identity_fields(item) -> dict:
    """The game's own label and the trade category we search it under.

    More use than the internal pricing path: the category is what a search
    actually queries, and a wrong one silently returns the wrong market.
    """
    return {"item_class_name": item.get("itemClass") or "",
            "category": category_for(item)}


def _coherence_fields(item, mod_index) -> dict:
    group, count, bonus = candidates.coherence_of(item, mod_index)
    return {"coherence_group": group, "coherence_count": count,
            "coherence_bonus": bonus}


def _best_roll(item, entry) -> float | None:
    """The item's strongest roll, preferring its own advanced descriptions.

    Those carry the range on the same line as the value, so nothing can
    misalign; the index template has to be matched by text.
    """
    percentiles = roll_percentiles_from_item(item)
    if not percentiles and entry is not None:
        percentiles = roll_percentiles(candidates.item_mods(item), entry.metadata)
    return max(percentiles) if percentiles else None


def wants_search(verdict, entry, item, force: bool = False) -> bool:
    """Whether to spend a search on this item.

    Coherence decides WHICH stats to search on, and is reported so you can
    judge the answer — but it no longer decides WHETHER to search. Anything
    the index cannot price gets searched, so copying an item always produces
    a number rather than a verdict about the item's quality.
    """
    if force or verdict.should_search:
        return True
    return entry is None and category_for(item) is not None


def _stop_on_eof(stop: threading.Event) -> None:
    """Set `stop` when stdin reaches EOF, i.e. the user pressed Ctrl-D.

    The watch loop blocks polling the clipboard and never reads stdin, so EOF
    has to be waited on separately and then interrupt the main thread to break
    it out of the poll.
    """
    try:
        while sys.stdin.readline():
            pass
    except (ValueError, OSError):
        return
    stop.set()
    _thread.interrupt_main()


def run_watch(args, cfg, cache, scout, league) -> int:
    """Price every item copied to the clipboard, until Ctrl-D."""
    index = scout.prices(league.short)
    rates = scout.currency_rates(index)
    mod_index = build_index(load_mods())
    base_rules, unique_rules = load_bases(), load_uniques()
    notables = load_notables()

    trade = None
    if not args.no_trade:
        def announce(seconds: float, reason: str) -> None:
            print(watch_ui.waiting_on_limit(seconds, reason), flush=True)

        governor = RateGovernor(on_wait=announce)
        session = GGGSession(governor, httpx.Client(timeout=30), cfg.user_agent)
        trade = TradeClient(session, cache, cfg.league or league.value)

    print(watch_ui.banner(league.value, league.divine_price_ex,
                          clipboard.describe_backend()), flush=True)
    stats = watch_ui.Session()

    # Ctrl+C is how you copy an item in game. Hitting it with the terminal
    # focused instead of the game is a slip that used to end the session, so
    # Ctrl-D stops instead and Ctrl+C only says so. Without a tty there is no
    # Ctrl-D to press, so there Ctrl+C must still work or nothing does.
    stop = threading.Event()
    interactive = sys.stdin.isatty()
    if interactive:
        threading.Thread(target=_stop_on_eof, args=(stop,), daemon=True).start()

    while True:
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
                searching = wants_search(verdict, entry_, item,
                                         force=getattr(cfg, "force", False)) \
                    and trade is not None

                # Most items resolve in one search and print immediately, so
                # announcing "searching…" up front is noise. It earns its place
                # only when the wait is real — a rate-limit block or a walk down
                # the widening ladder — so a timer prints it and is cancelled if
                # the result arrives first.
                announced = threading.Event()

                def announce_slow(item_name: str = name) -> None:
                    announced.set()
                    print(watch_ui.detected(item_name, "searching…"), flush=True)

                timer = threading.Timer(SLOW_SEARCH_SECONDS, announce_slow)
                timer.daemon = True
                if searching:
                    timer.start()

                try:
                    priced = _price_item(item, index, rates, mod_index, base_rules,
                                         unique_rules, notables, trade, cache, cfg)
                except (GGGError, httpx.HTTPError) as exc:
                    # One bad lookup must not end the session; the next copy retries.
                    timer.cancel()
                    print(watch_ui.error(f"{type(exc).__name__}: {exc}"), flush=True)
                    continue
                except Exception as exc:  # noqa: BLE001 - the feed must survive
                    timer.cancel()
                    print(watch_ui.error(f"unexpected {type(exc).__name__}: {exc}"),
                          flush=True)
                    continue
                timer.cancel()

                body = report.render(item, priced, rates)
                stats.record(name, priced.price_ex, priced.searches_used,
                             junk=priced.tag == "junk")
                if announced.is_set():
                    # The header is already on screen; only the detail is left.
                    print(watch_ui.body_lines(body), flush=True)
                else:
                    print(watch_ui.entry(body, priced.price_ex, league.divine_price_ex),
                          flush=True)
                print(watch_ui.status(stats, league.divine_price_ex), flush=True)
        except KeyboardInterrupt:
            if stop.is_set() or not interactive:
                break
            # A slip of the copy key. The generator died with the exception, so
            # the loop restarts — skipping whatever is on the clipboard now, which
            # is either already priced or was never an item.
            print()
            print(watch_ui.interrupted_hint(), flush=True)
            continue
        break

    print()
    print(watch_ui.status(stats, league.divine_price_ex))
    return 0


def price_one(block, index, rates, mod_index, base_rules, unique_rules,
              notables, trade, cache, cfg) -> str:
    item = itemtext.parse(block)
    priced = _price_item(item, index, rates, mod_index, base_rules,
                         unique_rules, notables, trade, cache, cfg)
    return report.render(item, priced, rates)


def _price_item(item, index, rates, mod_index, base_rules, unique_rules,
                notables, trade, cache, cfg) -> report.PricedItem:
    item_class = classify(item)
    entry = index_price_for(item, index)

    verdict = candidates.assess(item, entry, mod_index, base_rules, unique_rules)

    if wants_search(verdict, entry, item, force=getattr(cfg, "force", False)) \
            and trade is not None:
        category = category_for(item)
        if category:
            result = price_by_search(
                item, category, mod_index, notables, trade, cache, rates,
                status=cfg.status, max_searches=cfg.max_searches,
            )
            # Explain the rung that actually produced the price, not rung 0.
            group, stats = explain_selection(item, mod_index, notables,
                                             relax=result.relax_used)
            highlighted = searched_item_texts(item, mod_index, notables,
                                              relax=result.relax_used)
            highlighted += (defence_mod_texts(item) + granted_skill_text(item)
                            + searchable_implicits(item, mod_index))
            return report.PricedItem(
                name=display_name(item), item_class=item_class,
                price_ex=result.ceiling_ex, source="trade" if result.ceiling_ex else "unpriced",
                tag=result.tag, reason=verdict.reason, listings=result.listings,
                matches=result.matches, rune_inflated=result.rune_inflated,
                score=verdict.score,
                breakdown=tuple(candidates.score_rows(item, mod_index, base_rules)),
                **_coherence_fields(item, mod_index), **_identity_fields(item),
                median_ex=result.median_ex, p25_ex=result.p25_ex,
                confidence=result.confidence, skewed=result.skewed,
                relax_used=result.relax_used,
                suggested_ask_ex=result.suggested_ask_ex,
                searches_used=result.searches_used, from_cache=result.from_cache,
                searched_group=group, searched_stats=stats,
                searched_texts=tuple(highlighted),
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
            best_roll_pct=_best_roll(item, entry),
        )

    if item_class is ItemClass.UNKNOWN:
        tag = "unpriced:unknown-class"
    elif not verdict.should_search:
        # Scored too low to be worth one of a limited number of searches.
        # Called junk rather than unpriced: "unpriced" reads as a failure,
        # when the tool in fact reached a confident verdict about the item.
        tag = "junk"
    else:
        tag = "unpriced:no-index"
    return report.PricedItem(
        name=display_name(item), item_class=item_class, price_ex=None,
        source="unpriced", tag=tag, reason=verdict.reason, score=verdict.score,
        breakdown=tuple(candidates.score_rows(item, mod_index, base_rules)),
        **_coherence_fields(item, mod_index), **_identity_fields(item),
    )


if __name__ == "__main__":
    raise SystemExit(main())
