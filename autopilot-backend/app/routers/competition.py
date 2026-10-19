"""
app/routers/competition.py
──────────────────────────
Competition endpoints: team login and registration, test cases, eval runs,
and the organiser API behind the admin dashboard (/admin, X-Admin-Secret).

Participants (through the SDK, or the registration page):
    POST /api/login                 {teamID, password} -> {token, teamID, teamName, llmToken}
    GET  /api/registration/form     the registration questions
    POST /api/register              {teamName, password, answers} -> {teamID, approved}
    GET  /api/test-cases            public test cases [{code, name}]
    GET  /api/eval/info             how many hidden test cases an eval plays
    POST /api/eval/start            (login) {agentSource, sdkVersion} -> {evalId, slots}
    GET  /api/eval/{id}             (login) progress
    POST /api/eval/{id}/finish      (login) {log, summary} -> {score, slots}
    GET  /api/me                    (login) the team's own record

Organiser API (X-Admin-Secret), used by /admin: overview, teams (incl.
POST /admin/api/teams/import for a bulk import), sessions, evals, test cases,
registration form. Full list: docs/COMPETITION.md.

Pages: GET /register (registration form), GET /admin (dashboard).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import gzip
import hashlib
from pathlib import Path
from typing import Any, Literal

from fastapi import Header, APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.config import settings
from app.database import get_db
from app.models import EvalRun, GameSession, Team, TestCase
from app.security import verify_admin
from app.services import accounts, competition, leaderboards, sdk_updates

router = APIRouter(tags=["Competition"])
admin = APIRouter(prefix="/admin/api", tags=["Admin"], dependencies=[Depends(verify_admin)])
STATIC = Path(__file__).resolve().parents[1] / "static"
MAX_LOG_BYTES = 20 * 1024 * 1024
MAX_AGENT_BYTES = 512 * 1024


class LoginIn(BaseModel):
    teamID: str = Field(..., min_length=1, max_length=20)
    password: str = Field(..., min_length=1, max_length=200)


class RegisterIn(BaseModel):
    teamName: str = Field(..., min_length=2, max_length=60)
    password: str = Field(..., min_length=8, max_length=200)
    answers: dict[str, Any] = {}


class EvalStartIn(BaseModel):
    agentSource: str = Field(..., min_length=1)
    sdkVersion: str | None = None
    board: Literal["public", "private"] = Field(
        "private", description="public: every public test case (live board); private: every hidden one (final board)")


class EvalFinishIn(BaseModel):
    log: str | None = Field(None, description="base64 of the gzipped run log (JSON lines)")
    summary: dict[str, Any] = {}


async def _team(db: AsyncSession, team_id: int) -> Team:
    team = await db.get(Team, team_id)
    if team is None:
        raise HTTPException(404, "Unknown team")
    return team


def _parse_team_id(text: str) -> int | None:
    from app.services.reinforcement_service import db_id
    text = text.strip()
    return db_id(text[4:] if text.lower().startswith("team") else text)


# ── Login & registration ──────────────────────────────────────────────────────
@router.post("/api/login")
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)):
    team_id = _parse_team_id(body.teamID)
    team = await db.get(Team, team_id) if team_id else None
    ok = bool(team and team.password_hash) and await asyncio.to_thread(
        accounts.check_password, body.password, team.password_hash)
    if not ok:
        raise HTTPException(401, "Wrong team ID or password")
    if team.banned:
        raise HTTPException(403, "This team is banned from the competition")
    if not team.approved:
        raise HTTPException(403, "Your registration is waiting for the organisers' approval")
    return {"token": accounts.issue_token(team.id), "teamID": accounts.team_code(team.id),
            "teamName": team.name, "expiresIn": accounts.TOKEN_TTL_S, "llmToken": accounts.llm_token(team.id)}


@router.get("/api/registration/form")
async def registration_form(db: AsyncSession = Depends(get_db)):
    form = await competition.get_setting(db, "registration_form", competition.DEFAULT_FORM)
    return {k: form[k] for k in ("title", "description", "open", "questions")}


@router.post("/api/register", status_code=201)
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    form = competition.validate_form(
        await competition.get_setting(db, "registration_form", competition.DEFAULT_FORM))
    if not form["open"]:
        raise HTTPException(403, "Registration is closed")
    name = " ".join(body.teamName.split())
    taken = (await db.execute(select(Team.id).where(func.lower(Team.name) == name.lower()))).first()
    if taken:
        raise HTTPException(409, "That team name is taken")
    answers = competition.check_answers(form, body.answers)
    team = Team(name=name, password_hash=await asyncio.to_thread(accounts.hash_password, body.password),
                approved=form["autoApprove"], banned=False, profile={"answers": answers})
    db.add(team)
    await db.commit()
    await db.refresh(team)
    return {"teamID": accounts.team_code(team.id), "teamName": team.name, "approved": team.approved}


@router.get("/api/me")
async def me(team_id: int = Depends(accounts.require_team), db: AsyncSession = Depends(get_db)):
    team = await _team(db, team_id)
    return {"teamID": accounts.team_code(team.id), "teamName": team.name, "approved": team.approved,
            "banned": team.banned, **await _one_team_stats(db, team.id)}


# ── Test cases & eval ─────────────────────────────────────────────────────────
@router.get("/api/test-cases")
async def list_test_cases(db: AsyncSession = Depends(get_db)):
    """Public test cases, in order."""
    return [{"code": c.code, "name": c.name, "enemies": sum(int(e.get("count", 0)) for e in c.level["enemies"])}
            for c in await competition.public_cases(db)]


@router.get("/api/eval/info")
async def eval_info(db: AsyncSession = Depends(get_db)):
    return {"hiddenTestCases": len(await competition.hidden_cases(db)),
            "publicTestCases": len(await competition.public_cases(db)),
            "open": (await leaderboards.get_settings(db))["evalOpen"]}


@router.post("/api/eval/start", status_code=201)
async def eval_start(body: EvalStartIn, team_id: int = Depends(accounts.require_team),
                     db: AsyncSession = Depends(get_db), x_autopilot_sdk: str = Header(default="")):
    if sdk_updates.outdated(x_autopilot_sdk):
        raise HTTPException(sdk_updates.OUTDATED_STATUS, sdk_updates.OUTDATED_MESSAGE)
    team = await _team(db, team_id)
    if team.banned:
        raise HTTPException(403, "This team is banned from the competition")
    private = body.board == "private"
    board_settings = await leaderboards.get_settings(db)
    if private and not board_settings["evalOpen"]:
        raise HTTPException(403, "PUBLISH PRIVATE (the hidden test cases) is not open yet: the organisers "
                                 "open it when the build phase ends. Until then, PUBLISH PUBLIC.")
    if len(body.agentSource.encode()) > MAX_AGENT_BYTES:
        raise HTTPException(413, "agent.py is too large")
    cases = await (competition.hidden_cases(db) if private else competition.public_cases(db))
    if not cases:
        raise HTTPException(409, f"No {'hidden' if private else 'public'} test cases are set up yet")
    sha = hashlib.sha256(body.agentSource.encode()).hexdigest()
    # One start at a time per team: two SDKs started together cannot both open a run.
    await db.execute(select(Team.id).where(Team.id == team.id).with_for_update())
    earlier = (await db.execute(select(EvalRun).where(EvalRun.team_id == team.id, EvalRun.board == body.board)
                                .order_by(EvalRun.id))).scalars().all()
    action, run, why = competition.eval_start_decision(body.board, list(earlier), sha)
    if action == "refuse":
        raise HTTPException(409, why)
    if action == "resume" and await competition.broken_slots(db, run):
        # A test case was started and never finished (the game was closed mid-way):
        # it can't be played again, so this run can never be complete.
        if body.board == "private":
            await competition.close_run(db, run)
            await db.commit()
            leaderboards.changed()
            raise HTTPException(409, f"Your private run #{run.id} can't be completed: a hidden test case was "
                                     "started but not finished (the game was closed). Ask the organisers.")
        action = "new"  # public: start a new run (the old one is left abandoned below)
    if action == "resume":
        run.status = "running"  # an incomplete private run continues where it stopped
        await db.commit()
        played = {s["slot"]: s["score"] for s in await competition.eval_progress(db, run) if s["played"]}
        return {"evalId": run.id, "board": run.board, "slots": len(run.case_codes), "agentSha256": run.agent_sha256,
                "resumed": True, "played": played}
    # A new run: an unfinished one (public only) stays as it was, abandoned.
    for old in earlier:
        if old.status == "running":
            old.status = "abandoned"
    run = EvalRun(team_id=team.id, board=body.board, status="running", case_codes=[c.code for c in cases],
                  agent_source=body.agentSource, agent_sha256=sha, sdk_version=body.sdkVersion)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return {"evalId": run.id, "board": run.board, "slots": len(cases), "agentSha256": run.agent_sha256,
            "resumed": False, "played": {}}


async def _own_run(db: AsyncSession, eval_id: int, team_id: int) -> EvalRun:
    run = await db.get(EvalRun, eval_id)
    if run is None or run.team_id != team_id:
        raise HTTPException(404, "Unknown eval run")
    return run


@router.get("/api/eval/{eval_id}")
async def eval_status(eval_id: int, team_id: int = Depends(accounts.require_team),
                      db: AsyncSession = Depends(get_db)):
    run = await _own_run(db, eval_id, team_id)
    return {"evalId": run.id, "status": run.status, "score": run.score,
            "slots": await competition.eval_progress(db, run)}


@router.post("/api/eval/{eval_id}/finish")
async def eval_finish(eval_id: int, body: EvalFinishIn, team_id: int = Depends(accounts.require_team),
                      db: AsyncSession = Depends(get_db)):
    run = await _own_run(db, eval_id, team_id)
    # A run whose last test case finished was already scored then; its log
    # and summary are still taken once.
    if run.status == "running" or (run.status in leaderboards.COUNTED_EVALS and run.summary is None):
        if body.log:
            try:
                raw = base64.b64decode(body.log, validate=True)
                if len(raw) > MAX_LOG_BYTES or len(gzip.decompress(raw)) > 10 * MAX_LOG_BYTES:
                    raise HTTPException(413, "Run log too large")
            except (binascii.Error, OSError, EOFError):
                raise HTTPException(422, "log must be base64 of a gzipped file")
            folder = settings.UPLOAD_DIR / "eval_logs"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"eval{run.id}_team{team_id}.jsonl.gz"
            path.write_bytes(raw)
            run.log_path = str(path)
        if run.status == "running":
            await competition.close_run(db, run)
        run.summary = body.summary
        await db.commit()
        leaderboards.changed()
    return {"evalId": run.id, "board": run.board, "status": run.status, "score": run.score,
            "slots": await competition.eval_progress(db, run)}


# ── Pages ─────────────────────────────────────────────────────────────────────
@router.get("/register", response_class=HTMLResponse, include_in_schema=False)
async def register_page():
    return FileResponse(STATIC / "register.html")


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_page():
    return FileResponse(STATIC / "admin.html")


# ── Admin API ─────────────────────────────────────────────────────────────────
class TeamIn(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=60)
    password: str | None = Field(None, min_length=8, max_length=200)
    approved: bool | None = None
    banned: bool | None = None
    answers: dict[str, Any] | None = None
    notes: str | None = None


class CaseIn(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str | None = None
    hidden: bool | None = None
    active: bool | None = None
    position: int | None = None
    level: dict[str, Any] | None = None


# Only the columns the stats need: a team's games and runs pile up over the
# event, and their result/report JSON and agent sources would be read (and
# parsed on the event loop that also answers live games) for nothing.
STAT_GAME_COLS = (GameSession.team_id, GameSession.mode, GameSession.test_case, GameSession.score,
                  GameSession.started_at, GameSession.finished_at)
STAT_EVAL_COLS = (EvalRun.team_id, EvalRun.board, EvalRun.status, EvalRun.score, EvalRun.started_at)


async def _stats_by_team(db: AsyncSession, team_id: int | None = None) -> dict[int, dict]:
    """{team id: stats} for one team, or for every team that played (two queries in all)."""
    games_q = select(*STAT_GAME_COLS).order_by(GameSession.started_at)
    evals_q = select(*STAT_EVAL_COLS).order_by(EvalRun.id)
    if team_id is not None:
        games_q, evals_q = games_q.where(GameSession.team_id == team_id), evals_q.where(EvalRun.team_id == team_id)
    games: dict[int, list] = {}
    evals: dict[int, list] = {}
    for g in (await db.execute(games_q)).all():
        games.setdefault(g.team_id, []).append(g)
    for e in (await db.execute(evals_q)).all():
        evals.setdefault(e.team_id, []).append(e)
    return {t: _team_stats(games.get(t, []), evals.get(t, [])) for t in {*games, *evals}}


async def _one_team_stats(db: AsyncSession, team_id: int) -> dict:
    return (await _stats_by_team(db, team_id)).get(team_id) or _team_stats([], [])


def _team_stats(games: list, evals: list) -> dict:
    played = [g for g in games if g.mode == "play" and g.finished_at]
    best: dict[str, float] = {}
    for g in played:
        best[g.test_case] = max(best.get(g.test_case, 0.0), g.score or 0.0)
    counted = leaderboards.COUNTED_EVALS
    scored = [e.score for e in evals if e.board == "private" and e.score is not None and e.status in counted]
    public = [e.score for e in evals if e.board == "public" and e.score is not None and e.status in counted]
    last = max([g.started_at for g in games] + [e.started_at for e in evals], default=None)
    return {"gamesPlayed": len(played), "gamesStarted": len([g for g in games if g.mode == "play"]),
            "bestPlayScores": best, "evals": len(evals), "evalsCounted": len(scored) + len(public),
            "evalsRunning": len([e for e in evals if e.status == "running"]),
            "bestPublic": max(public) if public else None,
            "bestEval": max(scored) if scored else None, "lastEval": scored[-1] if scored else None,
            "lastActive": last.isoformat() if last else None}


def _team_row(team: Team) -> dict:
    profile = team.profile or {}
    return {"id": team.id, "teamID": accounts.team_code(team.id), "name": team.name,
            "approved": team.approved, "banned": team.banned, "hasPassword": bool(team.password_hash),
            "answers": profile.get("answers") or {}, "notes": profile.get("notes") or "",
            "createdAt": team.created_at.isoformat() if team.created_at else None}


@admin.get("/overview")
async def overview(db: AsyncSession = Depends(get_db)):
    count = lambda q: db.scalar(select(func.count()).select_from(q))  # noqa: E731
    return {
        "teams": await count(select(Team.id).subquery()),
        "pending": await count(select(Team.id).where(Team.approved.is_(False), Team.banned.is_(False)).subquery()),
        "banned": await count(select(Team.id).where(Team.banned.is_(True)).subquery()),
        "games": await count(select(GameSession.session_id).where(GameSession.finished_at.is_not(None)).subquery()),
        "evals": await count(select(EvalRun.id).subquery()),
        "evalsCounted": await count(select(EvalRun.id).where(
            EvalRun.status.in_(leaderboards.COUNTED_EVALS), EvalRun.score.is_not(None)).subquery()),
        "evalsRunning": await count(select(EvalRun.id).where(EvalRun.status == "running").subquery()),
        "publicCases": len(await competition.public_cases(db)),
        "hiddenCases": len(await competition.hidden_cases(db)),
    }


@admin.get("/teams")
async def admin_teams(db: AsyncSession = Depends(get_db)):
    teams = (await db.execute(select(Team).order_by(Team.id))).scalars().all()
    stats = await _stats_by_team(db)
    return [{**_team_row(t), **(stats.get(t.id) or _team_stats([], []))} for t in teams]


@admin.post("/teams", status_code=201)
async def admin_create_team(body: TeamIn, db: AsyncSession = Depends(get_db)):
    if not body.name:
        raise HTTPException(422, "name is required")
    if (await db.execute(select(Team.id).where(func.lower(Team.name) == body.name.lower()))).first():
        raise HTTPException(409, "That team name is taken")
    team = Team(name=body.name, approved=True if body.approved is None else body.approved,
                banned=bool(body.banned), profile={"answers": body.answers or {}, "notes": body.notes or ""},
                password_hash=await asyncio.to_thread(accounts.hash_password, body.password) if body.password else None)
    db.add(team)
    await db.commit()
    await db.refresh(team)
    return _team_row(team)


class ImportTeam(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    password: str | None = Field(None, min_length=8, max_length=200)
    answers: dict[str, Any] = {}


class ImportIn(BaseModel):
    teams: list[ImportTeam] = Field(..., max_length=3000)
    approved: bool = True


@admin.post("/teams/import")
async def admin_import_teams(body: ImportIn, db: AsyncSession = Depends(get_db)):
    """Create many teams at once (e.g. the registrations exported from Unstop).

    A team without a password gets a generated one. Names already taken (any
    case, or twice in the list) are skipped, so the same file can be imported
    again after adding rows. The reply is the only place the generated
    passwords appear: {created: [{teamID, name, password, answers}], skipped: [{name, reason}]}.
    """
    taken = {n.lower(): i for i, n in (await db.execute(select(Team.id, Team.name))).all()}
    fresh, skipped = [], []
    for row in body.teams:
        name = " ".join(row.name.split())
        if len(name) < 2:
            skipped.append({"name": row.name, "reason": "name too short"})
        elif name.lower() in taken:
            tid = taken[name.lower()]
            skipped.append({"name": name, "reason": "already exists" if tid else "twice in the list",
                            **({"teamID": accounts.team_code(tid)} if tid else {})})
        else:
            taken[name.lower()] = 0
            fresh.append((name, row.password or accounts.new_password(), row.answers))
    # Hashing is the slow part (deliberately): spread it over the CPU cores.
    loop = asyncio.get_running_loop()
    hashes = await asyncio.gather(*(loop.run_in_executor(None, accounts.hash_password, pw) for _, pw, _ in fresh))
    teams = [Team(name=name, approved=body.approved, banned=False, password_hash=h,
                  profile={"answers": answers, "notes": "", "source": "import"})
             for (name, _, answers), h in zip(fresh, hashes)]
    db.add_all(teams)
    await db.commit()
    created = [{"teamID": accounts.team_code(t.id), "name": t.name, "password": pw, "answers": answers}
               for t, (_, pw, answers) in zip(teams, fresh)]
    return {"created": created, "skipped": skipped}


@admin.get("/teams/{team_id}")
async def admin_team(team_id: int, db: AsyncSession = Depends(get_db)):
    team = await _team(db, team_id)
    games = (await db.execute(select(GameSession).where(GameSession.team_id == team_id)
                              .order_by(GameSession.started_at.desc()))).scalars().all()
    evals = (await db.execute(select(EvalRun).options(defer(EvalRun.agent_source)).where(EvalRun.team_id == team_id)
                              .order_by(EvalRun.id.desc()))).scalars().all()
    return {
        **_team_row(team), **await _one_team_stats(db, team_id),
        "games": [_game_row(g) for g in games],
        "evalRuns": [{**_eval_row(e), "slots": await competition.eval_progress(db, e)} for e in evals],
    }


@admin.patch("/teams/{team_id}")
async def admin_update_team(team_id: int, body: TeamIn, db: AsyncSession = Depends(get_db)):
    team = await _team(db, team_id)
    if body.name and body.name != team.name:
        if (await db.execute(select(Team.id).where(func.lower(Team.name) == body.name.lower(),
                                                   Team.id != team_id))).first():
            raise HTTPException(409, "That team name is taken")
        team.name = body.name
    if body.password:
        team.password_hash = await asyncio.to_thread(accounts.hash_password, body.password)
    for field in ("approved", "banned"):
        if getattr(body, field) is not None:
            setattr(team, field, getattr(body, field))
    if body.answers is not None or body.notes is not None:
        profile = dict(team.profile or {})
        if body.answers is not None:
            profile["answers"] = body.answers
        if body.notes is not None:
            profile["notes"] = body.notes
        team.profile = profile
    await db.commit()
    leaderboards.changed(force=True)  # a ban or approval changes who is ranked
    return _team_row(team)


@admin.delete("/teams/{team_id}", status_code=204)
async def admin_delete_team(team_id: int, db: AsyncSession = Depends(get_db)):
    """Remove a team and everything it played (spam or test registrations)."""
    await _team(db, team_id)
    # Games, eval runs, submissions and level data go with it (ON DELETE CASCADE).
    await db.execute(sql_delete(Team).where(Team.id == team_id))
    await db.commit()
    leaderboards.changed(force=True)


def _game_row(g: GameSession) -> dict:
    stats = (g.evaluator_output or {}).get("stats") or {}
    return {"sessionID": g.session_id, "testCase": g.test_case, "mode": g.mode, "evalId": g.eval_id,
            "evalSlot": g.eval_slot, "score": g.score, "won": g.success, "pauses": g.pauses,
            "placements": len(g.placements or []), "agentSha256": g.agent_sha256,
            "killed": stats.get("enemies_killed"), "enemies": stats.get("total_enemies"),
            "duration": (g.result or {}).get("durationSeconds"),
            "startedAt": g.started_at.isoformat() if g.started_at else None,
            "finishedAt": g.finished_at.isoformat() if g.finished_at else None}


def _eval_row(e: EvalRun) -> dict:
    return {"evalId": e.id, "teamID": accounts.team_code(e.team_id), "board": e.board, "status": e.status, "score": e.score,
            "cases": e.case_codes, "agentSha256": e.agent_sha256, "sdkVersion": e.sdk_version,
            "hasLog": bool(e.log_path), "summary": e.summary,
            "startedAt": e.started_at.isoformat() if e.started_at else None,
            "finishedAt": e.finished_at.isoformat() if e.finished_at else None}


@admin.get("/sessions/{session_id}")
async def admin_session(session_id: str, db: AsyncSession = Depends(get_db)):
    game = await db.get(GameSession, session_id)
    if game is None:
        raise HTTPException(404, "Unknown session")
    return {**_game_row(game), "result": game.result, "evaluatorOutput": game.evaluator_output,
            "placementList": game.placements}


@admin.get("/evals")
async def admin_evals(db: AsyncSession = Depends(get_db)):
    runs = (await db.execute(select(EvalRun).options(defer(EvalRun.agent_source))
                             .order_by(EvalRun.id.desc()).limit(500))).scalars().all()
    return [_eval_row(e) for e in runs]


@admin.get("/evals/{eval_id}")
async def admin_eval(eval_id: int, db: AsyncSession = Depends(get_db)):
    run = await db.get(EvalRun, eval_id)
    if run is None:
        raise HTTPException(404, "Unknown eval run")
    games = (await db.execute(select(GameSession).where(GameSession.eval_id == eval_id)
                              .order_by(GameSession.started_at))).scalars().all()
    return {**_eval_row(run), "agentSource": run.agent_source,
            "slots": await competition.eval_progress(db, run), "games": [_game_row(g) for g in games]}


@admin.post("/evals/{eval_id}/void")
async def admin_void_eval(eval_id: int, db: AsyncSession = Depends(get_db)):
    """Void a run: it leaves the leaderboard and no longer locks the team to its
    agent.py, so the team can publish again (after a genuine crash, say)."""
    run = await db.get(EvalRun, eval_id)
    if run is None:
        raise HTTPException(404, "Unknown eval run")
    run.status = "void"
    run.finished_at = run.finished_at or competition.now()
    await db.commit()
    leaderboards.changed(force=True)
    return _eval_row(run)


@admin.post("/evals/{eval_id}/close")
async def admin_close_eval(eval_id: int, db: AsyncSession = Depends(get_db)):
    """Score a run that is still running as it stands (a case not played
    counts 0), e.g. a team that stopped mid-run when the event ends."""
    run = await db.get(EvalRun, eval_id)
    if run is None:
        raise HTTPException(404, "Unknown eval run")
    if run.status != "running":
        raise HTTPException(409, f"Run #{run.id} is {run.status}, not running")
    await competition.close_run(db, run)
    await db.commit()
    leaderboards.changed(force=True)
    return _eval_row(run)


class CloseIn(BaseModel):
    board: Literal["public", "private"]


@admin.post("/evals/close-running")
async def admin_close_running(body: CloseIn, db: AsyncSession = Depends(get_db)):
    """Close every running run of a board (the end of the event)."""
    runs = (await db.execute(select(EvalRun).where(EvalRun.board == body.board, EvalRun.status == "running")
                             .with_for_update())).scalars().all()
    for run in runs:
        await competition.close_run(db, run)
    await db.commit()
    leaderboards.changed(force=True)
    return {"closed": [_eval_row(r) for r in runs]}


@admin.get("/evals/{eval_id}/log")
async def admin_eval_log(eval_id: int, db: AsyncSession = Depends(get_db)):
    run = await db.get(EvalRun, eval_id)
    if run is None or not run.log_path or not Path(run.log_path).exists():
        raise HTTPException(404, "No log was uploaded for this eval run")
    return FileResponse(run.log_path, media_type="application/gzip", filename=Path(run.log_path).name)


def _case_row(c: TestCase) -> dict:
    return {"code": c.code, "name": c.name, "hidden": c.hidden, "active": c.active, "position": c.position,
            "level": c.level, "enemies": sum(int(e.get("count", 0)) for e in c.level.get("enemies") or []),
            "updatedAt": c.updated_at.isoformat() if c.updated_at else None}


@admin.get("/test-cases")
async def admin_cases(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(TestCase).order_by(TestCase.hidden, TestCase.position, TestCase.code))).scalars()
    return [_case_row(c) for c in rows]


@admin.post("/test-cases", status_code=201)
async def admin_add_case(body: CaseIn, db: AsyncSession = Depends(get_db)):
    if not body.code or body.level is None:
        raise HTTPException(422, "code and level are required")
    if await competition.case_by_code(db, body.code):
        raise HTTPException(409, f"Test case {body.code.upper()} exists")
    case = TestCase(code=body.code.upper(), name=body.name or body.code.upper(), hidden=bool(body.hidden),
                    active=True if body.active is None else body.active, position=body.position or 0,
                    level=competition.normalise_level(body.level))
    db.add(case)
    await db.commit()
    await db.refresh(case)
    leaderboards.changed(force=True)  # the boards list the test cases
    return _case_row(case)


@admin.put("/test-cases/{code}")
async def admin_update_case(code: str, body: CaseIn, db: AsyncSession = Depends(get_db)):
    case = await competition.case_by_code(db, code)
    if case is None:
        raise HTTPException(404, "Unknown test case")
    if body.code and body.code.upper() != case.code:
        if await competition.case_by_code(db, body.code):
            raise HTTPException(409, f"Test case {body.code.upper()} exists")
        case.code = body.code.upper()
    for field in ("name", "hidden", "active", "position"):
        if getattr(body, field) is not None:
            setattr(case, field, getattr(body, field))
    if body.level is not None:
        case.level = competition.normalise_level(body.level)
    await db.commit()
    await db.refresh(case)
    leaderboards.changed(force=True)
    return _case_row(case)


@admin.delete("/test-cases/{code}", status_code=204)
async def admin_delete_case(code: str, db: AsyncSession = Depends(get_db)):
    case = await competition.case_by_code(db, code)
    if case is None:
        raise HTTPException(404, "Unknown test case")
    await db.delete(case)
    await db.commit()
    leaderboards.changed(force=True)


@admin.get("/registration-form")
async def admin_form(db: AsyncSession = Depends(get_db)):
    return await competition.get_setting(db, "registration_form", competition.DEFAULT_FORM)


@admin.put("/registration-form")
async def admin_put_form(body: dict[str, Any], db: AsyncSession = Depends(get_db)):
    form = competition.validate_form(body)
    await competition.put_setting(db, "registration_form", form)
    return form
