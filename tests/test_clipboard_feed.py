"""The clipboard feed yields a copy once.

Watch mode repriced the same pair of gloves five times in seven seconds and
counted each into the session total, without a single API call: every replay
was a cache hit, so nothing in the pricing path looked wrong. The clipboard
was handing the same item over and over.
"""

from sox import clipboard


def _feed(monkeypatch, values, skip_existing=True):
    monkeypatch.setattr(clipboard, "_powershell", lambda: "powershell.exe")
    monkeypatch.setattr(clipboard, "_watch_windows", lambda _poll: iter(values))
    return list(clipboard.watch(1, skip_existing=skip_existing))


def test_the_same_clipboard_is_not_yielded_twice(monkeypatch):
    """`Get-Clipboard -Raw` comes back EMPTY while another process holds the
    clipboard open, and SilentlyContinue turns that failed read into "".

    An empty string is not $null, so the watcher took it for new content and
    made it the value to compare against — which made the real item new again
    on the next poll. Item, empty, item, empty. Six seconds against a
    clipboard nobody touched emitted six times, three of them blank.
    """
    assert _feed(monkeypatch, ["a", "a", "a"], skip_existing=False) == ["a"]


def test_a_copy_after_something_else_is_a_new_copy(monkeypatch):
    """Deduplication is against the LAST value, not everything ever seen: a
    price checked, another item checked, then the first one again is three
    copies and the feed must show three."""
    assert _feed(monkeypatch, ["a", "b", "a"], skip_existing=False) == ["a", "b", "a"]


def test_whatever_was_on_the_clipboard_at_startup_is_skipped(monkeypatch):
    """Pricing whatever happened to be there when the session opened is
    noise, and repeats of it must stay skipped rather than becoming the
    first entry of the feed."""
    assert _feed(monkeypatch, ["stale", "stale", "copied"]) == ["copied"]


def test_the_shell_watcher_ignores_an_empty_read(monkeypatch):
    """The same guard on the other side of the pipe, so a blank read never
    becomes the value the next poll is compared against."""
    assert "IsNullOrWhiteSpace" in clipboard._PS_WATCHER
