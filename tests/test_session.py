"""The single door to GGG: rate discipline and error clarity.

No test existed for this, so the 429 retry path and the error mapping had
never been exercised.
"""

import httpx
import pytest

from sox.ggg.governor import RateGovernor
from sox.ggg.session import Blocked, GGGError, GGGSession, RateLimited


def build(handler, sleeper=None):
    gov = RateGovernor(clock=lambda: 0.0, sleeper=sleeper or (lambda s: None))
    return GGGSession(gov, httpx.Client(transport=httpx.MockTransport(handler)),
                      user_agent="sox-test"), gov


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

    session, gov = build(handler)
    session.get("https://x/")
    assert {r.limit for r in gov.rules} == {5, 15}
