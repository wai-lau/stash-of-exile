"""The single door to GGG. Nothing above this module may bypass it.

The trade API needs no authentication — verified end to end with a real
search and fetch — so this holds no credentials and has no auth failure
paths. Its whole job is rate discipline and error clarity.

Rate discipline is per endpoint. Search, fetch and exchange each answer
their own `x-rate-limit-policy` with their own budget — the -state counters
on a search do not move across fetches — so each gets a governor of its
own, keyed by the trade2 path segment. One governor for all of them charged
every fetch to the search window and gated each call by whichever endpoint
answered last: half the searches GGG allows, spent on pacing the wrong thing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from sox.ggg.governor import SEARCH_DOWN_AFTER, Budget, RateGovernor

MAX_429_RETRIES = 3
TRADE2 = "/api/trade2/"


class GGGError(Exception):
    """Base for every GGG transport failure."""


class Blocked(GGGError):
    """Cloudflare or GGG refused the request outright."""


class RateLimited(GGGError):
    pass


class SearchDown(RateLimited):
    """The bucket is in a lockout longer than SEARCH_DOWN_AFTER: the call was
    refused rather than slept, and `seconds` says how long is left."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"locked out for {seconds:.0f}s")
        self.seconds = seconds


def bucket(url: str) -> str:
    """The rate-limit bucket a URL is charged to: `search`, `fetch`,
    `exchange`, `data` — the first path segment under /api/trade2/."""
    path = httpx.URL(url).path
    if TRADE2 in path:
        return path.split(TRADE2, 1)[1].split("/", 1)[0] or "other"
    return "other"


class GGGSession:
    def __init__(
        self,
        governors: Callable[[str], RateGovernor],
        client: httpx.Client,
        user_agent: str,
    ) -> None:
        self._new_governor = governors
        self._governors: dict[str, RateGovernor] = {}
        self._client = client
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}

    def get(self, url: str, **kw: Any) -> httpx.Response:
        return self._request("GET", url, **kw)

    def post(self, url: str, json: Any = None, **kw: Any) -> httpx.Response:
        return self._request("POST", url, json=json, **kw)

    def budget(self, bucket: str) -> Budget | None:
        """What the bucket has left, or None before it has answered once."""
        governor = self._governors.get(bucket)
        return governor.budget() if governor else None

    def down(self, bucket: str) -> float:
        """Seconds of lockout left on the bucket, 0 when a call would go."""
        governor = self._governors.get(bucket)
        wait = governor.wait() if governor else 0.0
        return wait if wait >= SEARCH_DOWN_AFTER else 0.0

    def _governor(self, url: str) -> RateGovernor:
        name = bucket(url)
        if name not in self._governors:
            self._governors[name] = self._new_governor(name)
        return self._governors[name]

    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        headers = {**self._headers, **kw.pop("headers", {})}
        governor = self._governor(url)

        for attempt in range(MAX_429_RETRIES + 1):
            wait = governor.wait()
            if wait >= SEARCH_DOWN_AFTER:
                raise SearchDown(wait)
            governor.before_request()
            governor.record_request()
            response = self._client.request(
                method, url, headers=headers, follow_redirects=False, **kw
            )
            governor.observe(response.headers)

            if response.status_code == 429:
                if attempt == MAX_429_RETRIES:
                    raise RateLimited("rate limited by GGG after repeated backoff")
                retry_after = response.headers.get("Retry-After")
                if not governor.on_429(float(retry_after) if retry_after else None):
                    raise SearchDown(governor.wait())
                continue

            governor.on_success()
            self._check(response)
            return response

        raise RateLimited("unreachable")  # pragma: no cover

    def _check(self, response: httpx.Response) -> None:
        if response.status_code == 403:
            raise Blocked(
                "403 from GGG/Cloudflare. The trade API needs no login, so this is "
                "usually rate limiting or a blocked User-Agent rather than auth."
            )
        if response.status_code >= 400:
            raise GGGError(f"HTTP {response.status_code} from {response.url}: "
                           f"{response.text[:300]}")
