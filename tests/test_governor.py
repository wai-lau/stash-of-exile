"""Rate governor: it must pace itself, and say when it is doing so.

Real limits captured live 2026-08-18:
    x-rate-limit-ip: 5:10:60,15:60:300,30:300:1800,600:21600:3600
Four clauses enforced at once, so one rule can carry several.
"""

from sox.ggg.governor import Budget, RateGovernor


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


def test_a_named_governor_says_which_bucket_is_waiting():
    """Search and fetch each have a governor now; a bare "rate limit" no
    longer says which one is pacing."""
    now = [0.0]
    announced = []
    gov = RateGovernor(
        name="search",
        clock=lambda: now[0],
        sleeper=lambda s: now.__setitem__(0, now[0] + s),
        on_wait=lambda seconds, reason: announced.append(reason),
    )
    gov.observe({"X-Rate-Limit-Rules": "Ip", "X-Rate-Limit-Ip": "1:10:30"})
    gov.before_request()
    gov.record_request()
    gov.before_request()
    assert announced == ["search rate limit"]


# Live 2026-08-27 the state header rides beside the rules:
#
#     x-rate-limit-ip:       5:10:60,15:60:300,30:300:1800,600:21600:3600
#     x-rate-limit-ip-state: 1:10:0,1:60:0,1:300:0,53:21600:0
#
# hits:period:restricted-seconds, one per clause. It is the server's count,
# and the server's count includes the browser on the trade site, an overlay,
# a second sox, and the run before this one — none of which the local
# history can see. The 30:300 clause locks the IP out for 1800s.

def observe_state(gov, rules, state):
    gov.observe({"X-Rate-Limit-Rules": "Ip", "X-Rate-Limit-Ip": rules,
                 "X-Rate-Limit-Ip-State": state})


def test_the_servers_count_seeds_the_window():
    """One local request, but GGG has seen five this window: the next waits."""
    gov, _, slept = make()
    gov.before_request()
    gov.record_request()
    observe_state(gov, "5:10:60", "5:10:0")
    gov.before_request()
    assert slept == [10]


def test_seeded_hits_sit_just_outside_the_shorter_window():
    """GGG says one hit in the last 10s and five in the last 60s, so the four
    it saw and we did not are older than 10s: they expire in 50s, not 60."""
    gov, _, slept = make()
    gov.before_request()
    gov.record_request()
    observe_state(gov, "2:10:60,5:60:300", "1:10:0,5:60:0")
    gov.before_request()
    assert slept == [50]


def test_the_servers_count_also_trims_a_pessimistic_history():
    """Seeded by one response, corrected by the next: the server's count is
    the truth in both directions."""
    gov, _, slept = make()
    gov.before_request()
    gov.record_request()
    observe_state(gov, "5:10:60", "5:10:0")
    observe_state(gov, "5:10:60", "1:10:0")
    gov.before_request()
    assert slept == []


def test_a_restriction_in_progress_is_waited_out_before_asking():
    """The state header says a restriction is running; the next request
    waits it out rather than earning a longer one."""
    now = [0.0]
    announced = []
    gov = RateGovernor(clock=lambda: now[0],
                       sleeper=lambda s: now.__setitem__(0, now[0] + s),
                       on_wait=lambda s, r: announced.append((s, r)))
    gov.record_request()
    observe_state(gov, "5:10:60", "1:10:45")
    gov.before_request()
    assert announced == [(45.0, "restricted by GGG")]


def test_budget_is_the_tightest_clause():
    gov, _, _ = make()
    gov.record_request()
    observe_state(gov, "5:10:60,15:60:300", "3:10:0,14:60:0")
    assert gov.budget() == Budget(remaining=1, limit=15, period=60)


def test_no_budget_before_any_headers():
    gov, _, _ = make()
    gov.record_request()
    assert gov.budget() is None


def test_a_long_429_is_recorded_not_slept():
    """A 1800s Retry-After — the 30:300:1800 clause — is a lockout, not a
    pause. Sleeping it stalls the watch loop for half an hour with copies
    queueing behind it; recording it lets the loop keep pricing without
    the search until it lapses."""
    from sox.ggg.governor import SEARCH_DOWN_AFTER

    now = [0.0]
    slept = []
    gov = RateGovernor(clock=lambda: now[0], sleeper=slept.append)
    assert SEARCH_DOWN_AFTER == 60
    assert gov.on_429(retry_after=1800.0) is False
    assert slept == []
    assert gov.wait() == 1800.0
    now[0] = 1000.0
    assert gov.wait() == 800.0


def test_a_short_429_is_still_slept():
    slept = []
    gov = RateGovernor(clock=lambda: 0.0, sleeper=slept.append)
    assert gov.on_429(retry_after=7.0) is True
    assert slept == [7.0]
    assert gov.wait() == 0.0, "slept through it; nothing left to wait"
