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

import shutil
import subprocess
import time
from collections.abc import Iterator

# Marks the start of a clipboard payload on the PowerShell stream. Chosen to
# be something no item text could contain.
DELIMITER = "<<<SOX-CLIP>>>"

_PS_WATCHER = f"""
$ErrorActionPreference = 'SilentlyContinue'
$last = ''
while ($true) {{
    $current = Get-Clipboard -Raw
    if ($current -ne $null -and $current -ne $last) {{
        $last = $current
        Write-Output '{DELIMITER}'
        Write-Output $current
    }}
    Start-Sleep -Milliseconds {{poll_ms}}
}}
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
    buffer: list[str] = []
    try:
        for line in process.stdout:  # type: ignore[union-attr]
            if line.rstrip("\r\n") == DELIMITER:
                if buffer:
                    yield "\n".join(buffer).strip()
                buffer = []
                continue
            buffer.append(line.rstrip("\r\n"))
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

    for n, text in enumerate(stream):
        if n == 0 and skip_existing:
            continue
        yield text


def looks_like_item(text: str) -> bool:
    """Item text always opens with these headers; ordinary copied text does not."""
    head = text.lstrip()[:400]
    return head.startswith("Item Class:") or head.startswith("Rarity:")
