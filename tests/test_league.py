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


def build(payload, cache_path):
    def handler(request):
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
