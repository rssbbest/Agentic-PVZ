"""Structured local logging + batched telemetry buffer (doc §15, local half).

Full upload (POST /v1/telemetry) is backend-team-blocked; this module
implements the offline part: JSONL file + in-memory event buffer that
the future uploader will drain.
"""
import datetime
import json
import os
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_log_dir_override: str | None = None
_buffer: list[dict] = []
_context: dict = {}


def _default_log_dir() -> Path:
    if _log_dir_override:
        return Path(_log_dir_override)
    return Path(os.path.expanduser("~/.autopilot/logs"))


def configure(match_id: str = "", team_id: str = "", sdk_version: str = "") -> None:
    with _lock:
        _context.clear()
        if match_id:
            _context["match_id"] = match_id
        if team_id:
            _context["team_id"] = team_id
        if sdk_version:
            _context["sdk_version"] = sdk_version


def set_log_dir(path: str) -> None:
    global _log_dir_override
    _log_dir_override = path


def _write(event: dict) -> None:
    with _lock:
        merged = {**_context, **event}
        merged.setdefault("ts", datetime.datetime.now(datetime.timezone.utc).isoformat())
        _buffer.append(merged)
        buf = json.dumps(merged)
        match_id = merged.get("match_id", "local")
    try:
        d = _default_log_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"{match_id}.jsonl", "a", encoding="utf-8") as f:
            f.write(buf + "\n")
    except Exception:
        pass  # logging must never crash a match


def log_event(event_type: str, **fields: Any) -> None:
    _write({"type": event_type, **fields})


def log_violation(event_type: str, **fields: Any) -> None:
    _write({"type": event_type, "violation": True, **fields})


def drain_buffer() -> list[dict]:
    """Pop buffered events for future batched upload. Backend-blocked."""
    with _lock:
        events = list(_buffer)
        _buffer.clear()
        return events


def peek_buffer() -> list[dict]:
    with _lock:
        return list(_buffer)
