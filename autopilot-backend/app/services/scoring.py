"""
app/services/scoring.py
───────────────────────
Rolls the per-config run results up into one submission score.

A submission is run against every enemy config. Once every run has reached a
terminal state:

  * any run `failed` (infrastructure error: SDK/simulator/evaluator down)
        -> submission `failed`, no score. An organiser retries it, so a
           service blip never silently costs a team points.
  * otherwise -> submission `completed`,
        total_score = mean of run scores over ALL runs
        (`completed` -> evaluator score, `invalid` -> 0).
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Run, RunStatus, Submission, SubmissionStatus

logger = logging.getLogger(__name__)

TERMINAL = {RunStatus.completed, RunStatus.invalid, RunStatus.failed}


async def finalize_submission(db: AsyncSession, submission_id: int) -> None:
    """Idempotent: safe to call after every run; updates progressive total_score."""
    rows = (
        await db.execute(
            select(Run.status, Run.score).where(Run.submission_id == submission_id)
        )
    ).all()
    if not rows:
        return

    submission = await db.get(Submission, submission_id)
    if submission is None:
        return

    # Count all test cases attempted so far
    attempted_runs = [r for r in rows if r.status in {RunStatus.completed, RunStatus.invalid}]
    if attempted_runs:
        total = sum(
            float(r.score) if r.status == RunStatus.completed and r.score is not None else 0.0
            for r in attempted_runs
        )
        submission.total_score = round(total / len(attempted_runs), 4)

    if all(r.status in TERMINAL for r in rows):
        if any(r.status == RunStatus.failed for r in rows):
            submission.status = SubmissionStatus.failed
            submission.total_score = None
        else:
            submission.status = SubmissionStatus.completed
    elif attempted_runs:
        submission.status = SubmissionStatus.pending

    logger.info(
        "Submission %d scoring update: status=%s, total_score=%s (%d/%d runs terminal)",
        submission_id,
        submission.status.value,
        submission.total_score,
        len(attempted_runs),
        len(rows),
    )

    await db.commit()
