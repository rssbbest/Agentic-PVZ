"""Public and private leaderboards.

Only complete published runs count: a team presses PUBLISH PUBLIC (every
public test case once, one frozen agent.py) or PUBLISH PRIVATE (every hidden
test case once; possible only while the organisers keep it open, and once per
team: the run is locked to its agent.py, see competition.eval_start_decision).
A run's score is the SUM of its test cases.
A run with a test case not played (incomplete, abandoned) and single PLAY games
never reach a board.

Public (live): each team's best published public run. Updates as runs finish.
Private (final): each team's private run, the latest completed one or the best
(organiser setting). Shown only once the organisers publish it.

Both boards are computed once per change and shared by every viewer: a game
or run that finishes calls `changed()`, and the cached board is rebuilt on
the next read (at most every MIN_REBUILD_S; at least every MAX_AGE_S, which
also picks up organiser edits such as a ban).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import load_only

from app.database import AsyncSessionLocal
from app.models import EvalRun, GameSession, Team
from app.services import accounts, competition

SETTINGS_KEY = "leaderboard"
DEFAULT_SETTINGS = {
    "evalOpen": False,          # teams may PUBLISH PRIVATE (opened by the organisers after the build phase)
    "privatePublished": False,  # the private board is public
    "privateRule": "best",      # "latest" or "best" completed private run per team
}
RULES = ("latest", "best")
COUNTED_EVALS = ("finished",)  # complete runs only: every test case of the board played
MIN_REBUILD_S = 1.0
MAX_AGE_S = 15.0

_version = 0
_floor = 0  # boards built before the last forced change are never served
_tick: asyncio.Event | None = None  # set (and replaced) at every change
_cache: dict[str, tuple[int, float, dict]] = {}
_lock = asyncio.Lock()


def changed(force: bool = False) -> None:
    """A game or eval finished, or a setting changed: rebuild the boards.
    `force` (settings) skips the debounce, so the next read is already new."""
    global _version, _floor, _tick
    _version += 1
    if force:
        _floor = _version
    if _tick is not None:
        _tick.set()
        _tick = None


def version() -> int:
    return _version


async def wait_for_change(since: int, timeout: float) -> None:
    """Return once the boards changed after version `since`, or after `timeout`."""
    global _tick
    if _version != since:
        return
    if _tick is None:
        _tick = asyncio.Event()
    tick = _tick
    try:
        await asyncio.wait_for(tick.wait(), timeout)
    except asyncio.TimeoutError:
        pass


def _stamp(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _assign_ranks(rows: list[dict]) -> list[dict]:
    """Equal scores share a rank (1, 2, 2, 4)."""
    for i, row in enumerate(rows):
        row["rank"] = rows[i - 1]["rank"] if i and row["score"] == rows[i - 1]["score"] else i + 1
    return rows


def rank_runs(runs: Iterable[Any], played: dict[int, int], teams: dict[int, Any], rule: str,
              case_scores: dict[int, dict[str, float]] | None = None) -> list[dict]:
    """`runs`: completed published runs (status finished/incomplete, with a
    score: the sum over the run's test cases). One run per team counts: its
    best or its latest (`rule`). Teams not in `teams` (banned, not approved)
    are left out. Ties go to the run that finished first."""
    chosen: dict[int, Any] = {}
    counts: dict[int, int] = {}
    for run in runs:
        if run.team_id not in teams or run.score is None or run.status not in COUNTED_EVALS:
            continue
        counts[run.team_id] = counts.get(run.team_id, 0) + 1
        current = chosen.get(run.team_id)
        if current is None:
            chosen[run.team_id] = run
        elif rule == "best":
            if (run.score, current.finished_at) > (current.score, run.finished_at):
                chosen[run.team_id] = run
        elif (run.finished_at, run.id) > (current.finished_at, current.id):
            chosen[run.team_id] = run
    rows = []
    for run in chosen.values():
        codes = list(run.case_codes or [])
        row = {
            "teamID": accounts.team_code(run.team_id), "teamName": teams[run.team_id].name,
            "score": round(float(run.score), 2), "evalId": run.id, "status": run.status,
            "played": played.get(run.id, 0), "totalCases": len(codes),
            "evals": counts[run.team_id], "finishedAt": _stamp(run.finished_at), "_at": run.finished_at,
        }
        if case_scores is not None:
            scores = case_scores.get(run.id, {})
            row["cases"] = {c: (round(scores[c], 2) if c in scores else None) for c in codes}
        rows.append(row)
    rows.sort(key=lambda r: (-r["score"], r["_at"]))
    for r in rows:
        del r["_at"]
    return _assign_ranks(rows)


async def get_settings(db) -> dict:
    s = await competition.get_setting(db, SETTINGS_KEY, DEFAULT_SETTINGS)
    return {k: s.get(k, v) for k, v in DEFAULT_SETTINGS.items()}


async def _eligible_teams(db) -> dict[int, Any]:
    rows = (await db.execute(select(Team).where(Team.banned.is_(False), Team.approved.is_(True)))).scalars().all()
    return {t.id: t for t in rows}


async def _runs(db, board: str) -> tuple[list[Any], dict[int, int], dict[int, dict[str, float]]]:
    """Completed runs of a board, the games each played, and their per-case scores."""
    runs = (await db.execute(select(EvalRun).options(load_only(  # not the agent sources and logs
        EvalRun.id, EvalRun.team_id, EvalRun.status, EvalRun.score, EvalRun.case_codes, EvalRun.finished_at)).where(
        EvalRun.board == board, EvalRun.status.in_(COUNTED_EVALS), EvalRun.score.is_not(None)))).scalars().all()
    if not runs:
        return [], {}, {}
    games = (await db.execute(
        select(GameSession.eval_id, GameSession.eval_slot, GameSession.score, GameSession.success).where(
            GameSession.eval_id.in_([r.id for r in runs]), GameSession.finished_at.is_not(None))
        .order_by(GameSession.finished_at))).all()
    by_run: dict[int, list[Any]] = {}
    for g in games:
        by_run.setdefault(g.eval_id, []).append(g)
    played: dict[int, int] = {}
    scores: dict[int, dict[str, float]] = {}
    for run in runs:  # the same reading of a run's games as its own page and the admin's
        slots = competition.slot_results(by_run.get(run.id, []), len(run.case_codes))
        played[run.id] = sum(s["played"] for s in slots)
        scores[run.id] = {run.case_codes[s["slot"] - 1]: float(s["score"] or 0.0) for s in slots if s["played"]}
    return list(runs), played, scores


async def _build_public(db) -> dict:
    runs, played, scores = await _runs(db, "public")
    return {"board": "public", "cases": [c.code for c in await competition.public_cases(db)],
            "rows": rank_runs(runs, played, await _eligible_teams(db), "best", scores)}


async def _build_private(db) -> dict:
    settings = await get_settings(db)
    runs, played, _ = await _runs(db, "private")
    return {"board": "private", "rule": settings["privateRule"], "published": settings["privatePublished"],
            "hiddenCases": len(await competition.hidden_cases(db)),
            "rows": rank_runs(runs, played, await _eligible_teams(db), settings["privateRule"])}


def _fresh(cached: tuple[int, float, dict] | None) -> bool:
    if cached is None or cached[0] < _floor:
        return False
    age = time.monotonic() - cached[1]
    return age < MAX_AGE_S and (cached[0] == _version or age < MIN_REBUILD_S)


async def board(name: str) -> dict:
    """The current board ("public" or "private"), from the shared cache."""
    if _fresh(_cache.get(name)):
        return _cache[name][2]
    async with _lock:
        if _fresh(_cache.get(name)):
            return _cache[name][2]
        version = _version
        async with AsyncSessionLocal() as db:
            data = await (_build_public if name == "public" else _build_private)(db)
        data["updatedAt"] = competition.now().isoformat()
        _cache[name] = (version, time.monotonic(), data)
        return data
