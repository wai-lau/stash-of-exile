"""The watch loop is the main entry point and had no test at all.

A NameError in it shipped and only surfaced when a real item was copied:
every unit below it passed while the thing the user actually runs crashed.
"""

from argparse import Namespace
from pathlib import Path

import pytest

from sox import cli, clipboard
from sox.cache import Cache
from sox.config import Config
from sox.ggg.trade import Listing
from sox.scout import IndexEntry, League

FIXTURES = Path(__file__).parent / "fixtures" / "items"
LEAGUE = League(value="Runes of Aldur", short="runes", divine_price_ex=320.0,
                base_currency="Exalted Orb")


class FakeScout:
    def __init__(self, index=None):
        self._index = index or {}

    def current_league(self):
        return LEAGUE

    def prices(self, league):
        return self._index

    def currency_rates(self, index):
        return {"exalted": 1.0, "divine": 320.0}


class FakeTrade:
    def __init__(self):
        self.searches = 0

    def search(self, query):
        self.searches += 1
        return "q", [f"h{i}" for i in range(12)]

    def fetch(self, query_id, hashes):
        return [Listing(amount=5.0, currency="exalted", account="a")]


def run(monkeypatch, tmp_path, texts, index=None, trade=None):
    monkeypatch.setattr(clipboard, "watch", lambda poll: iter(texts))
    monkeypatch.setattr(clipboard, "describe_backend", lambda: "fake")
    if trade is not None:
        monkeypatch.setattr(cli, "TradeClient", lambda *a, **k: trade)
    args = Namespace(poll=10, no_trade=trade is None, force=False)
    # A per-test cache: a shared one let an earlier test's price be replayed
    # here, so the client under test was never called at all.
    cache = Cache(tmp_path / "cache.sqlite")
    try:
        return cli.run_watch(args, Config(), cache, FakeScout(index), LEAGUE)
    finally:
        cache.close()


def test_watch_prices_a_copied_item(monkeypatch, capsys, tmp_path):
    trade = FakeTrade()
    code = run(monkeypatch, tmp_path, [(FIXTURES / "RareItem.txt").read_text()], trade=trade)
    out = capsys.readouterr().out
    assert code == 0
    assert "Oblivion Strike" in out
    assert "market" in out
    assert trade.searches >= 1


def test_watch_ignores_non_item_text(monkeypatch, capsys, tmp_path):
    trade = FakeTrade()
    run(monkeypatch, tmp_path, ["just some text I copied", "https://example.com"], trade=trade)
    out = capsys.readouterr().out
    assert trade.searches == 0
    assert "market" not in out


def test_watch_survives_a_failing_lookup(monkeypatch, capsys, tmp_path):
    """One bad item must not end the session."""
    class Exploding(FakeTrade):
        def search(self, query):
            raise RuntimeError("boom")

    texts = [(FIXTURES / "RareItem.txt").read_text(),
             (FIXTURES / "UncutSkillGem.txt").read_text()]
    index = {"Uncut Skill Gem (Level 19)": IndexEntry(
        "Uncut Skill Gem (Level 19)", 9.2, 457, {})}
    code = run(monkeypatch, tmp_path, texts, index=index, trade=Exploding())
    out = capsys.readouterr().out
    assert code == 0
    assert "ERROR" in out, "the failure is reported"
    assert "Uncut Skill Gem" in out, "and the next item is still priced"


def test_watch_prices_from_the_index_without_a_trade_client(monkeypatch, capsys, tmp_path):
    index = {"Uncut Skill Gem (Level 19)": IndexEntry(
        "Uncut Skill Gem (Level 19)", 9.2, 457, {})}
    run(monkeypatch, tmp_path, [(FIXTURES / "UncutSkillGem.txt").read_text()], index=index)
    out = capsys.readouterr().out
    assert "9.2 ex" in out
