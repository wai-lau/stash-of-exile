"""The resolver must survive GGG rewording mods between patches.

Each test mutates a copy of the live stats table the way a patch plausibly
would, then asserts the allowlist still resolves — or fails loudly when it
genuinely cannot.
"""

import gzip
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "resolve_allowlist.py"
LOCK = ROOT / "src" / "sox" / "data" / "mod_allowlist.toml"

# Recorded fixtures — the suite makes no network calls.
FIXTURES = Path(__file__).resolve().parent / "fixtures"
STATS = FIXTURES / "trade2_stats.json.gz"


def read_gz(path):
    with gzip.open(path, "rt") as fh:
        return json.load(fh)

LIFE_ID = "explicit.stat_3299347043"
LIFE_TEXT = "# to maximum Life"


def run(stats, lock=LOCK):
    """Run the generator, returning (exit_code, stdout, stderr)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(stats, fh)
        path = fh.name
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), path, str(lock)],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def load_stats():
    return read_gz(STATS)


def mutate_text(stats, stat_id, new_text):
    for group in stats["result"]:
        for entry in group["entries"]:
            if entry["id"] == stat_id:
                entry["text"] = new_text
                return stats
    raise AssertionError(f"{stat_id} not found")


def drop_id(stats, stat_id):
    for group in stats["result"]:
        group["entries"] = [e for e in group["entries"] if e["id"] != stat_id]
    return stats


def test_baseline_resolves():
    code, out, err = run(load_stats())
    assert code == 0, err
    assert LIFE_ID in out


def test_survives_recapitalization():
    """A patch that re-cases a mod must not drop it."""
    stats = mutate_text(load_stats(), LIFE_ID, "# TO MAXIMUM life")
    code, out, err = run(stats)
    assert code == 0, err
    assert LIFE_ID in out


def test_survives_added_plus_and_whitespace():
    stats = mutate_text(load_stats(), LIFE_ID, "+#  to   maximum Life")
    code, out, err = run(stats)
    assert code == 0, err
    assert LIFE_ID in out


def test_survives_full_rewording_via_lock():
    """Text we cannot match is fine as long as the id still exists.

    This is the case plain text matching cannot survive: the lock key comes
    from our own canonical text, so the previously resolved id is reused.
    """
    stats = mutate_text(load_stats(), LIFE_ID, "# to Maximum Hitpoints")
    code, out, err = run(stats)
    assert code == 0, err
    assert LIFE_ID in out
    assert "locked" in err


def test_fails_loudly_when_id_and_text_both_gone():
    """A genuinely removed mod must fail the build, not vanish silently."""
    stats = drop_id(load_stats(), LIFE_ID)
    stats = mutate_text(stats, "explicit.stat_3489782002", "# to maximum Energy Shield")
    code, out, err = run(stats)
    assert code == 1, "expected non-zero exit when a mod cannot be resolved"
    assert LIFE_TEXT in err
    assert LIFE_ID not in out


def test_ambiguous_ids_are_all_kept():
    """Duplicate mod text must produce an OR group, never an arbitrary pick."""
    code, out, err = run(load_stats())
    assert code == 0, err
    assert "ambiguous = true" in out
    assert "explicit.stat_3981240776" in out and "explicit.stat_2704225257" in out


# --- base and unique resolvers -------------------------------------------

BASE_SCRIPT = ROOT / "scripts" / "resolve_base_allowlist.py"
UNIQUE_SCRIPT = ROOT / "scripts" / "resolve_unique_allowlist.py"
ITEMS = FIXTURES / "trade2_items.json.gz"


def run_items(script, items, extra=()):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(items, fh)
        path = fh.name
    proc = subprocess.run(
        [sys.executable, str(script), path, *extra],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def load_items():
    return read_gz(ITEMS)


def recase_base(items, base, new):
    for group in items["result"]:
        for entry in group["entries"]:
            if not entry.get("name") and entry.get("type") == base:
                entry["type"] = new
                return items
    raise AssertionError(f"base {base} not found")


def drop_base(items, base):
    for group in items["result"]:
        group["entries"] = [
            e for e in group["entries"]
            if e.get("name") or e.get("type") != base
        ]
    return items


def test_base_survives_recapitalization():
    """A re-cased base must still resolve, and be written canonically."""
    items = recase_base(load_items(), "Omen Sceptre", "OMEN SCEPTRE")
    code, out, err = run_items(BASE_SCRIPT, items)
    assert code == 0, err
    assert 'name = "OMEN SCEPTRE"' in out, "should emit the table's spelling"


def test_base_fails_loudly_when_removed():
    items = drop_base(load_items(), "Omen Sceptre")
    code, out, err = run_items(BASE_SCRIPT, items)
    assert code == 1, "a removed base must fail the build"
    assert "Omen Sceptre" in err


def scout_arg():
    """Materialize the gzipped scout fixture as plain JSON for the resolver."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(read_gz(FIXTURES / "scout_uniques.json.gz"), fh)
        return fh.name


def test_unique_survives_recapitalization():
    items = load_items()
    for group in items["result"]:
        for entry in group["entries"]:
            if entry.get("name") == "Mageblood":
                entry["name"] = "MAGEBLOOD"
    code, out, err = run_items(UNIQUE_SCRIPT, items, [scout_arg()])
    assert code == 0, err
    assert 'name = "MAGEBLOOD"' in out


def test_unique_fails_loudly_when_removed():
    items = load_items()
    for group in items["result"]:
        group["entries"] = [e for e in group["entries"] if e.get("name") != "Mageblood"]
    code, out, err = run_items(UNIQUE_SCRIPT, items, [scout_arg()])
    assert code == 1, "a removed unique must fail the build"
    assert "Mageblood" in err
