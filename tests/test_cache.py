"""On-disk cache.

The cache is an optimisation, never a dependency: if it cannot be read or
written the tool still prices items, it just pays the API calls again.
"""

import os

from sox.cache import TTL, Cache


def test_round_trips_json_values(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    cache.put("index_price", "Mageblood", {"price": 135416.55}, ttl=60)
    assert cache.get("index_price", "Mageblood") == {"price": 135416.55}
    cache.close()


def test_missing_key_returns_none(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    assert cache.get("index_price", "nope") is None
    cache.close()


def test_expired_entry_returns_none(tmp_path):
    now = [1000.0]
    cache = Cache(tmp_path / "c.sqlite", clock=lambda: now[0])
    cache.put("index_price", "k", "v", ttl=10)
    now[0] = 1009.0
    assert cache.get("index_price", "k") == "v"
    now[0] = 1011.0
    assert cache.get("index_price", "k") is None
    cache.close()


def test_persists_across_instances(tmp_path):
    path = tmp_path / "c.sqlite"
    first = Cache(path)
    first.put("stats_data", "stats", [1, 2, 3], ttl=3600)
    first.close()
    second = Cache(path)
    assert second.get("stats_data", "stats") == [1, 2, 3]
    second.close()


def test_ttls_match_the_spec():
    assert TTL["stats_data"] == 7 * 86400
    assert TTL["filters_data"] == 7 * 86400
    assert TTL["index_price"] == 6 * 3600
    assert TTL["trade_price"] == 12 * 3600


def test_survives_the_database_being_deleted_underneath_it(tmp_path):
    """A watch session must not die because the cache file went away.

    Deleting the file leaves the open connection pointing at an unlinked
    inode, and the next write raises "attempt to write a readonly database".
    """
    path = tmp_path / "c.sqlite"
    cache = Cache(path)
    cache.put("index_price", "k", "v", ttl=60)

    path.unlink()
    cache.put("index_price", "k2", "v2", ttl=60)      # must not raise
    assert cache.get("index_price", "k2") in (None, "v2")
    cache.close()


def test_survives_an_unwritable_location(tmp_path):
    """An unusable cache degrades to no cache, never to a crash."""
    directory = tmp_path / "locked"
    directory.mkdir()
    cache = Cache(directory / "c.sqlite")
    cache.close()
    os.chmod(directory, 0o500)
    try:
        broken = Cache(directory / "other.sqlite")
        broken.put("index_price", "k", "v", ttl=60)   # must not raise
        assert broken.get("index_price", "k") is None
        broken.close()
    finally:
        os.chmod(directory, 0o700)
