"""The single door to GGG. Nothing above this module may bypass it.

The trade API needs no authentication — verified end to end with a real
search and fetch — so this holds no credentials and has no auth failure
paths. Its whole job is rate discipline and error clarity.
"""

from __future__ import annotations

from typing import Any

import httpx

from sox.ggg.governor import RateGovernor

MAX_429_RETRIES = 3


class GGGError(Exception):
    """Base for every GGG transport failure."""


class Blocked(GGGError):
    """Cloudflare or GGG refused the request outright."""


class RateLimited(GGGError):
    pass


class GGGSession:
    def __init__(
        self,
        governor: RateGovernor,
        client: httpx.Client,
        user_agent: str,
    ) -> None:
        self._governor = governor
        self._client = client
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}

    def get(self, url: str, **kw: Any) -> httpx.Response:
        return self._request("GET", url, **kw)

    def post(self, url: str, json: Any = None, **kw: Any) -> httpx.Response:
        return self._request("POST", url, json=json, **kw)

    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        headers = {**self._headers, **kw.pop("headers", {})}

        for attempt in range(MAX_429_RETRIES + 1):
            self._governor.before_request()
            self._governor.record_request()
            response = self._client.request(
                method, url, headers=headers, follow_redirects=False, **kw
            )
            self._governor.observe(response.headers)

            if response.status_code == 429:
                if attempt == MAX_429_RETRIES:
                    raise RateLimited("rate limited by GGG after repeated backoff")
                retry_after = response.headers.get("Retry-After")
                self._governor.on_429(float(retry_after) if retry_after else None)
                continue

            self._governor.on_success()
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
