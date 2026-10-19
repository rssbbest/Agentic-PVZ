"""Participant-facing terminal output for `autopilot play`.

Short timestamped lines; failures in red so they stand out. Colour is used
only on a terminal and never when NO_COLOR is set. On Windows the console's
ANSI (VT) mode is switched on once; agent processes share that console.
"""
from __future__ import annotations

import logging
import os
import sys
import time

_STYLES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33",
           "blue": "34", "magenta": "35", "cyan": "36"}
_enabled: bool | None = None
_sinks: list = []  # extra destinations for every line, e.g. a run log (plain text)


def add_sink(sink) -> None:
    _sinks.append(sink)


def remove_sink(sink) -> None:
    if sink in _sinks:
        _sinks.remove(sink)


def _enable_windows_vt() -> bool:
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # VT processing
    except Exception:
        return False


def color_enabled() -> bool:
    global _enabled
    if _enabled is None:
        if os.environ.get("NO_COLOR") or not getattr(sys.stdout, "isatty", lambda: False)():
            _enabled = False
        elif os.name == "nt":
            _enabled = _enable_windows_vt()
        else:
            _enabled = True
    return _enabled


def paint(text: str, *styles: str) -> str:
    if not styles or not color_enabled():
        return text
    return f"\033[{';'.join(_STYLES[s] for s in styles)}m{text}\033[0m"


def say(text: str = "", *styles: str, stamp: bool = True) -> None:
    prefix = paint(time.strftime("%H:%M:%S "), "dim") if stamp and text else ""
    line = prefix + paint(text, *styles)
    print(line, flush=True)
    for sink in list(_sinks):
        try:
            sink(line)
        except Exception:
            pass


def fail(text: str) -> None:
    say("✗ " + text, "red")


class ParticipantLogHandler(logging.Handler):
    """Shows the SDK's own log records the same way: warnings and errors in red."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info and record.levelno >= logging.ERROR:
                message += "\n" + "".join(logging.Formatter().formatException(record.exc_info))
            if record.levelno >= logging.WARNING:
                fail(message)
            else:
                say(message)
        except Exception:
            self.handleError(record)
