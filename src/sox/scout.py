"""poe2scout index client — the zero-cost half of pricing.

No auth, no key. Their Swagger is misconfigured (it points at the Swagger
petstore demo), so these routes were read from the project source and then
confirmed live. `category` is REQUIRED; omitting it returns HTTP 400.
"""

from __future__ import annotations

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


class ScoutClient:
    def __init__(self, client: httpx.Client, cache: Cache, user_agent: str) -> None:
        self._client = client
        self._cache = cache
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}

    def current_league(self) -> League:
        for entry in self._get("/Leagues"):
            if entry.get("IsCurrent"):
                return League(
                    value=entry["Value"],
                    short=entry["ShortName"],
                    divine_price_ex=float(entry.get("DivinePrice") or 0.0),
                    base_currency=entry.get("BaseCurrencyText", "Exalted Orb"),
                )
        raise RuntimeError("no current league reported by the index")

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
