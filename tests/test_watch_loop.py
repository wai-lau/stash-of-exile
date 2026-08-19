"""The watch loop's exit behaviour.

Ctrl+C is how you copy an item in Path of Exile. Pressing it while the
terminal has focus instead of the game used to end the session, so Ctrl-D
stops and Ctrl+C only says so.

This file exists because run_watch had no test at all, which is how a
NameError in the loop body once shipped with every other test green.
"""

import io
import types
from unittest import mock

from sox import cli, clipboard


class _Scout:
    def prices(self, _league):
        return {}

    def currency_rates(self, _index):
        return {"exalted": 1.0, "divine": 320.0}


LEAGUE = types.SimpleNamespace(value="Rise of the Abyssal", short="Abyss",
                               divine_price_ex=320.0)
ARGS = types.SimpleNamespace(poll=1, no_trade=True)
CFG = types.SimpleNamespace(user_agent="sox/test", status="any", league=None,
                            max_searches=4, force=False)


def _run(monkeypatch, feed, *, tty=True):
    monkeypatch.setattr(clipboard, "watch", lambda _poll: feed())
    monkeypatch.setattr(clipboard, "describe_backend", lambda: "test")
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: tty, raising=False)
    # The EOF watcher would interrupt the main thread out from under the test.
    monkeypatch.setattr(cli.threading, "Thread", lambda **kw: types.SimpleNamespace(start=lambda: None))
    return cli.run_watch(ARGS, CFG, None, _Scout(), LEAGUE)


def test_ctrl_c_does_not_end_the_session(monkeypatch, capsys):
    """The copy key must not kill the feed; it prints what actually stops."""
    rounds = []

    def feed():
        rounds.append(1)
        if len(rounds) < 3:
            raise KeyboardInterrupt
        return iter(())  # third pass: clipboard closes normally

    assert _run(monkeypatch, feed) == 0
    assert len(rounds) == 3, "the loop restarted after each interrupt"
    out = capsys.readouterr().out
    assert out.count("press Ctrl-D to stop") == 2


def test_ctrl_d_ends_the_session(monkeypatch, capsys):
    """EOF sets the stop flag, so the interrupt it raises exits, not hints."""
    events = []
    real_event = cli.threading.Event
    monkeypatch.setattr(cli.threading, "Event",
                        lambda: events.append(real_event()) or events[-1])

    def feed():
        # Stand in for the EOF watcher firing: it sets the loop's stop flag,
        # then interrupts the main thread to break it out of the poll.
        events[0].set()
        raise KeyboardInterrupt

    assert _run(monkeypatch, feed) == 0
    assert "press Ctrl-D to stop" not in capsys.readouterr().out


def test_eof_on_stdin_raises_the_interrupt_that_stops_the_loop():
    """Ctrl-D is only EOF on stdin; the loop is blocked polling the clipboard
    and never reads it, so the watcher has to interrupt the main thread."""
    import threading

    stop = threading.Event()
    fired = threading.Event()
    with mock.patch.object(cli.sys, "stdin", io.StringIO("")), \
            mock.patch.object(cli._thread, "interrupt_main", fired.set):
        cli._stop_on_eof(stop)
    assert stop.is_set() and fired.is_set()


def test_without_a_tty_ctrl_c_still_exits(monkeypatch, capsys):
    """There is no Ctrl-D to press when stdin is a pipe, so suppressing
    Ctrl+C there would leave no way out at all."""
    def feed():
        raise KeyboardInterrupt

    assert _run(monkeypatch, feed, tty=False) == 0
    assert "press Ctrl-D to stop" not in capsys.readouterr().out
