"""Double-click support: open the console in a terminal window.

Windows gives a double-clicked .exe its own console, and macOS Finder opens a
Unix executable in Terminal. A Linux file manager starts it with no terminal
at all (stdin is /dev/null), so the SDK reopens itself in the first terminal
app it finds. Never triggered for piped input (scripts, tests) or when
AUTOPILOT_IN_TERMINAL is set (already relaunched, or switched off).
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

# (program, arguments placed before the command to run)
TERMINALS = [
    ("x-terminal-emulator", ["-e"]),
    ("gnome-terminal", ["--"]),
    ("konsole", ["-e"]),
    ("xfce4-terminal", ["-x"]),
    ("mate-terminal", ["-x"]),
    ("qterminal", ["-e"]),
    ("lxterminal", ["-e"]),
    ("tilix", ["-e"]),
    ("kitty", []),
    ("alacritty", ["-e"]),
    ("wezterm", ["start", "--"]),
    ("foot", []),
    ("xterm", ["-e"]),
]


def started_without_terminal() -> bool:
    """True when a Linux file manager started us: no tty and no piped input."""
    if not sys.platform.startswith("linux") or os.environ.get("AUTOPILOT_IN_TERMINAL"):
        return False
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        if os.isatty(0):
            return False
        mode = os.fstat(0).st_mode
    except OSError:
        return True  # no stdin at all
    return stat.S_ISCHR(mode)  # /dev/null, not a pipe or a file


def terminal_command(program: list[str]) -> list[str] | None:
    for name, args in TERMINALS:
        path = shutil.which(name)
        if path:
            return [path, *args, *program]
    return None


def relaunch_in_terminal() -> bool:
    """Reopen this program in a terminal window; True if one was started."""
    program = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "autopilot_sdk"]
    command = terminal_command(program)
    if command is None:
        return False
    env = {**os.environ, "AUTOPILOT_IN_TERMINAL": "1"}
    folder = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    subprocess.Popen(command, cwd=folder, env=env, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True
