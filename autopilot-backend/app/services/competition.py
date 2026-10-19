"""Competition flow: test cases, the game sessions the SDK starts for them,
eval runs over the hidden test cases, and the registration form.

The SDK picks what is played, not the game: it adds `testCase` (play) or
`evalId` + `evalSlot` (eval) to the game's POST /api/team, with the team's
login token. The backend answers with that test case's level and a session
id, and later scores the session's /api/results into the GameSession row.
"""
from __future__ import annotations

import hashlib
import json
import zlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CompetitionSetting, EvalRun, GameSession, Team, TestCase
from app.schemas import GameSettings, SpawnSettings, TeamDataOut

logger = logging.getLogger(__name__)

TEST_CASE_DIR = Path(__file__).resolve().parents[2] / "test_cases"
PUBLIC_SEED = TEST_CASE_DIR / "reinforcement_test_cases.json"
HIDDEN_SEED = TEST_CASE_DIR / "hidden_test_cases.json"
MAX_COINS = 1_000_000  # a level's starting coins
MAX_SEED = 2**31 - 1


def case_seed(code: str) -> int:
    """A test case's spawn seed: the same for every team and every game, so the
    same agent gets the same score (build11 spawns from it)."""
    return zlib.crc32(code.strip().upper().encode()) % (MAX_SEED - 1) + 1

DEFAULT_FORM = {
    "title": "Autopilot: team registration",
    "description": "Register your team. You log in to the game with the team ID shown "
                   "after registering and the password you choose here.",
    "open": True,
    "autoApprove": False,
    "questions": [
        {"id": "members", "label": "Team members (one name per line)", "type": "textarea", "required": True},
        {"id": "email", "label": "Contact email", "type": "email", "required": True},
        {"id": "phone", "label": "Contact phone", "type": "text", "required": False},
        {"id": "college", "label": "College or organisation", "type": "text", "required": False},
    ],
}
QUESTION_TYPES = {"text", "textarea", "email", "number", "url", "select", "checkbox"}


def now() -> datetime:
    return datetime.now(timezone.utc)


# ── Levels ────────────────────────────────────────────────────────────────────
ENEMY_IDS = range(4)     # Unity prefab indices (playerGuide.md); the game does not check them
DEFENCE_IDS = range(4)
GRID_W, GRID_H = 11, 6


def _bad(text: str) -> HTTPException:
    return HTTPException(422, f"Invalid level: {text}")


def _int(value: Any, what: str, allowed: range) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in allowed:
        raise _bad(f"{what} must be a whole number from {allowed.start} to {allowed.stop - 1}, got {value!r}")
    return value


