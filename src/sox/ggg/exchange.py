"""Bulk currency exchange — the deep book the index never checks.

Currency never reaches the item search, so nothing cross-checked the index
and it drifted unchallenged: Omen of the Sovereign was indexed at 26.5 ex off
31 listings while the exchange held 1303 offers at one exalted each. This is
the same trade2 host as the item search, so it goes through GGGSession and
pays the same rate discipline.
"""

from __future__ import annotations

from dataclasses import dataclass

from sox.cache import TTL, Cache
from sox.ggg.session import GGGSession

BASE = "https://www.pathofexile.com/api/trade2"

# NOT "online". The book for Omen of the Sovereign is 1303 offers deep at
# "any" and 5 at "online": PoE2 trade is asynchronous and most of the market
# is offline at any moment, exactly as it is for the item search.
STATUS = "any"


@dataclass(frozen=True)
class Offer:
    ratio: float   # exalted paid per ONE unit received
    stock: int     # units the seller actually holds, which is the depth


@dataclass(frozen=True)
class Book:
    offers: list[Offer]   # the cheapest page, which is the end that prices
    total: int            # how many offers exist, which is the real breadth


class ExchangeClient:
    def __init__(self, session: GGGSession, cache: Cache, league: str) -> None:
        self._session = session
        self._cache = cache
        self._league = league

    def ids(self) -> dict[str, str]:
        """Display name -> exchange id, across every group.

        ~780 ids over 14 groups, which covers every category the index files
        under "currency" and adds waystones, which it does not price at all.
        """
        cached = self._cache.get("exchange_static", "ids")
        if cached is not None:
            return cached

        payload = self._session.get(f"{BASE}/data/static").json()
        ids: dict[str, str] = {}
        for group in payload.get("result") or []:
            for entry in group.get("entries") or []:
                text, entry_id = entry.get("text"), entry.get("id")
                if text and entry_id:
                    ids.setdefault(text, entry_id)
        self._cache.put("exchange_static", "ids", ids, ttl=TTL["exchange_static"])
        return ids

    def book(self, item_id: str, have: str = "exalted") -> Book:
        """Offers to sell `item_id`, cheapest first, priced in `have`.

        One side only. This is the ASK side — what the thing costs to buy —
        and it is read cheapest-first, so pagination keeps the end that
        matters and drops the 11,000-exalted tail nobody trades at.
        """
        cached = self._cache.get("exchange_book", f"{self._league}/{have}/{item_id}")
        if cached is not None:
            return Book([Offer(**o) for o in cached["offers"]], cached["total"])

        query = {
            "query": {"status": {"option": STATUS}, "have": [have], "want": [item_id]},
            "sort": {"have": "asc"},
            "engine": "new",
        }
        payload = self._session.post(
            f"{BASE}/exchange/{self._league}", json=query
        ).json()

        offers: list[Offer] = []
        for entry in (payload.get("result") or {}).values():
            for offer in ((entry or {}).get("listing") or {}).get("offers") or []:
                paid = (offer.get("exchange") or {}).get("amount")
                got = (offer.get("item") or {}).get("amount")
                if not paid or not got:
                    continue
                # A bundle is how a sub-exalted price is expressed at all:
                # one exalted for 40 wisdom scrolls is 0.025 each.
                offers.append(Offer(
                    ratio=float(paid) / float(got),
                    stock=int((offer.get("item") or {}).get("stock") or 1),
                ))
        offers.sort(key=lambda o: o.ratio)
        # `total` counts the whole book; `offers` is only the page the API
        # returned. Cheapest-first means the page kept is the end that sets
        # the price, but the count must not pretend the book is 100 deep.
        book = Book(offers, int(payload.get("total") or len(offers)))
        self._cache.put(
            "exchange_book", f"{self._league}/{have}/{item_id}",
            {"offers": [o.__dict__ for o in offers], "total": book.total},
            ttl=TTL["exchange_book"],
        )
        return book
