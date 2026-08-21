"""The clipboard feed yields one entry per copy — no more, no fewer.

Watch mode once repriced the same pair of gloves five times in seven seconds
and counted each into the session total, without a single API call: every
replay was a cache hit, so nothing in the pricing path looked wrong. The
clipboard was handing the same item over and over.

Guarding that by comparing TEXT went too far the other way: copying the same
item twice is two copies and must price twice, and the item already sitting on
the clipboard when the session opens is the one you are most likely to copy
again. So the Windows watcher counts clipboard WRITES, and only the backends
that cannot see writes fall back to comparing text.
"""

import types
from itertools import islice

from sox import clipboard


def _feed(monkeypatch, values, skip_existing=True):
    """`values` is the backend stream: (text, is_baseline) pairs, where the
    baseline is what the clipboard already held when the watcher opened and
    every other pair is one copy."""
    monkeypatch.setattr(clipboard, "_powershell", lambda: "powershell.exe")
    monkeypatch.setattr(clipboard, "_watch_windows", lambda _poll: iter(values))
    return list(clipboard.watch(1, skip_existing=skip_existing))


def _polled(monkeypatch, reads, count):
    """The first `count` values a polling backend yields for `reads`."""
    outputs = iter(reads)
    monkeypatch.setattr(
        clipboard.subprocess, "run",
        lambda *_a, **_kw: types.SimpleNamespace(stdout=next(outputs)),
    )
    monkeypatch.setattr(clipboard.time, "sleep", lambda _s: None)
    return list(islice(clipboard._watch_polling(["xclip"], 1), count))


def test_the_same_item_copied_twice_is_two_copies(monkeypatch):
    """Copy an item, read the price, copy it again to re-check: two copies.

    Comparing text made the second one invisible, and the feed sat silent
    through every re-copy — which reads as a broken clipboard, not as a
    deliberate skip.
    """
    stream = [("stale", True), ("a", False), ("a", False)]
    assert _feed(monkeypatch, stream) == ["a", "a"]


def test_a_copy_after_something_else_is_a_new_copy(monkeypatch):
    """A price checked, another item checked, then the first one again is
    three copies and the feed must show three."""
    stream = [("", True), ("a", False), ("b", False), ("a", False)]
    assert _feed(monkeypatch, stream) == ["a", "b", "a"]


def test_whatever_was_on_the_clipboard_at_startup_is_skipped(monkeypatch):
    """Pricing whatever happened to be there when the session opened is
    noise: it is skipped until you copy it yourself."""
    stream = [("stale", True), ("copied", False)]
    assert _feed(monkeypatch, stream) == ["copied"]


def test_the_first_copy_of_a_session_is_not_taken_for_stale_clipboard(monkeypatch):
    """Skipping the stale clipboard means skipping what was ALREADY there, not
    whatever arrives first.

    An empty clipboard at startup announces itself as empty. Without that the
    first thing down the pipe was the user's first copy, dropped as if it were
    stale.
    """
    stream = [("", True), ("copied", False)]
    assert _feed(monkeypatch, stream) == ["copied"]


def test_the_stale_clipboard_is_shown_when_it_is_not_skipped(monkeypatch):
    """`skip_existing=False` asks for what is on the clipboard now, so the
    baseline is a value of the feed rather than a value to hide."""
    stream = [("stale", True), ("copied", False)]
    assert _feed(monkeypatch, stream, skip_existing=False) == ["stale", "copied"]


def test_the_shell_watcher_counts_clipboard_writes():
    """Windows bumps a sequence number on every write to the clipboard, so a
    re-copy of unchanged text is still a copy. Text alone cannot see it."""
    assert "GetClipboardSequenceNumber" in clipboard._PS_WATCHER


def test_the_shell_watcher_ignores_an_empty_read():
    """`Get-Clipboard -Raw` comes back EMPTY while another process holds the
    clipboard open — the game does it constantly — and SilentlyContinue turns
    that failed read into "". A blank read is a failed read, not a copy."""
    assert "IsNullOrWhiteSpace" in clipboard._PS_WATCHER


def test_the_shell_watcher_reports_the_clipboard_it_opened_on():
    """The baseline has to come from the watcher: only it knows what was there
    before the loop started, and a blank one must still be announced."""
    assert f"'{clipboard._BASELINE_PREFIX}'" in clipboard._PS_WATCHER


def test_a_polled_backend_opens_with_the_clipboard_it_found(monkeypatch):
    """wl-paste, xclip and pbpaste hand back text and nothing else, so their
    first read is the baseline and a repeat of it is the same copy still
    sitting there — not a new one."""
    reads = ["stale", "stale", "a", "b"]
    assert _polled(monkeypatch, reads, 3) == [("stale", True), ("a", False),
                                              ("b", False)]
