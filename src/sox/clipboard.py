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
# `Get-Clipboard -Raw` intermittently comes back EMPTY while the clipboard is
# held open by another process — the game does it constantly — and
# SilentlyContinue turns that failed read into "" rather than an error. An
# empty string is not $null, so the old guard let it through: it was emitted
# as new content and became $last, which made the real item new again on the
# next poll. Item, empty, item, empty, forever. Measured against a clipboard
# nobody touched for six seconds: six emissions, two distinct values, one of
# them blank — and the watch feed repriced the same gloves five times and
# counted each into the session total.
_PS_WATCHER = """
$ErrorActionPreference = 'SilentlyContinue'
$last = ''
while ($true) {
    $current = Get-Clipboard -Raw
    if (-not [string]::IsNullOrWhiteSpace($current) -and $current -ne $last) {
        $last = $current
        $bytes = [Text.Encoding]::UTF8.GetBytes($current)
        [Console]::Out.WriteLine([Convert]::ToBase64String($bytes))
        [Console]::Out.Flush()
    }
    Start-Sleep -Milliseconds {poll_ms}
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


def _watch_windows(poll_ms: int) -> Iterator[str]:
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
            if not payload:
                continue
            try:
                text = base64.b64decode(payload).decode("utf-8", "replace")
            except (ValueError, binascii.Error):
                continue
            yield text.replace("\r\n", "\n").strip()
    finally:
        process.terminate()


def _watch_polling(command: list[str], poll_ms: int) -> Iterator[str]:
    last = None
    while True:
        try:
            current = subprocess.run(
                command, capture_output=True, text=True, timeout=5
            ).stdout
        except (subprocess.SubprocessError, OSError):
            current = ""
        if current and current != last:
            last = current
            yield current.strip()
        time.sleep(poll_ms / 1000)


def watch(poll_ms: int = 400, skip_existing: bool = True) -> Iterator[str]:
    """Yield clipboard contents each time they change.

    The clipboard already holds something when the watcher starts, and pricing
    whatever happened to be there is noise. `skip_existing` drops that first
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

    # Deduplicated HERE and not only in each backend. Both of them already
    # tracked the previous value, and the Windows one was still able to yield
    # the same item over and over, so the guarantee this function's docstring
    # makes was resting on a shell script keeping its own promise.
    last = None
    first = True
    for text in stream:
        if text == last:
            continue
        last = text
        if first:
            first = False
            if skip_existing:
                continue
        yield text


def looks_like_item(text: str) -> bool:
    """Item text always opens with these headers; ordinary copied text does not."""
    head = text.lstrip()[:400]
    return head.startswith("Item Class:") or head.startswith("Rarity:")
