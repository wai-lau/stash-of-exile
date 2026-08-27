"""The single door to GGG: rate discipline and error clarity.

No test existed for this, so the 429 retry path and the error mapping had
never been exercised.
"""

import httpx
import pytest

from sox.ggg.governor import RateGovernor
from sox.ggg.session import Blocked, GGGError, GGGSession, RateLimited


SEARCH = "https://www.pathofexile.com/api/trade2/search/poe2/Runes%20of%20Aldur"
FETCH = "https://www.pathofexile.com/api/trade2/fetch/abc,def"
EXCHANGE = "https://www.pathofexile.com/api/trade2/exchange/Runes%20of%20Aldur"
STATIC = "https://www.pathofexile.com/api/trade2/data/static"


def build(handler, sleeper=None):
    """A session whose governors are handed out per bucket and kept for
    inspection, keyed by the bucket name the session asked for."""
    governors = {}

    def factory(name):
        governors[name] = RateGovernor(clock=lambda: 0.0,
                                       sleeper=sleeper or (lambda s: None))
        return governors[name]

    return GGGSession(factory, httpx.Client(transport=httpx.MockTransport(handler)),
                      user_agent="sox-test"), governors


def limited(policy, clauses):
    return httpx.Response(200, json={}, headers={
        "X-Rate-Limit-Policy": policy,
        "X-Rate-Limit-Rules": "Ip",
        "X-Rate-Limit-Ip": clauses,
    })


def test_sends_the_user_agent_and_no_credentials():
    """The trade API needs no session, so nothing may leak into a request."""
    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("user-agent")
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json={"ok": True})

    session, _ = build(handler)
    session.get("https://www.pathofexile.com/api/trade2/data/stats")
    assert seen["ua"] == "sox-test"
    assert seen["cookie"] is None, "sox holds no credentials"


def test_retries_after_a_429_and_honours_retry_after():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"ok": True})

    slept = []
    session, _ = build(handler, sleeper=slept.append)
    assert session.get("https://x/").status_code == 200
    assert calls["n"] == 2
    assert 2 in slept


def test_gives_up_after_repeated_429s():
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "1"})

    session, _ = build(handler)
    with pytest.raises(RateLimited):
        session.get("https://x/")


def test_403_is_reported_as_blocked_not_as_auth():
    """The trade API needs no login, so a 403 is rate limiting or a UA block."""
    session, _ = build(lambda request: httpx.Response(403, text="cloudflare"))
    with pytest.raises(Blocked) as exc:
        session.get("https://x/")
    assert "login" in str(exc.value).lower()


def test_other_errors_carry_the_status_and_body():
    session, _ = build(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(GGGError) as exc:
        session.get("https://x/")
    assert "500" in str(exc.value)
    assert "boom" in str(exc.value)


def test_learns_the_rate_rules_from_a_live_response():
    """Limits are advertised only on responses; the first call teaches them."""
    def handler(request):
        return httpx.Response(200, json={}, headers={
            "X-Rate-Limit-Rules": "Ip",
            "X-Rate-Limit-Ip": "5:10:60,15:60:300",
            "X-Rate-Limit-Ip-State": "1:10:0,1:60:0",
        })

    session, governors = build(handler)
    session.get(STATIC)
    assert {r.limit for r in governors["data"].rules} == {5, 15}


# Live 2026-08-27, one search then two fetches then a search:
#
#     search  x-rate-limit-policy: trade-search-request-limit
#             x-rate-limit-ip:     5:10:60,15:60:300,30:300:1800,600:21600:3600
#             x-rate-limit-ip-state: 1:10:0 ... then 2:10:0
#     fetch   x-rate-limit-policy: trade-fetch-request-limit
#             x-rate-limit-ip:     12:4:10,16:12:300,50:300:300,1000:21600:1800
#             x-rate-limit-ip-state: 1:4:0 ... then 2:4:0
#
# The search counter went 1 -> 2 across two fetches: each endpoint has its
# own budget. One governor for both charged every fetch to the search window
# and gated each call by whichever endpoint answered last.

def by_endpoint(request):
    path = request.url.path
    if "/search/" in path:
        return limited("trade-search-request-limit", "2:10:60")
    if "/fetch/" in path:
        return limited("trade-fetch-request-limit", "3:10:60")
    if "/exchange/" in path:
        return limited("trade-exchange-request-limit", "5:15:60")
    return httpx.Response(200, json={})


def test_a_fetch_is_not_charged_to_the_search_window():
    """Two searches around two fetches is within 2:10:60 for search."""
    slept = []
    session, _ = build(by_endpoint, sleeper=slept.append)
    session.post(SEARCH, json={})
    session.get(FETCH)
    session.get(FETCH)
    session.post(SEARCH, json={})
    assert slept == [], "fetches were counted against the search window"


def test_a_fetch_is_not_gated_by_the_search_rules():
    """Search allows one per 10s; fetch allows twelve per 4s. Two fetches
    straight after a search are what the fetch policy permits."""
    def handler(request):
        if "/search/" in request.url.path:
            return limited("trade-search-request-limit", "1:10:60")
        return limited("trade-fetch-request-limit", "12:4:10")

    slept = []
    session, _ = build(handler, sleeper=slept.append)
    session.post(SEARCH, json={})
    session.get(FETCH)
    session.get(FETCH)
    assert slept == [], "a fetch waited on the search rule"


def test_each_endpoint_gets_its_own_governor():
    session, governors = build(by_endpoint)
    session.post(SEARCH, json={})
    session.get(FETCH)
    session.post(EXCHANGE, json={})
    session.get(STATIC)
    assert set(governors) == {"search", "fetch", "exchange", "data"}
    assert {r.limit for r in governors["search"].rules} == {2}
    assert {r.limit for r in governors["fetch"].rules} == {3}
    assert {r.limit for r in governors["exchange"].rules} == {5}
