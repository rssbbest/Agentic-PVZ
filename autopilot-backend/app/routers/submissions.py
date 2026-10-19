"""
app/routers/submissions.py
──────────────────────────
POST /submissions            Upload agent.py (multipart). The backend creates ONE
                             Run per enemy config and runs the agent against all
                             of them in the background.
GET  /submissions/{id}       Submission status, total score and per-config runs.
POST /submissions/{id}/retry (admin) Re-run the runs that failed on an
                             infrastructure error (SDK / simulator / evaluator down).

POST /teams                  Register a team before submitting.

Participants only send `team_id` + `agent_file`. They never choose enemies:
the enemy configs live in the backend and every submission faces all of them.
"""
from __future__ import annotations

import logging
import uuid

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models import EnemyConfig, Run, RunStatus, Submission, SubmissionStatus, Team
from app.schemas import SubmissionOut, TeamCreate, TeamOut
from app.security import verify_admin
from app.services.orchestrator import run_submission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/submissions", tags=["Submissions"])
teams_router = APIRouter(prefix="/teams", tags=["Teams"])


async def _load_submission(db: AsyncSession, submission_id: int) -> Submission | None:
    """Fetch a submission with its runs eagerly loaded (async sessions can't lazy-load)."""
    stmt = (
        select(Submission)
        .options(selectinload(Submission.runs))
        .where(Submission.id == submission_id)
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


# ── POST /teams ───────────────────────────────────────────────────────────────
@teams_router.post("", response_model=TeamOut, status_code=201)
async def create_team(body: TeamCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Team).where(Team.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Team name already exists")
    team = Team(name=body.name)
    db.add(team)
    await db.flush()
    await db.refresh(team)

    # Auto-provision baseline TeamData so Unity can immediately load matches
    from app.services.seed_data import build_default_team_data
    td = build_default_team_data(team.id)
    db.add(td)
    await db.flush()
    logger.info("Created team: id=%d, name='%s'", team.id, team.name)
    return team


# ── POST /submissions ─────────────────────────────────────────────────────────
@router.post("", response_model=SubmissionOut, status_code=201)
async def submit_agent(
    background_tasks: BackgroundTasks,
    agent_file: UploadFile,
    team_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload the participant's agent.py.

    Form fields
    -----------
    agent_file : the Python file
    team_id    : existing team id
    """
    team = await db.get(Team, team_id)
    if team is None:
        logger.warning("Submission rejected: team_id=%s not found", team_id)
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")

    # The backend decides what the agent is tested against: every enemy config.
    config_ids = (
        await db.execute(select(EnemyConfig.id).order_by(EnemyConfig.id))
    ).scalars().all()
    if not config_ids:
        logger.error("Submission rejected: No enemy configs available in DB")
        raise HTTPException(status_code=503, detail="No enemy configs available")

    # ── Save uploaded file ────────────────────────────────────────────────────
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = settings.UPLOAD_DIR / f"team{team_id}_{uuid.uuid4().hex}.py"
    async with aiofiles.open(file_path, "wb") as f:
        while chunk := await agent_file.read(1024 * 64):
            await f.write(chunk)

    # ── One Submission, one pending Run per enemy config ──────────────────────
    submission = Submission(
        team_id=team_id, file_path=str(file_path), status=SubmissionStatus.pending
    )
    db.add(submission)
    await db.flush()
    db.add_all(
        Run(submission_id=submission.id, enemy_config_id=cid, status=RunStatus.pending)
        for cid in config_ids
    )
    await db.flush()

    logger.info(
        "Submission created: id=%d, team_id=%d (%s), %d runs queued, file=%s",
        submission.id,
        team_id,
        team.name,
        len(config_ids),
        file_path.name,
    )

    # Runs after the response is sent, i.e. after get_db has committed the rows above.
    background_tasks.add_task(run_submission, submission.id, str(file_path))

    return await _load_submission(db, submission.id)

    return await _load_submission(db, submission.id)


# ── GET /submissions/{id} ─────────────────────────────────────────────────────
@router.get("/{submission_id}", response_model=SubmissionOut)
async def get_submission(submission_id: int, db: AsyncSession = Depends(get_db)):
    submission = await _load_submission(db, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail=f"Submission {submission_id} not found")
    return submission


# ── POST /submissions/{id}/retry ──────────────────────────────────────────────
@router.post(
    "/{submission_id}/retry",
    response_model=SubmissionOut,
    dependencies=[Depends(verify_admin)],
)
async def retry_failed_runs(
    submission_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Re-run only the runs that failed on an infrastructure error."""
    submission = await db.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail=f"Submission {submission_id} not found")

    failed = (
        await db.execute(
            select(Run).where(Run.submission_id == submission_id, Run.status == RunStatus.failed)
        )
    ).scalars().all()
    if not failed:
        raise HTTPException(status_code=409, detail="No failed runs to retry")

    for run in failed:
        run.status = RunStatus.pending
        run.sdk_status = None
        run.defence_placement = None
        run.evaluator_output = None
        run.score = None
    submission.status = SubmissionStatus.pending
    submission.total_score = None
    await db.flush()

    background_tasks.add_task(
        run_submission, submission_id, submission.file_path, [r.id for r in failed]
    )
    return await _load_submission(db, submission_id)
