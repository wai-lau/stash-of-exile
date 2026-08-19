"""Rate governor: it must pace itself, and say when it is doing so.

Real limits captured live 2026-08-18:
    x-rate-limit-ip: 5:10:60,15:60:300,30:300:1800,600:21600:3600
Four clauses enforced at once, so one rule can carry several.
"""

from sox.ggg.governor import RateGovernor


def make(clock_start=0.0):
    now = [clock_start]
    slept = []

    def sleeper(seconds):
        slept.append(seconds)
        now[0] += seconds

    return RateGovernor(clock=lambda: now[0], sleeper=sleeper), now, slept


def test_no_wait_before_any_headers_are_seen():
    gov, _, slept = make()
    gov.before_request()
    assert slept == []


def test_parses_several_clauses_from_one_rule():
    gov, _, _ = make()
    gov.observe({
        "X-Rate-Limit-Rules": "Ip",
        "X-Rate-Limit-Ip": "5:10:60,15:60:300,30:300:1800",
        "X-Rate-Limit-Ip-State": "1:10:0,1:60:0,1:300:0",
    })
    assert len(gov.rules) == 3, "each clause is its own limit"
    assert {r.limit for r in gov.rules} == {5, 15, 30}


def test_blocks_before_breaching_the_tightest_clause():
    gov, _, slept = make()
    gov.observe({
        "X-Rate-Limit-Rules": "Ip",
        "X-Rate-Limit-Ip": "2:10:30",
        "X-Rate-Limit-Ip-State": "0:10:0",
    })
    for _ in range(2):
        gov.before_request()
        gov.record_request()
    assert slept == []
    gov.before_request()
    assert slept and slept[0] > 0, "must block rather than breach"


def test_window_slides_so_old_requests_stop_counting():
    gov, now, slept = make()
    gov.observe({
        "X-Rate-Limit-Rules": "Ip",
        "X-Rate-Limit-Ip": "2:10:30",
        "X-Rate-Limit-Ip-State": "0:10:0",
    })
    for _ in range(2):
        gov.before_request()
        gov.record_request()
    now[0] += 11
    gov.before_request()
    assert slept == []


def test_malformed_headers_are_ignored_not_fatal():
    gov, _, slept = make()
    gov.observe({"X-Rate-Limit-Rules": "Ip", "X-Rate-Limit-Ip": "garbage"})
    gov.before_request()
    assert slept == []




def test_announces_waits_so_it_does_not_look_hung():
    """Waiting silently is indistinguishable from a hang."""
    now = [0.0]
    announced = []
    gov = RateGovernor(
        clock=lambda: now[0],
        sleeper=lambda s: now.__setitem__(0, now[0] + s),
        on_wait=lambda seconds, reason: announced.append((round(seconds), reason)),
    )
    gov.observe({
        "X-Rate-Limit-Rules": "Ip",
        "X-Rate-Limit-Ip": "2:10:30",
        "X-Rate-Limit-Ip-State": "0:10:0",
    })
    for _ in range(2):
        gov.before_request()
        gov.record_request()
    gov.before_request()
    assert announced, "a wait must be announced"
    assert announced[0][1] == "rate limit"


def test_announces_429_backoff():
    announced = []
    gov = RateGovernor(clock=lambda: 0.0, sleeper=lambda s: None,
                       on_wait=lambda s, r: announced.append(r))
    gov.on_429(retry_after=7.0)
    gov.on_429(retry_after=None)
    assert "429, server asked us to wait" in announced
    assert "429, backing off" in announced
