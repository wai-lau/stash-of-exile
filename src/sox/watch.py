"""Live clipboard pricing feed, for a terminal parked on a second monitor.

Copy an item in game and its price appears here. Nothing to click, no window
to focus, and every entry stays on screen so a session of pricing reads as a
list rather than a series of one-shot answers.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sox import __version__

DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"

# Above this many exalted an item is worth calling out.
NOTABLE_VALUE_EX = 500.0


@dataclass
class Session:
    priced: int = 0
    junk: int = 0
    unpriced: int = 0
    searches: int = 0
    total_ex: float = 0.0
    best_name: str = ""
    best_ex: float = 0.0
    started: datetime = field(default_factory=datetime.now)

    def record(self, name: str, price_ex: float | None, searches: int,
               junk: bool = False) -> None:
        self.searches += searches
        if price_ex is None:
            # Junk is a verdict, not a failure, so it is counted apart from
            # items the tool genuinely could not price.
            if junk:
                self.junk += 1
            else:
                self.unpriced += 1
            return
        self.priced += 1
        self.total_ex += price_ex
        if price_ex > self.best_ex:
            self.best_ex, self.best_name = price_ex, name


def width() -> int:
    return min(shutil.get_terminal_size((100, 40)).columns, 120)


def rule(char: str = "─") -> str:
    return DIM + char * width() + RESET


def revision() -> str | None:
    """The checked-out commit, read from .git without invoking git.

    A version string is bumped at releases and so cannot say whether a given
    fix is in the process you are looking at. One session repriced the same
    amulet 1,473 times against a bug that was already fixed and pushed, and
    nothing on screen could tell whether that build was the one running.
    """
    root = Path(__file__).resolve().parent.parent.parent
    try:
        ref = (root / ".git" / "HEAD").read_text().strip()
        if ref.startswith("ref: "):
            ref = (root / ".git" / ref[5:]).read_text().strip()
    except OSError:
        return None
    return ref[:7] or None


def banner(league: str, divine_ex: float, backend: str) -> str:
    build = f"{__version__}" + (f"+{rev}" if (rev := revision()) else "")
    return "\n".join([
        rule("═"),
        f"{BOLD}sox watch{RESET} {DIM}{build}{RESET}"
        f"  ·  {league}  ·  1 div = {divine_ex:,.0f} ex",
        f"{DIM}clipboard: {backend}{RESET}",
        f"{DIM}copy an item in game (Ctrl+C) and it is priced here · Ctrl-D to stop{RESET}",
        rule("═"),
    ])


def interrupted_hint() -> str:
    """Ctrl+C is the game's copy key. Hitting it with the terminal focused is
    a slip, not a request to end the session, so say what actually stops."""
    return f"{DIM}(Ctrl+C is the in-game copy key — press Ctrl-D to stop){RESET}"


def timestamp() -> str:
    return f"{DIM}{datetime.now():%H:%M:%S}{RESET}"


def colour_for(price_ex: float | None, divine_ex: float) -> str:
    if price_ex is None:
        return YELLOW
    if price_ex >= NOTABLE_VALUE_EX:
        return GREEN
    return RESET


def detected(name: str, note: str) -> str:
    """Printed the instant an item lands on the clipboard.

    A trade search can take seconds once the rate governor starts pacing, and
    a feed that shows nothing until the answer arrives looks broken. The name
    appears immediately; the priced detail follows under it.
    """
    return f"{timestamp()} {BOLD}{name}{RESET}  {DIM}· {note}{RESET}"


def body_lines(body: str) -> str:
    """The priced detail, indented under an already-printed header."""
    return "\n".join(f"          {line}" for line in body.splitlines()[1:])


def entry(body: str, price_ex: float | None, divine_ex: float) -> str:
    """One priced item, tinted so a valuable one is visible from across a desk."""
    colour = colour_for(price_ex, divine_ex)
    lines = body.splitlines()
    head = f"{timestamp()} {colour}{BOLD}{lines[0]}{RESET}"
    rest = "\n".join(f"          {line}" for line in lines[1:])
    return f"{head}\n{rest}" if rest else head


def status(session: Session, divine_ex: float) -> str:
    parts = [
        f"{session.priced} priced",
        f"{session.junk} junk" if session.junk else "",
        f"{session.unpriced} unpriced" if session.unpriced else "",
        f"{session.searches} searches",
    ]
    total = f"{session.total_ex:,.0f} ex"
    if divine_ex > 0:
        total += f" ({session.total_ex / divine_ex:,.1f} div)"
    separator = f"  {DIM}·{RESET}  "
    line = f"{CYAN}total {total}{RESET}" + separator + separator.join(
        p for p in parts if p
    )
    return f"{rule()}\n{line}\n{rule()}"


def waiting(message: str) -> str:
    return f"{DIM}… {message}{RESET}"


def error(message: str) -> str:
    return f"{RED}{BOLD}!! ERROR{RESET}   {RED}{message}{RESET}"


def waiting_on_limit(seconds: float, reason: str) -> str:
    return (f"{YELLOW}·· waiting {seconds:.0f}s{RESET}  {DIM}({reason}) — "
            f"copies made while waiting are still picked up{RESET}")
