"""
app/routers/leaderboard.py
───────────────────────────
GET /leaderboard  –  one row per team, ranked by submission score DESC

A submission's score is the average of its per-config run scores (see
services/scoring.py). Each team is represented by its BEST completed submission.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Submission, SubmissionStatus, Team
from app.schemas import LeaderboardEntry

router = APIRouter(prefix="/leaderboard", tags=["Leaderboard"])


@router.get("", response_model=list[LeaderboardEntry])
async def leaderboard(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """
    Top `limit` teams by their best completed submission.

    Submissions that are still running or that failed (awaiting retry) have no
    total score and are excluded. Ties break on the earlier submission.
    """
    # Inner: every scored submission, numbered per team (best first) so we can keep one.
    best_per_team = (
        select(
            Submission.id.label("submission_id"),
            Submission.total_score,
            Submission.created_at,
            Submission.team_id,
            Team.name.label("team_name"),
            func.row_number().over(
                partition_by=Submission.team_id,
                order_by=(Submission.total_score.desc(), Submission.created_at.asc()),
            ).label("rn"),
        )
        .join(Team, Submission.team_id == Team.id)
        .where(Submission.status.in_([SubmissionStatus.completed, SubmissionStatus.pending]))
        .where(Submission.total_score.is_not(None))
        .subquery("best_per_team")
    )

    # Outer: keep each team's best submission, assign a dense rank, cap to limit.
    stmt = (
        select(
            best_per_team.c.submission_id,
            best_per_team.c.total_score,
            best_per_team.c.created_at,
            best_per_team.c.team_id,
            best_per_team.c.team_name,
            func.dense_rank().over(
                order_by=(
                    best_per_team.c.total_score.desc(),
                    best_per_team.c.created_at.asc(),
                )
            ).label("rank"),
        )
        .where(best_per_team.c.rn == 1)
        .order_by(best_per_team.c.total_score.desc(), best_per_team.c.created_at.asc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()

    return [
        LeaderboardEntry(
            rank=int(row.rank),
            team_id=row.team_id,
            team_name=row.team_name,
            submission_id=row.submission_id,
            score=float(row.total_score),
            created_at=row.created_at,
        )
        for row in rows
    ]
