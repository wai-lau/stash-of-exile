"""The watch status line says how many searches GGG has left for us, so the
next copy can wait for the window instead of finding a wait line."""

from sox import watch as watch_ui
from sox.ggg.governor import Budget


def test_status_shows_the_searches_left_in_the_tightest_window():
    stats = watch_ui.Session(priced=1, searches=4, total_ex=10.0)
    line = watch_ui.status(stats, divine_ex=360.0, budget=Budget(11, 15, 60))
    assert "11/15 left (60s window)" in line


def test_longer_windows_read_in_minutes_and_hours():
    stats = watch_ui.Session(priced=1, searches=4)
    assert "9/30 left (5min window)" in watch_ui.status(
        stats, divine_ex=360.0, budget=Budget(9, 30, 300))
    assert "547/600 left (6h window)" in watch_ui.status(
        stats, divine_ex=360.0, budget=Budget(547, 600, 21600))


def test_no_budget_before_the_first_search_answers():
    stats = watch_ui.Session(priced=1, searches=0)
    assert "left" not in watch_ui.status(stats, divine_ex=360.0)
