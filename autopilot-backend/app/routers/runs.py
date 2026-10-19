"""
app/routers/runs.py
────────────────────
GET  /runs/{id}          – fetch a run's full state
POST /runs/{id}/result   – simulator webhook: posts game result → evaluator → DB
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import EnemyConfig, Run, RunStatus
from app.schemas import RunOut, SimulatorResult
from app.security import verify_simulator
from app.services import evaluator_service
from app.services.scoring import finalize_submission
from app.ws_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["Runs"])


# ── GET /runs/{id} ────────────────────────────────────────────────────────────
@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)):
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run


# ── POST /runs/{id}/result ────────────────────────────────────────────────────
@router.post("/{run_id}/result", response_model=RunOut, dependencies=[Depends(verify_simulator)])
async def simulator_result(
    run_id: int,
    body: SimulatorResult,
    db: AsyncSession = Depends(get_db),
):
    """
    Called by the simulator when a round ends.

    Steps
    -----
    1. Validate run exists and is in 'simulating' state
    2. Determine test-case budget from enemy_config
    3. Call evaluator with simulator result and budget
    4. Persist evaluator_output + score
    5. Mark run 'completed' (or 'failed' on error / missing score)
    6. Roll the run up into the submission's total score (once all runs are done)
    7. Broadcast final result over WebSocket
    """
    run = await db.get(Run, run_id)
    if run is None:
        logger.warning("Run %d not found on result callback", run_id)
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    if run.status != RunStatus.simulating:
        logger.warning("Run %d status mismatch: '%s' != 'simulating'", run_id, run.status)
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} is in status '{run.status}', expected 'simulating'",
        )

    # ── Test-Case Budget Determination ────────────────────────────────────────
    test_case_budget: float | None = None
    if run.enemy_config_id:
        enemy_config = await db.get(EnemyConfig, run.enemy_config_id)
        if enemy_config:
            if enemy_config.budget is not None and enemy_config.budget > 0:
                test_case_budget = float(enemy_config.budget)
            elif enemy_config.enemies:
                total_enemies = sum(
                    int(e.get("count", 0))
                    for e in enemy_config.enemies
                    if isinstance(e, dict)
                )
                test_case_budget = float(max(250.0, min(850.0, 150.0 + total_enemies * 30.0)))

    logger.info("Evaluating simulator result for Run %d (test_case_budget=%s)...", run_id, test_case_budget)

    # ── Call evaluator ────────────────────────────────────────────────────────
    try:
        evaluator_output, score = await evaluator_service.evaluate(
            body.result,
            expected_defences=run.defence_placement,
            test_case_budget=test_case_budget,
        )
    except Exception as exc:
        logger.exception("Run %d evaluator call failed: %s", run_id, exc)
        run.status = RunStatus.failed
        await db.commit()
        await finalize_submission(db, run.submission_id)
        await manager.send_error(run_id, f"Evaluator call failed: {exc}")
        raise HTTPException(status_code=502, detail=f"Evaluator error: {exc}") from exc

    if score is None or not evaluator_output.get("valid", True):
        logger.warning("Run %d evaluated as INVALID: %s", run_id, evaluator_output.get("error"))
        run.evaluator_output = evaluator_output
        run.score = 0.0
        run.status = RunStatus.invalid
        await db.commit()
        await finalize_submission(db, run.submission_id)
        await manager.send_result(run_id, 0.0, evaluator_output)
        return run

    # ── Persist ───────────────────────────────────────────────────────────────
    logger.info("Run %d COMPLETED: score=%.2f, breakdown=%s", run_id, score, evaluator_output.get("breakdown"))
    run.evaluator_output = evaluator_output
    run.score = score
    run.status = RunStatus.completed
    await db.commit()
    await finalize_submission(db, run.submission_id)
    await db.refresh(run)

    # ── Broadcast ─────────────────────────────────────────────────────────────
    await manager.send_result(run_id, score, evaluator_output)

    return run
