"""The exchange fills as a watch session sees them.

poe2scout's snapshot is read once, at startup, and a session can run for
hours on it. Waiting for the request before the first item prices is the
wrong trade too: the cache holds the last snapshot, so the session starts
on that and the fresh one lands when it lands.
"""

import threading

from sox.scout import LiveFills, Snapshot

OLD = Snapshot({"Divine Orb": (340.0, 1_000_000.0)}, epoch=1000.0)
NEW = Snapshot({"Divine Orb": (360.0, 2_000_000.0)}, epoch=4600.0)


def test_startup_reads_the_old_fills():
    fills = LiveFills(OLD)
    assert fills.get("Divine Orb") == (340.0, 1_000_000.0)
    assert fills.get("Chaos Orb", (0.0, 0.0)) == (0.0, 0.0)
    assert bool(fills)
    assert not LiveFills(Snapshot({}, None))


def test_the_old_fills_stand_until_the_fetch_lands():
    gate = threading.Event()

    def fetch():
        gate.wait()
        return NEW

    fills = LiveFills(OLD)
    worker = fills.refresh(fetch)
    assert fills.take_update() is False
    assert fills.get("Divine Orb") == (340.0, 1_000_000.0)
    gate.set()
    worker.join()
    assert fills.take_update() is True
    assert fills.get("Divine Orb") == (360.0, 2_000_000.0)


def test_an_update_is_taken_once():
    fills = LiveFills(OLD)
    fills.refresh(lambda: NEW).join()
    assert fills.take_update() is True
    assert fills.take_update() is False


def test_an_empty_fetch_keeps_the_old_fills():
    """A failed or empty snapshot is not news; the old figures stay."""
    fills = LiveFills(OLD)
    fills.refresh(lambda: Snapshot({}, None)).join()
    assert fills.take_update() is False
    assert fills.get("Divine Orb") == (340.0, 1_000_000.0)


def test_a_fetch_that_raises_keeps_the_old_fills():
    def fetch():
        raise RuntimeError("poe2scout down")

    fills = LiveFills(OLD)
    fills.refresh(fetch).join()
    assert fills.take_update() is False
    assert fills.get("Divine Orb") == (340.0, 1_000_000.0)


def test_age_is_measured_from_the_snapshot_epoch():
    """Not from when it was fetched: a fresh fetch of a day-old snapshot is
    a day old."""
    fills = LiveFills(OLD)
    assert fills.age(now=1000.0 + 3 * 3600) == 3 * 3600
    fills.refresh(lambda: NEW).join()
    fills.take_update()
    assert fills.age(now=4600.0 + 60) == 60


def test_no_epoch_is_no_age():
    assert LiveFills(Snapshot({}, None)).age(now=5.0) is None
