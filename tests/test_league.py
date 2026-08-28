"""Which league the index is read from.

Two leagues report IsCurrent at once — the softcore league and its hardcore
twin — so "the first current one" resolves by list order, not by logic. Live
on 2026-08-19 the order happened to put softcore first; nothing guarantees it,
and reading HC prices into a softcore session is silently wrong (divine was
358.07 against 361.68, close enough that no output would look odd).
"""

import httpx
import pytest

from sox.cache import Cache
from sox.scout import ScoutClient

# Shape copied from a live /poe2/Leagues response, HC first.
LEAGUES = [
    {"Value": "HC Runes of Aldur", "ShortName": "runeshc", "IsCurrent": True,
     "DivinePrice": 361.68, "BaseCurrencyText": "Exalted Orb"},
    {"Value": "Runes of Aldur", "ShortName": "runes", "IsCurrent": True,
     "DivinePrice": 358.07, "BaseCurrencyText": "Exalted Orb"},
    {"Value": "Standard", "ShortName": "standard", "IsCurrent": False,
     "DivinePrice": 185.81, "BaseCurrencyText": "Exalted Orb"},
]


# The snapshot's own timestamp, from /ExchangeSnapshot — poe2scout takes one
# an hour, and this one is 2026-08-27 00:00 UTC.
SNAPSHOT = {"Epoch": 1_787_788_800, "Volume": 81_803_773.0}


def build(payload, cache_path, snapshot=SNAPSHOT):
    def handler(request):
        if "ExchangeSnapshot" in str(request.url):
            if snapshot is None:
                return httpx.Response(404, json={})
            return httpx.Response(200, json=snapshot)
        return httpx.Response(200, json=payload)

    return ScoutClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        Cache(cache_path),
        user_agent="sox-test",
    )


def test_the_current_league_is_the_softcore_one(tmp_path):
    """Not "the first IsCurrent": HC is listed first here and must lose."""
    scout = build(LEAGUES, tmp_path / "c.sqlite")
    assert scout.current_league().value == "Runes of Aldur"


def test_hardcore_is_reachable_on_request(tmp_path):
    scout = build(LEAGUES, tmp_path / "c.sqlite")
    assert scout.current_league(hardcore=True).value == "HC Runes of Aldur"


def test_a_league_with_no_current_softcore_entry_is_an_error(tmp_path):
    only_hc = [dict(LEAGUES[0]), dict(LEAGUES[2])]
    scout = build(only_hc, tmp_path / "c.sqlite")
    with pytest.raises(RuntimeError):
        scout.current_league()


# Shape copied from a live SnapshotPairs response, one pair, trimmed to the
# fields read. RelativePrice is quoted in a unit of the snapshot's own:
# Exalted Orb itself reported 0.91, so divine's raw 329.21 is 361.77 ex.
PAIRS = [
    {
        "CurrencyOne": {"Text": "Divine Orb"},
        "CurrencyTwo": {"Text": "Exalted Orb"},
        "CurrencyOneData": {"RelativePrice": 329.21, "ValueTraded": 17_314_115.0},
        "CurrencyTwoData": {"RelativePrice": 0.91, "ValueTraded": 797_092.0},
    },
    {
        "CurrencyOne": {"Text": "Divine Orb"},
        "CurrencyTwo": {"Text": "Masterwork Rune"},
        # A lower-volume divine figure: the best-traded pair's number wins.
        "CurrencyOneData": {"RelativePrice": 999.0, "ValueTraded": 35_554.0},
        "CurrencyTwoData": {"RelativePrice": 237.03, "ValueTraded": 34_940.0},
    },
]


def test_fills_are_normalised_by_exalted_own_figure(tmp_path):
    scout = build(PAIRS, tmp_path / "c.sqlite")
    fills = scout.exchange_fills("runes")
    price, traded = fills["Divine Orb"]
    assert price == pytest.approx(329.21 / 0.91)
    assert traded == pytest.approx(17_314_115.0 / 0.91)
    assert fills["Masterwork Rune"][0] == pytest.approx(237.03 / 0.91)


def test_a_snapshot_with_no_exalted_figure_prices_nothing(tmp_path):
    """Without exalted's own figure there is no unit to normalise with, and
    a raw RelativePrice quoted as a price would be ~10% off everything."""
    scout = build([PAIRS[1]], tmp_path / "c.sqlite")
    assert not scout.exchange_fills("runes")


def build_counting(payload, cache):
    """`calls` counts the pair reads; the epoch read rides along with each."""
    calls = []

    def handler(request):
        if "ExchangeSnapshot" in str(request.url):
            return httpx.Response(200, json=SNAPSHOT)
        calls.append(str(request.url))
        return httpx.Response(200, json=payload)

    return ScoutClient(httpx.Client(transport=httpx.MockTransport(handler)),
                       cache, user_agent="sox-test"), calls


def test_cached_fills_are_read_past_their_ttl(tmp_path):
    """Startup takes the last snapshot the cache holds, however old, and
    costs no request doing it."""
    now = [1000.0]
    cache = Cache(tmp_path / "c.sqlite", clock=lambda: now[0])
    scout, calls = build_counting(PAIRS, cache)
    fresh = scout.exchange_fills("runes")
    now[0] += 2 * 3600
    assert scout.cached_fills("runes") == fresh
    assert len(calls) == 1


def test_a_refresh_asks_again_even_with_a_fresh_cache(tmp_path):
    """The snapshot is hourly and poe2scout is not rate-limited, so the
    background fetch always asks; the cache only seeds the wait."""
    scout, calls = build_counting(PAIRS, Cache(tmp_path / "c.sqlite"))
    scout.exchange_fills("runes")
    scout.exchange_fills("runes")
    assert len(calls) == 1
    scout.exchange_fills("runes", refresh=True)
    assert len(calls) == 2


def test_the_snapshot_carries_its_own_epoch(tmp_path):
    """poe2scout's hourly snapshots stopped for a day on 2026-08-27 and a
    freshly fetched answer was 29 hours old. The fetch time says nothing;
    the snapshot's own timestamp is what an age is measured from."""
    scout = build(PAIRS, tmp_path / "c.sqlite")
    snap = scout.exchange_fills("runes")
    assert snap.epoch == 1_787_788_800
    assert snap.age(now=1_787_788_800 + 29 * 3600) == 29 * 3600


def test_the_cached_snapshot_keeps_its_epoch(tmp_path):
    now = [1000.0]
    cache = Cache(tmp_path / "c.sqlite", clock=lambda: now[0])
    scout, _ = build_counting(PAIRS, cache)
    scout.exchange_fills("runes")
    now[0] += 2 * 3600
    assert scout.cached_fills("runes").epoch == 1_787_788_800


def test_a_snapshot_without_an_epoch_has_no_age(tmp_path):
    """The epoch endpoint is as undocumented as the pairs one; losing it
    loses the warning, not the prices."""
    scout = build(PAIRS, tmp_path / "c.sqlite", snapshot=None)
    snap = scout.exchange_fills("runes")
    assert snap["Divine Orb"][0] == pytest.approx(329.21 / 0.91)
    assert snap.epoch is None
    assert snap.age(now=1.0) is None


def test_an_empty_cache_is_an_empty_snapshot(tmp_path):
    scout, calls = build_counting(PAIRS, Cache(tmp_path / "c.sqlite"))
    snap = scout.cached_fills("runes")
    assert not snap and snap.epoch is None
    assert calls == []