def normalise_level(level: dict[str, Any]) -> dict[str, Any]:
    """A level as stored, checked against what the game accepts: the Unity build
    trusts every value (it indexes its prefab lists with the ids), so a bad
    level must be refused here, not sent to the game."""
    if not isinstance(level, dict) or not isinstance(level.get("enemies"), list) or not level["enemies"]:
        raise HTTPException(422, "A level needs a non-empty 'enemies' list")
    enemies, counts = [], {}
    for e in level["enemies"]:
        if not isinstance(e, dict) or "enemyId" not in e:
            raise HTTPException(422, "Every enemy entry needs enemyId and count >= 1")
        eid = _int(e["enemyId"], "enemyId", ENEMY_IDS)
        count = e.get("count", 0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise HTTPException(422, "Every enemy entry needs enemyId and count >= 1")
        if eid in counts:
            raise _bad(f"enemyId {eid} is listed twice: put its total in one entry")
        rows = e.get("allowedRows")
        if rows is not None:
            if not isinstance(rows, list):
                raise _bad(f"allowedRows of enemyId {eid} must be a list of rows")
            rows = sorted({_int(r, f"allowedRows of enemyId {eid}", range(GRID_H)) for r in rows})
        counts[eid] = count
        enemies.append({**e, "enemyId": eid, "count": count, **({"allowedRows": rows} if rows is not None else {})})
    spawn = {**SpawnSettings().model_dump(), **(level.get("spawnSettings") or {})}
    game = {**GameSettings().model_dump(), **(level.get("gameSettings") or {})}
    for name, value in spawn.items():
        if name in ("spawnSequence", "seed"):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise _bad(f"spawnSettings.{name} must be a number >= 0, got {value!r}")
    seq = spawn.get("spawnSequence")
    if seq:
        if not isinstance(seq, list):
            raise _bad("spawnSettings.spawnSequence must be a list of enemyIds")
        for eid in seq:
            _int(eid, "spawnSequence entry", ENEMY_IDS)
            if eid not in counts:
                raise _bad(f"spawnSequence uses enemyId {eid}, which is not in enemies")
        for eid, n in counts.items():
            if seq.count(eid) > n:
                raise _bad(f"spawnSequence uses enemyId {eid} {seq.count(eid)} times, but its count is {n}")
    elif "spawnSequence" in spawn:
        spawn.pop("spawnSequence")
    if spawn.get("seed") is None:
        spawn.pop("seed", None)
    else:
        _int(spawn["seed"], "spawnSettings.seed", range(1, MAX_SEED + 1))
    for name in ("reinforcementsEnabled", "sendScreenshotToo"):
        if not isinstance(game.get(name), bool):
            raise _bad(f"gameSettings.{name} must be true or false")
    for name in ("reinforcementIntervalSeconds", "reinforcementHoldSeconds"):
        value = game.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise _bad(f"gameSettings.{name} must be a number >= 0, got {value!r}")
    defences, cells = [], set()
    for d in level.get("defencePlacementData") or []:
        pos = d.get("defenceGridPosition") if isinstance(d, dict) else None
        if not isinstance(pos, dict):
            raise _bad("every starting defence needs defenceId and defenceGridPosition {x, y}")
        did = _int(d.get("defenceId"), "defenceId", DEFENCE_IDS)
        cell = (_int(pos.get("x"), "defence x", range(GRID_W)), _int(pos.get("y"), "defence y", range(GRID_H)))
        if cell in cells:
            raise _bad(f"two starting defences on cell {cell}")
        cells.add(cell)
        defences.append({"defenceId": did, "defenceGridPosition": {"x": cell[0], "y": cell[1]}})
    out = {"enemies": enemies, "spawnSettings": spawn, "gameSettings": game, "defencePlacementData": defences}
    coins = level.get("coins")
    if coins is not None:
        if isinstance(coins, bool) or not isinstance(coins, (int, float)) or not 0 < coins <= MAX_COINS:
            raise _bad(f"coins (starting coins) must be a number from 1 to {MAX_COINS}, got {coins!r}")
        out["coins"] = int(coins) if float(coins).is_integer() else float(coins)
    if isinstance(level.get("description"), str) and level["description"].strip():
        out["description"] = level["description"].strip()
    return out


def level_reply(session_id: str, defences: list[dict], enemies: list[dict],
                spawn: dict | None, game: dict | None, tc_budget: float | None = None,
                coins: float | None = None, seed: int | None = None) -> TeamDataOut:
    """The /api/team reply for a level: settings, budget and coin counter."""
    from app.services.evaluator_service import (
        BOUNTY_PER_KILL, calculate_test_case_budget, calculate_unlocked_budget, get_unit_cost, unit_costs,
        wave_size,
    )
    from app.services.reinforcement_service import record_session_initial_defences, record_session_starting_coins

    spawn_out = {**SpawnSettings().model_dump(), **(spawn or {})}
    if spawn_out.get("seed") is None:
        spawn_out["seed"] = seed
    game_out = {**GameSettings().model_dump(), **(game or {})}
    game_out["sendScreenshotToo"] = False
    enemies_out = []
    for e in enemies:
        entry = dict(e)
        if not entry.get("allowedRows"):
            entry["allowedRows"] = list(range(GRID_H))  # the API guide: missing/empty = any row
        enemies_out.append(entry)
    match_budget = calculate_test_case_budget(tc_budget, wave_size(enemies), coins)
    opening = calculate_unlocked_budget(match_budget, enemies_killed_so_far=0, starting_coins=coins)
    remaining = opening - float(sum(get_unit_cost(d) for d in defences))
    record_session_initial_defences(session_id, defences)
    record_session_starting_coins(session_id, coins)
    return TeamDataOut(
        sessionID=session_id, defencePlacementData=defences, enemies=enemies_out,
        spawnSettings=SpawnSettings(**spawn_out), gameSettings=GameSettings(**game_out),
        budget=opening, matchBudget=match_budget, unitCosts=unit_costs(),
        bountyPerKill=BOUNTY_PER_KILL, coins=int(max(0.0, remaining)),
    )


async def case_by_code(db: AsyncSession, code: str) -> TestCase | None:
    code = (code or "").strip().upper()
    return (await db.execute(select(TestCase).where(TestCase.code == code))).scalars().first()


async def public_cases(db: AsyncSession) -> list[TestCase]:
    rows = (await db.execute(
        select(TestCase).where(TestCase.hidden.is_(False), TestCase.active.is_(True))
        .order_by(TestCase.position, TestCase.code))).scalars().all()
    return list(rows)


async def hidden_cases(db: AsyncSession) -> list[TestCase]:
    rows = (await db.execute(
        select(TestCase).where(TestCase.hidden.is_(True), TestCase.active.is_(True))
        .order_by(TestCase.position, TestCase.code))).scalars().all()
    return list(rows)


async def start_session(db: AsyncSession, team: Team, request: Any, logged_in: int | None) -> TeamDataOut:
    """Start a game of the test case (or eval slot) the SDK asked for."""
    if not team.approved:
        raise HTTPException(403, "Your team is waiting for the organisers' approval")
    eval_id = getattr(request, "evalId", None)
    mode = "eval" if eval_id else "play"
    slot = None
    if mode == "eval":
        if logged_in != team.id:
            raise HTTPException(401, "Publishing needs your team's login")
        run = await db.get(EvalRun, int(eval_id))
        if run is None or run.team_id != team.id:
            raise HTTPException(404, "Unknown eval run")
        if run.status != "running":
            raise HTTPException(409, f"This eval run is {run.status}; start a new one")
        sent = (getattr(request, "agentSha256", None) or "").strip().lower()
        if sent != run.agent_sha256:
            raise HTTPException(409, f"agent.py ({sent[:12] or 'not sent'}) is not the one frozen for run "
                                     f"#{run.id} ({run.agent_sha256[:12]}): every test case of a run plays the "
                                     "same agent.py")
        slot = int(getattr(request, "evalSlot", None) or 0)
        if not 1 <= slot <= len(run.case_codes):
            raise HTTPException(422, f"evalSlot must be 1..{len(run.case_codes)}")
        # One start per slot: quitting a game that goes badly and starting the
        # slot again would let a team retry a level until it is lucky.
        started = (await db.execute(select(GameSession).where(
            GameSession.eval_id == run.id, GameSession.eval_slot == slot))).scalars().first()
        if started is not None:
            raise HTTPException(409, f"Test case {slot} of this run was already started; a run plays each "
                                     "test case once. Publish again for a new run.")
        case = await case_by_code(db, run.case_codes[slot - 1])
        if case is None:
            raise HTTPException(410, "That test case was removed; publish again")
    else:
        case = await case_by_code(db, getattr(request, "testCase", None) or "")
        if case is None or case.hidden or not case.active:
            raise HTTPException(404, f"Unknown test case '{getattr(request, 'testCase', '')}'")
    level = case.level
    session_id = "c" + uuid.uuid4().hex[:15]
    db.add(GameSession(
        session_id=session_id, team_id=team.id, test_case=case.code, mode=mode,
        eval_id=int(eval_id) if eval_id else None, eval_slot=slot,
        agent_sha256=(getattr(request, "agentSha256", None) or None),
    ))
    await db.commit()
    logger.info("Game session %s: team %s, %s %s%s", session_id, team.id, mode, case.code,
                f" (eval {eval_id} slot {slot})" if slot else "")
    return level_reply(session_id, list(level.get("defencePlacementData") or []), level["enemies"],
                       level.get("spawnSettings"), level.get("gameSettings"), coins=level.get("coins"),
                       seed=case_seed(case.code) if case.hidden else None)


async def session_wave(db: AsyncSession, session_id: str) -> int | None:
    """Enemies in the session's test case, for scoring; None if not a case session."""
    from app.services.evaluator_service import wave_size

    game = await db.get(GameSession, session_id) if session_id else None
    if game is None:
        return None
    case = await case_by_code(db, game.test_case)
    return wave_size(case.level["enemies"]) if case else None


async def session_coins(db: AsyncSession, session_id: str) -> float | None:
    """The starting coins of the session's test case; None = the event default."""
    game = await db.get(GameSession, session_id) if session_id else None
    case = await case_by_code(db, game.test_case) if game is not None else None
    return case.level.get("coins") if case else None


# ── Result checks ────────────────────────────────────────────────────────────
# The game runs on the player's laptop, so its report is checked against what
# the backend knows before it is scored: the level, the time, the pauses the
# backend saw and the placements its referee approved.
PROCESS_STARTED = datetime.now(timezone.utc)  # pauses and placements of older games are not in memory
MAX_GAME_SPEED = 1.6  # game time per real second (build10 had a 1.5x button; build11 has none)
SPAWN_DELAY_S = 0.3   # the spawn effect before each enemy appears


def min_spawn_seconds(level: dict) -> float:
    """Game time before the last enemy of the wave can appear."""
    spawn = {**SpawnSettings().model_dump(), **(level.get("spawnSettings") or {})}
    count = sum(int(e["count"]) for e in level["enemies"])
    wait, total = float(spawn["interArrivalTime"]), SPAWN_DELAY_S
    for _ in range(max(0, count - 1)):
        total += max(wait, float(spawn["minInterArrivalTime"])) + SPAWN_DELAY_S
        wait -= float(spawn["timeDecreaseRate"])
    return total


def result_problem(started_at: datetime, level: dict, result: dict, pauses_seen: int,
                   approved: list[dict], finished_at: datetime | None = None) -> str | None:
    """Why a test-case game's report can't be true, or None."""
    wave = sum(int(e["count"]) for e in level["enemies"])
    duration = float(result.get("durationSeconds") or 0.0)
    killed = int(result.get("enemiesKilled") or 0)
    real = ((finished_at or now()) - started_at).total_seconds()
    if duration > real * MAX_GAME_SPEED + 10:
        return f"the game reports {duration:.0f} s of play, but it started {real:.0f} s ago"
    if killed > wave:
        return f"the game reports {killed} kills, but the wave has {wave} enemies"
    if result.get("success") is True:
        if killed < wave:
            return f"a win needs all {wave} enemies killed, the game reports {killed}"
        if duration < 0.8 * min_spawn_seconds(level):
            return (f"won after {duration:.0f} s, but this wave takes at least "
                    f"{min_spawn_seconds(level):.0f} s to spawn")
    if started_at < PROCESS_STARTED:
        return None  # the backend restarted during this game: its pauses and placements are gone
    game = {**GameSettings().model_dump(), **(level.get("gameSettings") or {})}
    if duration > 5 and pauses_seen == 0:
        return "the backend saw none of this game's pauses (the SDK reports every pause)"
    interval = float(game.get("reinforcementIntervalSeconds") or 0)
    if game.get("reinforcementsEnabled") and interval > 0:
        expected = int(duration // interval)
        if pauses_seen < expected // 2:
            return f"the backend saw {pauses_seen} pauses of this game, a {duration:.0f} s game has about {expected}"
    allowed: dict[tuple, int] = {}
    for p in approved:
        pos = (p.get("defenceGridPosition") or {}) if isinstance(p, dict) else {}
        if "x" in pos and "y" in pos:
            cell = (round(float(pos["x"])), round(float(pos["y"])))
            allowed[cell] = allowed.get(cell, 0) + 1
    unplaced = 0   # games before build11 report a defence that never hit anything at (0, 0)
    for d in result.get("defences") or []:
        pos = d.get("position") if isinstance(d, dict) else None
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            cell = (round(float(pos["x"])), round(float(pos["y"])))
            if allowed.get(cell, 0) > 0:
                allowed[cell] -= 1
            elif cell == (0, 0) and not float(d.get("damageDealt") or 0):
                unplaced += 1
            else:
                return f"the game reports a defence at {cell} that the referee never approved"
    if unplaced > sum(allowed.values()):
        return "the game reports more defences than the referee approved"
    return None


async def check_result(db: AsyncSession, session_id: str, result: dict, logged_in: int | None,
                       pauses_seen: int, approved: list[dict]) -> str | None:
    """For a test-case game: the result must come with the team's login, and be
    possible (`result_problem`). Returns why it can't be scored, or None."""
    from app.config import settings

    game = await db.get(GameSession, session_id) if session_id else None
    if game is None or game.finished_at is not None:
        return None  # not a test-case game, or already scored (the first result stays)
    if (settings.REQUIRE_LOGIN or game.mode == "eval") and logged_in != game.team_id:
        raise HTTPException(401, "This game's result needs the team's login (the SDK sends it)")
    case = await case_by_code(db, game.test_case)
    if case is None:
        return None
    problem = result_problem(game.started_at, case.level, result, pauses_seen, approved)
    if problem:
        logger.warning("Result refused for session %s (team %s, %s): %s", session_id, game.team_id,
                       game.test_case, problem)
    return problem


async def record_result(db: AsyncSession, session_id: str, result: dict, evaluator_output: dict,
                        score: float | None, pauses: int, placements: list | None) -> GameSession | None:
    game = await db.get(GameSession, session_id) if session_id else None
    if game is None:
        return None
    if game.finished_at is not None:
        logger.warning("Session %s already has a result; keeping the first", session_id)
        return game
    game.result, game.evaluator_output = result, evaluator_output
    game.score = float(score) if score is not None else 0.0
    game.success = bool(result.get("success"))
    game.pauses, game.placements = pauses, placements
    game.finished_at = now()
    await db.commit()
    if game.eval_id is not None:
        # The run's last test case: score the run now, so it reaches the board
        # even if the SDK stops before it reports the run finished.
        run = await db.get(EvalRun, game.eval_id)
        if run is not None and run.status == "running" and all(
                s["played"] for s in await eval_progress(db, run)):
            await close_run(db, run)
            await db.commit()
    from app.services import leaderboards
    leaderboards.changed()
    return game


# A private (hidden test case) run locks the team to its agent.py: only that
# file can continue it, and a finished one cannot be run again. Organisers can
# void a run (status "void") to lift the lock.
LOCKING = ("running", "finished", "incomplete", "abandoned")


def eval_start_decision(board: str, runs: list[Any], agent_sha: str) -> tuple[str, Any, str | None]:
    """What PUBLISH does, given the team's earlier runs of this board (oldest
    first): ("new", None, None), ("resume", run, None) or ("refuse", run, why).

    Private: the first run fixes the agent.py. The same file resumes the
    latest run where it stopped (Ctrl+C, a crash); a different file is
    refused; a finished (or closed) run is not played again.
    Public: the same file resumes its unfinished run; anything else starts a
    new run (the board counts each team's best).
    """
    if board == "private":
        locked = [r for r in runs if r.status in LOCKING]
        if not locked:
            return "new", None, None
        latest = locked[-1]
        other = next((r for r in locked if r.agent_sha256 != agent_sha), None)
        if other is not None:
            return "refuse", other, (
                f"Your private run #{other.id} plays agent.py {other.agent_sha256[:12]}; this agent.py is "
                f"{agent_sha[:12]}. A private run plays one agent.py over every hidden test case, so only that "
                "same file can continue it. If something went wrong, ask the organisers.")
        if latest.status == "finished":
            return "refuse", latest, (
                f"Your private run #{latest.id} is complete (score {latest.score or 0:g}): every hidden test case "
                "was played once with this agent.py.")
        if latest.status == "incomplete":
            return "refuse", latest, (
                f"Your private run #{latest.id} was closed with {latest.score or 0:g} points (the test cases it did "
                "not play count 0). If something went wrong, ask the organisers.")
        return "resume", latest, None
    running = [r for r in runs if r.status == "running"]
    if running and running[-1].agent_sha256 == agent_sha:
        return "resume", running[-1], None
    return "new", None, None


def slot_results(games: list[Any], slots: int) -> list[dict]:
    """Each test case (slot) of a run from its finished games, oldest first:
    the first game that finished a slot counts. Shared by the participant's
    replies, the admin views and the leaderboards, so they always agree."""
    by_slot: dict[int, Any] = {}
    for g in games:
        by_slot.setdefault(g.eval_slot, g)
    return [{"slot": i, "played": i in by_slot,
             "score": by_slot[i].score if i in by_slot else None,
             "won": by_slot[i].success if i in by_slot else None}
            for i in range(1, slots + 1)]


def run_total(slots: list[dict]) -> tuple[float, str]:
    """A run's score (the sum over its test cases, one not played counting 0)
    and status (finished if every case was played, else incomplete)."""
    return (round(sum(s["score"] or 0.0 for s in slots), 2),
            "finished" if all(s["played"] for s in slots) else "incomplete")


async def eval_progress(db: AsyncSession, run: EvalRun) -> list[dict]:
    games = (await db.execute(select(GameSession).where(
        GameSession.eval_id == run.id, GameSession.finished_at.is_not(None))
        .order_by(GameSession.finished_at))).scalars().all()
    return slot_results(list(games), len(run.case_codes))


async def broken_slots(db: AsyncSession, run: EvalRun) -> list[int]:
    """Test cases of a run that were started but never finished: one start per
    test case, so the run can never be complete."""
    rows = (await db.execute(select(GameSession.eval_slot, GameSession.finished_at).where(
        GameSession.eval_id == run.id))).all()
    done = {slot for slot, finished in rows if finished is not None}
    return sorted({slot for slot, finished in rows if finished is None and slot not in done})


async def close_run(db: AsyncSession, run: EvalRun) -> list[dict]:
    """Score a run now (see run_total). The caller commits."""
    slots = await eval_progress(db, run)
    run.score, run.status = run_total(slots)
    run.finished_at = now()
    return slots


# ── Settings ──────────────────────────────────────────────────────────────────
async def get_setting(db: AsyncSession, key: str, default: dict) -> dict:
    row = await db.get(CompetitionSetting, key)
    return {**default, **row.value} if row else dict(default)


async def put_setting(db: AsyncSession, key: str, value: dict) -> None:
    row = await db.get(CompetitionSetting, key)
    if row is None:
        db.add(CompetitionSetting(key=key, value=value))
    else:
        row.value = value
    await db.commit()


def validate_form(form: dict) -> dict:
    questions, seen = [], set()
    for q in form.get("questions") or []:
        qid = str(q.get("id") or "").strip()
        if not qid or qid in seen or qid in {"teamName", "password"}:
            raise HTTPException(422, f"Question ids must be unique and not teamName/password: {qid!r}")
        if q.get("type", "text") not in QUESTION_TYPES:
            raise HTTPException(422, f"Question type must be one of {sorted(QUESTION_TYPES)}")
        seen.add(qid)
        questions.append({"id": qid, "label": str(q.get("label") or qid), "type": q.get("type", "text"),
                          "required": bool(q.get("required")), "options": list(q.get("options") or []),
                          "help": str(q.get("help") or "")})
    return {"title": str(form.get("title") or DEFAULT_FORM["title"]),
            "description": str(form.get("description") or ""),
            "open": bool(form.get("open", True)), "autoApprove": bool(form.get("autoApprove", False)),
            "questions": questions}


def check_answers(form: dict, answers: dict) -> dict:
    clean = {}
    for q in form["questions"]:
        value = answers.get(q["id"])
        if isinstance(value, str):
            value = value.strip()
        empty = value in (None, "", [], False) if q["type"] != "number" else value in (None, "")
        if empty:
            if q["required"]:
                raise HTTPException(422, f"Please answer: {q['label']}")
            continue
        if q["type"] == "email" and ("@" not in str(value) or "." not in str(value).split("@")[-1]):
            raise HTTPException(422, f"Not an email address: {q['label']}")
        if q["type"] == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise HTTPException(422, f"Not a number: {q['label']}")
        if q["type"] == "select" and q["options"] and value not in q["options"]:
            raise HTTPException(422, f"Choose one of the options: {q['label']}")
        clean[q["id"]] = value if not isinstance(value, str) else value[:2000]
    return clean


# ── Seeding ───────────────────────────────────────────────────────────────────
def _seed_cases(path: Path, hidden: bool) -> list[TestCase]:
    if not path.exists():
        return []
    cases = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for i, case in enumerate(cases):
        raw = str(case.get("name") or "")
        code = str(case.get("code") or (f"{'H' if hidden else 'TC'}{int(case.get('testCaseId', i + 1)):02d}"))
        name = raw.split("_", 1)[1].replace("_", " ") if raw.upper().startswith(code.upper() + "_") else raw
        out.append(TestCase(code=code.upper(), name=name or code, hidden=hidden, active=True, position=i,
                            level=normalise_level(case)))
    return out


async def seed_test_cases(db: AsyncSession) -> None:
    """Load the shipped test cases. An empty table gets them all. After that, a
    test-case file that changed since the last start (someone pushed new
    levels) updates its cases by code; cases added in /admin stay, and edits
    made in /admin stay until the file itself changes again."""
    empty = not (await db.execute(select(TestCase.id).limit(1))).first()
    for path, hidden in ((PUBLIC_SEED, False), (HIDDEN_SEED, True)):
        if not path.exists():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        key = f"test_case_file:{path.name}"
        last = (await get_setting(db, key, {})).get("sha")
        try:
            rows = _seed_cases(path, hidden)
        except (HTTPException, ValueError) as exc:  # a broken file must not stop the server
            logger.error("Skipping %s: %s", path.name, getattr(exc, "detail", exc))
            continue
        if empty:
            db.add_all(rows)
            logger.info("Seeded %d %s test cases from %s", len(rows), "hidden" if hidden else "public", path.name)
        elif last and last != digest:
            stored = {c.code: c for c in (await db.execute(select(TestCase))).scalars()}
            for row in rows:
                case = stored.get(row.code)
                if case is None:
                    db.add(row)
                else:
                    case.name, case.level, case.hidden = row.name, row.level, hidden
            logger.info("%s changed: updated %d test cases from it", path.name, len(rows))
        await put_setting(db, key, {"sha": digest})  # commits