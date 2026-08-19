"""Configuration. Pure data — the only I/O is reading the TOML file.

Note there are no credentials here. The trade API needs no authentication,
which was verified end to end, so sox holds no secrets at all.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "sox" / "config.toml"
DEFAULT_CACHE_PATH = Path.home() / ".local" / "share" / "sox" / "cache.sqlite"

# Verified against /api/trade2/data/filters -> status_filters.status
VALID_STATUS = ("available", "securable", "onlineleague", "online", "any")

USER_AGENT = "sox/0.1 (personal item pricer; +https://github.com/wai-lau/stash-of-exile)"


@dataclass(frozen=True)
class Config:
    league: str | None = None      # None -> resolve the current league at runtime
    # "any", not "online". PoE2 trade is asynchronous — buyers message a
    # seller and wait — so most of the market is offline at any moment and
    # buyers browse it regardless. Filtering to online sellers cut one search
    # from 918 listings to 1, which then priced a 3ex item in the hundreds.
    status: str = "any"
    max_searches: int = 4          # per item; one per relaxation rung
    cache_path: Path = DEFAULT_CACHE_PATH
    user_agent: str = USER_AGENT
    force: bool = False            # search even low-scoring items

    @property
    def league_or_current(self) -> str | None:
        return self.league


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return Config()

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    cfg = Config(
        league=raw.get("league"),
        status=raw.get("status", "any"),
        max_searches=int(raw.get("max_searches", 4)),
    )
    if cfg.status not in VALID_STATUS:
        raise ValueError(f"invalid status {cfg.status!r}; expected one of {VALID_STATUS}")
    if "cache_path" in raw:
        cfg = replace(cfg, cache_path=Path(raw["cache_path"]).expanduser())
    return cfg
