"""Watch the clipboard for new item text.

On WSL the clipboard that matters belongs to Windows, where the game runs.
Reading it means calling powershell.exe, which costs roughly half a second to
start — far too slow to respawn on every poll. So a single long-lived
PowerShell does the polling itself and pushes changes over stdout, and this
module just reads that stream.

Native Linux and macOS backends are polled directly, since those tools start
in milliseconds.
"""

from __future__ import annotations

import base64
import binascii
import shutil
import subprocess
import time
from collections.abc import Iterator

# Each clipboard payload is sent as ONE base64 line. A start-delimiter with a
# multi-line payload cannot work here: the payload is only known to be
# complete when the NEXT delimiter arrives, so every item would lag one copy
# behind. One self-contained line per change has no such lag, and base64 also
# removes any chance of item text colliding with a delimiter.
#
# Write-Output block-buffers when stdout is a pipe, so writing goes through
# [Console]::Out with an explicit Flush to make every change arrive at once.
#
# The first line is the BASELINE — whatever the clipboard held when the watcher
# opened — marked with a `#`, which base64 never produces. It is sent even when
# that clipboard was blank, because "nothing was there" is exactly what the
# reader needs to know: without it the reader can only guess that the first
# line it sees is the stale clipboard, and on a blank start that guess eats the
# session's first real copy.
#
# What counts as a new copy is the clipboard SEQUENCE NUMBER, which Windows
# bumps on every write. Comparing the text instead cannot see the commonest
# check of all — copy an item, read its price, copy the same item again — and
# the item most likely to be re-copied is the one already on the clipboard when
# the session opened, i.e. the one the baseline skips. Between the two the feed
# looked dead: nothing appeared however many times the item was copied, until
# a DIFFERENT item was copied.
#
# `Get-Clipboard -Raw` intermittently comes back EMPTY while the clipboard is
# held open by another process — the game does it constantly — and
# SilentlyContinue turns that failed read into "" rather than an error. An
# empty string is not $null, so a blank read once counted as new content and
# made the real item new again on the next poll: item, empty, item, empty,
# forever, repricing the same gloves five times into the session total. A blank
# read is a failed read, so it is skipped WITHOUT advancing the sequence — the
# next poll simply reads again.
#
# GetClipboardSequenceNumber needs a compiler for Add-Type, so a machine
# without one falls back to comparing text: a re-copy goes unnoticed there, but
# the feed still runs.
_BASELINE_PREFIX = "#"

_PS_WATCHER = """
$ErrorActionPreference = 'SilentlyContinue'
$sequenced = $false
try {
    Add-Type -Namespace Sox -Name Clip -ErrorAction Stop -MemberDefinition @'
[DllImport("user32.dll")]
public static extern uint GetClipboardSequenceNumber();
'@
    $seq = [Sox.Clip]::GetClipboardSequenceNumber()
    $sequenced = $true
} catch { }
$last = Get-Clipboard -Raw
if ([string]::IsNullOrWhiteSpace($last)) { $last = '' }
$bytes = [Text.Encoding]::UTF8.GetBytes($last)
[Console]::Out.WriteLine('#' + [Convert]::ToBase64String($bytes))
[Console]::Out.Flush()
while ($true) {
    Start-Sleep -Milliseconds {poll_ms}
    if ($sequenced) {
        $now = [Sox.Clip]::GetClipboardSequenceNumber()
        if ($now -eq $seq) { continue }
    }
    $current = Get-Clipboard -Raw
    if ([string]::IsNullOrWhiteSpace($current)) { continue }
    if (-not $sequenced -and $current -eq $last) { continue }
    $seq = $now
    $last = $current
    $bytes = [Text.Encoding]::UTF8.GetBytes($current)
    [Console]::Out.WriteLine([Convert]::ToBase64String($bytes))
    [Console]::Out.Flush()
}
"""

def _powershell() -> str | None:
    for name in ("powershell.exe", "pwsh.exe"):
        if shutil.which(name):
            return name
    return None


def _polling_backend() -> list[str] | None:
    for command in (
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
        ["pbpaste"],
    ):
        if shutil.which(command[0]):
            return command
    return None


def describe_backend() -> str:
    if _powershell():
        return f"{_powershell()} (Windows clipboard via WSL)"
    backend = _polling_backend()
    return backend[0] if backend else "none"


def _watch_windows(poll_ms: int) -> Iterator[tuple[str, bool]]:
    """Yield `(text, is_baseline)`, the baseline first."""
    script = _PS_WATCHER.replace("{poll_ms}", str(poll_ms))
    process = subprocess.Popen(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        # NOT `for line in process.stdout`: iterating a file object uses an
        # 8KB read-ahead buffer, so nothing is yielded until that fills. For a
        # live feed each line must be delivered as it arrives.
        for line in iter(process.stdout.readline, ""):  # type: ignore[union-attr]
            payload = line.strip()
            baseline = payload.startswith(_BASELINE_PREFIX)
            if baseline:
                payload = payload[len(_BASELINE_PREFIX):]
            elif not payload:
                continue
            try:
                text = base64.b64decode(payload).decode("utf-8", "replace")
            except (ValueError, binascii.Error):
                continue
            yield text.replace("\r\n", "\n").strip(), baseline
    finally:
        process.terminate()


def _watch_polling(command: list[str], poll_ms: int) -> Iterator[tuple[str, bool]]:
    """Yield `(text, is_baseline)`, the baseline first — blank if the clipboard
    was empty when the watch opened."""
    last = None
    baseline = True
    while True:
        try:
            current = subprocess.run(
                command, capture_output=True, text=True, timeout=5
            ).stdout
        except (subprocess.SubprocessError, OSError):
            current = ""
        if baseline:
            baseline = False
            last = current
            yield current.strip(), True
        elif current and current != last:
            last = current
            yield current.strip(), False
        time.sleep(poll_ms / 1000)


def watch(poll_ms: int = 400, skip_existing: bool = True) -> Iterator[str]:
    """Yield clipboard contents each time they change.

    The clipboard already holds something when the watcher starts, and pricing
    whatever happened to be there is noise. `skip_existing` drops that stale
    value so the feed only shows what you copy from now on.
    """
    if _powershell():
        stream = _watch_windows(poll_ms)
    else:
        backend = _polling_backend()
        if backend is None:
            raise RuntimeError(
                "no clipboard backend found. Install wl-clipboard or xclip, or "
                "run under WSL where powershell.exe is available."
            )
        stream = _watch_polling(backend, poll_ms)

    # A backend reports one value per COPY: the Windows one watches the
    # clipboard sequence number, and the polling ones compare text because a
    # sequence number is all they lack. Deduplicating again here would undo
    # that — a second copy of the same item is a second copy, and the feed
    # going silent on it looks exactly like a broken clipboard.
    #
    # The stale clipboard is the one the BACKEND marks as the baseline, never
    # merely the first line to arrive. Nothing is sent for a clipboard that is
    # empty at startup, or one whose early reads come back blank because the
    # game is holding it open, so "first line" was the session's FIRST REAL
    # COPY and it was dropped as if it were stale.
    for text, baseline in stream:
        if baseline:
            if text and not skip_existing:
                yield text
            continue
        yield text


def looks_like_item(text: str) -> bool:
    """Item text always opens with these headers; ordinary copied text does not."""
    head = text.lstrip()[:400]
    return head.startswith("Item Class:") or head.startswith("Rarity:")
