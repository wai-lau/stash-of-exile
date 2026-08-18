"""trade2 search/fetch. Unofficial, Cloudflare-fronted, rate limited, no auth."""

from __future__ import annotations

from dataclasses import dataclass

from sox.cache import TTL, Cache
from sox.ggg.session import GGGSession

BASE = "https://www.pathofexile.com/api/trade2"
FETCH_BATCH = 10  # the fetch endpoint accepts at most 10 hashes per call


@dataclass(frozen=True)
class Listing:
    amount: float   # raw, in `currency` — NOT exalted
    currency: str   # observed: exalted, divine, chaos, transmute, ...
    account: str

    def to_exalted(self, rates: dict[str, float]) -> float | None:
        """Convert to exalted using the index currency table.

        Sellers price in whatever currency they like; comparing raw amounts
        would rank a 2-transmute item above a 1-divine one.
        """
        rate = rates.get(self.currency)
        return None if rate is None else self.amount * rate


class TradeClient:
    def __init__(self, session: GGGSession, cache: Cache, league: str) -> None:
        self._session = session
        self._cache = cache
        self._league = league

    def search(self, query: dict) -> tuple[str, list[str]]:
        payload = self._session.post(
            f"{BASE}/search/poe2/{self._league}", json=query
        ).json()
        return payload.get("id", ""), payload.get("result", [])

    def fetch(self, query_id: str, hashes: list[str]) -> list[Listing]:
        listings: list[Listing] = []
        for start in range(0, len(hashes), FETCH_BATCH):
            batch = hashes[start : start + FETCH_BATCH]
            payload = self._session.get(
                f"{BASE}/fetch/{','.join(batch)}", params={"query": query_id}
            ).json()
            for result in payload.get("result") or []:
                listing = (result or {}).get("listing") or {}
                price = listing.get("price")
                if not price or price.get("amount") is None:
                    continue  # no buyout: not a usable data point
                listings.append(
                    Listing(
                        amount=float(price["amount"]),
                        currency=price.get("currency", ""),
                        account=(listing.get("account") or {}).get("name", ""),
                    )
                )
        return listings

    def stats(self) -> dict:
        return self._cached("stats_data", "/data/stats")

    def filters(self) -> dict:
        return self._cached("filters_data", "/data/filters")

    def _cached(self, table: str, path: str) -> dict:
        cached = self._cache.get(table, path)
        if cached is not None:
            return cached
        payload = self._session.get(BASE + path).json()
        self._cache.put(table, path, payload, ttl=TTL[table])
        return payload
