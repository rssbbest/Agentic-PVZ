"""Run logs: everything one SDK run did, in a folder of its own.

    autopilot-runs/2026-10-16_18-03-22_play_TC03/
        session.json   what was run (mode, test case, team, backend, SDK
                       version) and, at the end, every game with its score
        events.jsonl   one JSON object per line, in order: login, each level,
                       each pause (the game's snapshot, what the agent was
                       given and returned, the referee's answer, timings), LLM
                       calls, each result with the backend's score
        console.log    the terminal output, without colours
        agent-<sha>.py every version of agent.py that played (play mode picks
                       up edits; eval plays one frozen copy)

`eval` uploads events.jsonl (gzipped) with the run, so organisers can check
that a winning score came from real games.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ANSI = re.compile(r"\x1b\[[0-9;]*m")
DEFAULT_DIR = Path("autopilot-runs")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RunLog:
    def __init__(self, root: Path | str, mode: str, label: str = "", meta: dict | None = None) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        name = "_".join(filter(None, [stamp, mode, re.sub(r"[^A-Za-z0-9-]+", "", label)]))
        self.dir = Path(root).expanduser().resolve() / name
        suffix = 1
        while self.dir.exists():
            suffix += 1
            self.dir = self.dir.with_name(f"{name}-{suffix}")
        self.dir.mkdir(parents=True)
        self._lock = threading.Lock()
        self._events = open(self.dir / "events.jsonl", "a", encoding="utf-8")
        self._console = open(self.dir / "console.log", "a", encoding="utf-8")
        self._agents: set[str] = set()
        self.meta = {"mode": mode, "label": label, "started": datetime.now().isoformat(timespec="seconds"),
                     **(meta or {}), "games": []}
        self._write_meta()
        self.event("session_start", **{k: v for k, v in self.meta.items() if k != "games"})

    def event(self, kind: str, **data: Any) -> None:
        line = json.dumps({"t": round(time.time(), 3), "kind": kind, **data}, default=str)
        with self._lock:
            if not self._events.closed:
                self._events.write(line + "\n")
                self._events.flush()

    def text(self, line: str) -> None:
        with self._lock:
            if not self._console.closed:
                self._console.write(ANSI.sub("", line) + "\n")
                self._console.flush()

    def agent(self, source: bytes) -> str:
        """Keep a copy of this agent.py version; returns its sha256."""
        digest = sha256(source)
        if digest not in self._agents:
            self._agents.add(digest)
            (self.dir / f"agent-{digest[:12]}.py").write_bytes(source)
            self.event("agent_version", sha256=digest, bytes=len(source))
        return digest

    def game(self, row: dict) -> None:
        with self._lock:
            self.meta["games"].append(row)
        self._write_meta()

    def update(self, **fields: Any) -> None:
        with self._lock:
            self.meta.update(fields)
        self._write_meta()

    def _write_meta(self) -> None:
        with self._lock:
            (self.dir / "session.json").write_text(json.dumps(self.meta, indent=2, default=str), encoding="utf-8")

    def gzipped_events(self) -> bytes:
        with self._lock:
            self._events.flush()
        return gzip.compress((self.dir / "events.jsonl").read_bytes())

    def close(self) -> None:
        self.update(finished=datetime.now().isoformat(timespec="seconds"))
        self.event("session_end")
        with self._lock:
            self._events.close()
            self._console.close()
