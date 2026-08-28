"""poe2scout index client — the zero-cost half of pricing.

No auth, no key. Their Swagger is misconfigured (it points at the Swagger
petstore demo), so these routes were read from the project source and then
confirmed live. `category` is REQUIRED; omitting it returns HTTP 400.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import httpx

from sox.cache import TTL, Cache

BASE = "https://api.poe2scout.com/poe2"
PER_PAGE = 100

CURRENCY_CATEGORIES = (
    "currency", "fragments", "runes", "essences", "ultimatum", "expedition",
    "ritual", "vaultkeys", "breach", "abyss", "uncutgems", "lineagesupportgems",
    "delirium", "incursion", "idol", "verisium", "vaal",
)
UNIQUE_CATEGORIES = ("accessory", "armour", "flask", "jewel", "map", "weapon", "sanctum")

# Trade listing currency ids -> the index name that prices them. Anything not
# listed here is looked up by its own name.
CURRENCY_ALIASES = {
    "exalted": "Exalted Orb",
    "divine": "Divine Orb",
    "chaos": "Chaos Orb",
    "alch": "Orb of Alchemy",
    "alt": "Orb of Alteration",
    "aug": "Orb of Augmentation",
    "transmute": "Orb of Transmutation",
    "regal": "Regal Orb",
    "vaal": "Vaal Orb",
    "annul": "Orb of Annulment",
    "chance": "Orb of Chance",
    "mirror": "Mirror of Kalandra",
}


def is_hardcore(entry: dict) -> bool:
    """Whether a league entry is a hardcore one.

    The index marks it three ways and only inconsistently: temporary leagues
    prefix the name ("HC Runes of Aldur") and suffix the short name
    ("runeshc"), while the permanent one is just "Hardcore" and shortens to
    "hardcore", which carries neither marker.
    """
    value = entry.get("Value") or ""
    short = (entry.get("ShortName") or "").casefold()
    return value.startswith("HC ") or value == "Hardcore" or short.endswith("hc")


@dataclass(frozen=True)
class League:
    value: str
    short: str
    divine_price_ex: float
    base_currency: str


@dataclass(frozen=True)
class IndexEntry:
    name: str
    price_ex: float
    quantity: int
    metadata: dict


# poe2scout takes a snapshot an hour, so a read within the hour is up to an
# hour old by nature, and a second hour is slack for their side. Past three
# the snapshots have stopped: they did for a day on 2026-08-27, and a fresh
# fetch of a day-old snapshot priced Hinekora's Lock at 1,395 div against a
# board asking 1,643. The fetch time says nothing; the snapshot's own does.
STALE_SNAPSHOT_S = 3 * 3600


@dataclass(frozen=True)
class Snapshot:
    """One hour of the game's exchange: each currency's fills, and when.

    `epoch` is the snapshot's own timestamp. None when the epoch endpoint
    did not answer — the prices stand, the age is unknown. Reads like the
    dict it wraps where the pricer reads it: `get`, `[]` and truth.
    """

    fills: dict[str, tuple[float, float]]
    epoch: float | None = None

    def get(self, name: str, default=None):
        return self.fills.get(name, default)

    def __getitem__(self, name: str) -> tuple[float, float]:
        return self.fills[name]

    def __bool__(self) -> bool:
        return bool(self.fills)

    def age(self, now: float | None = None) -> float | None:
        """Seconds since the snapshot was taken; None when that is unknown."""
        if self.epoch is None:
            return None
        return max(0.0, (time.time() if now is None else now) - self.epoch)


def describe_age(seconds: float) -> str:
    """An age as a reader says it: 45min, 29h, 3d."""
    if seconds < 3600:
        return f"{int(seconds // 60)}min"
    if seconds < 48 * 3600:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _snapshot_from_cache(cached) -> Snapshot:
    if not cached:
        return Snapshot({}, None)
    if "fills" not in cached:   # the shape before the epoch rode along
        return Snapshot({k: (v[0], v[1]) for k, v in cached.items()}, None)
    return Snapshot({k: (v[0], v[1]) for k, v in cached["fills"].items()},
                    cached.get("epoch"))


class ScoutClient:
    def __init__(self, client: httpx.Client, cache: Cache, user_agent: str) -> None:
        self._client = client
        self._cache = cache
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}

    def current_league(self, hardcore: bool = False) -> League:
        """The league in play, softcore unless asked otherwise.

        TWO entries report IsCurrent at once — a league and its hardcore twin
        — so taking the first one resolves by list order rather than by logic.
        Live on 2026-08-19 that order put softcore first, which is luck: HC
        prices are close enough (divine 361.68 against 358.07) that reading
        the wrong book would not look wrong anywhere in the output.
        """
        for entry in self._get("/Leagues"):
            if not entry.get("IsCurrent"):
                continue
            if is_hardcore(entry) != hardcore:
                continue
            return League(
                value=entry["Value"],
                short=entry["ShortName"],
                divine_price_ex=float(entry.get("DivinePrice") or 0.0),
                base_currency=entry.get("BaseCurrencyText", "Exalted Orb"),
            )
        wanted = "hardcore" if hardcore else "softcore"
        raise RuntimeError(f"no current {wanted} league reported by the index")

    def prices(self, league: str) -> dict[str, IndexEntry]:
        """Every indexable item, keyed by display name.

        `Text` is preferred over `Name` because it carries the distinguishing
        detail for level-priced items: "Uncut Skill Gem (Level 20)" and
        "(Level 4)" share a Name but differ ~8x in price.
        """
        cached = self._cache.get("index_price", league)
        if cached is not None:
            return {k: IndexEntry(**v) for k, v in cached.items()}

        merged: dict[str, IndexEntry] = {}
        for kind, categories in (
            ("Currencies", CURRENCY_CATEGORIES),
            ("Uniques", UNIQUE_CATEGORIES),
        ):
            for category in categories:
                for item in self._all_pages(
                    f"/Leagues/{league}/{kind}/ByCategory", category
                ):
                    price = item.get("CurrentPrice")
                    text, short = item.get("Text"), item.get("Name")
                    if price is None or not (text or short):
                        continue
                    entry = IndexEntry(
                        name=short or text,
                        price_ex=float(price),
                        quantity=int(item.get("CurrentQuantity") or 0),
                        metadata=item.get("ItemMetadata") or {},
                    )
                    # Index under BOTH forms. Uniques report Text as
                    # "Mageblood Utility Belt" (name + base) while the item we
                    # hold is named just "Mageblood"; gems need the Text form
                    # because it carries the level, as in
                    # "Uncut Skill Gem (Level 20)".
                    for key in (text, short):
                        if key:
                            merged.setdefault(key, entry)

        self._cache.put(
            "index_price", league,
            {k: v.__dict__ for k, v in merged.items()},
            ttl=TTL["index_price"],
        )
        return merged

    def _all_pages(self, path: str, category: str) -> list[dict]:
        """Every page of a category, not just the first.

        Categories are larger than one page: armour alone holds 227 uniques
        across 3 pages, so fetching only page 1 silently loses more than half
        of them and prices those items as "no index".
        """
        items: list[dict] = []
        page = 1
        while True:
            payload = self._get(
                path, params={"category": category, "perPage": PER_PAGE, "page": page}
            )
            batch = payload.get("Items") or []
            items.extend(batch)
            total_pages = int(payload.get("Pages") or 1)
            if page >= total_pages or not batch:
                return items
            page += 1

    def cached_fills(self, league_short: str) -> Snapshot:
        """The last snapshot the cache holds, however old — the figures a
        watch session starts on while `exchange_fills(refresh=True)` runs
        behind it. Empty when nothing was ever cached."""
        return _snapshot_from_cache(self._cache.peek("exchange_fills", league_short))

    def exchange_fills(self, league_short: str, *, refresh: bool = False) -> Snapshot:
        """Display name -> (price in exalted, exalted traded), read off the
        game's own Currency Exchange, with the snapshot's own timestamp.

        poe2scout snapshots the in-game exchange hourly. The endpoint is
        undocumented — read out of their frontend bundle and confirmed live —
        so an empty answer here is survivable by design: everything falls
        back to the trade-site books.

        A fill cannot be faked: an offer nobody takes never appears here,
        which is what separates Masterwork Rune's 38,000 ex of real trades
        from the 748 one-unit bait listings on its trade-site book.

        RelativePrice is quoted in a unit of the snapshot's own — Exalted Orb
        itself reports ~0.91 — so every figure is normalised by exalted's
        own, which lands divine at 361.8 ex right where the trade-site book
        independently puts it. Each currency takes its figure from the pair
        it traded the most value in.

        `refresh` skips the cache read: the snapshot is hourly and poe2scout
        sets no rate limit, so the fetch a session starts in the background
        always asks, and the cache only seeds the wait.
        """
        cached = None if refresh else self._cache.get("exchange_fills", league_short)
        if cached is not None:
            return _snapshot_from_cache(cached)

        best: dict[str, tuple[float, float]] = {}
        try:
            pairs = self._get(f"/Leagues/{league_short}/SnapshotPairs")
        except (httpx.HTTPError, ValueError):
            return Snapshot({}, None)
        for pair in pairs or []:
            for side in ("CurrencyOne", "CurrencyTwo"):
                name = (pair.get(side) or {}).get("Text")
                data = pair.get(f"{side}Data") or {}
                price = data.get("RelativePrice")
                traded = float(data.get("ValueTraded") or 0.0)
                if not name or not price:
                    continue
                if name not in best or traded > best[name][1]:
                    best[name] = (float(price), traded)

        unit = best.get("Exalted Orb", (0.0, 0.0))[0]
        if not unit:
            return Snapshot({}, None)
        fills = {name: (price / unit, traded / unit)
                 for name, (price, traded) in best.items()}
        epoch = self._snapshot_epoch(league_short)
        self._cache.put(
            "exchange_fills", league_short,
            {"epoch": epoch, "fills": {k: list(v) for k, v in fills.items()}},
            ttl=TTL["exchange_fills"],
        )
        return Snapshot(fills, epoch)

    def _snapshot_epoch(self, league_short: str) -> float | None:
        """When the snapshot the pairs came from was taken — its own clock,
        not ours. As undocumented as the pairs; losing it loses the age
        warning, not the prices."""
        try:
            payload = self._get(f"/Leagues/{league_short}/ExchangeSnapshot")
        except (httpx.HTTPError, ValueError):
            return None
        epoch = payload.get("Epoch") if isinstance(payload, dict) else None
        return float(epoch) if epoch else None

    def currency_rates(self, index: dict[str, IndexEntry]) -> dict[str, float]:
        """Trade currency id -> value in exalted."""
        rates = {"exalted": 1.0}
        for code, index_name in CURRENCY_ALIASES.items():
            entry = index.get(index_name)
            if entry is not None:
                rates[code] = entry.price_ex
        return rates

    def _get(self, path: str, params: dict | None = None):
        response = self._client.get(BASE + path, headers=self._headers, params=params)
        response.raise_for_status()
        return response.json()


class LiveFills:
    """The exchange fills as a watch session sees them.

    A session can run for hours on the snapshot it read at startup, and
    waiting on that read before the first item prices is the wrong trade
    too. So the session starts on whatever the cache last held — stale beats
    nothing — and a fetch runs behind it; the loop takes the fresh figures
    between items, where the rates that hang off them are recomputed in the
    same thread that reads them. An empty or failed fetch is not news and
    leaves the old figures standing.

    Reads what the pricer reads of a dict: `get` and truth — and its age.
    """

    def __init__(self, current: Snapshot) -> None:
        self._current = current
        self._pending: Snapshot | None = None
        self._lock = threading.Lock()

    def get(self, name: str, default=None):
        return self._current.get(name, default)

    def __bool__(self) -> bool:
        return bool(self._current)

    @property
    def epoch(self) -> float | None:
        return self._current.epoch

    def age(self, now: float | None = None) -> float | None:
        return self._current.age(now)

    def refresh(self, fetch) -> threading.Thread:
        """Run `fetch` on a thread; its answer waits for `take_update`."""
        def run() -> None:
            try:
                fresh = fetch()
            except Exception:  # noqa: BLE001 - the session must not care
                return
            if fresh:
                with self._lock:
                    self._pending = fresh

        worker = threading.Thread(target=run, name="exchange-fills", daemon=True)
        worker.start()
        return worker

    def take_update(self) -> bool:
        """Swap in a landed fetch; True when the figures changed."""
        with self._lock:
            fresh, self._pending = self._pending, None
        if fresh is None:
            return False
        self._current = fresh
        return True
