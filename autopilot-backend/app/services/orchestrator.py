"""
app/services/orchestrator.py
─────────────────────────────
Run orchestration. One submission fans out into one Run per enemy config:

    run_submission(submission)                      (background task)
        └─ for each Run (bounded by MAX_CONCURRENT_RUNS):
             SDK (validate + execute agent.py with that config's enemies)
               ├─ invalid  → run `invalid`, violations stored
               ├─ failed   → run `failed` (infrastructure error)
               └─ valid    → run `simulating`, POST simulator /start
                                 ↓ (simulator later calls POST /runs/{id}/result)
        after each run reaches a terminal state, scoring.finalize_submission()
        rolls the runs up into the submission's total_score.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import EnemyConfig, Run, RunStatus, Submission
from app.services import sdk_service, simulator_service
from app.services.scoring import finalize_submission
from app.ws_manager import manager

logger = logging.getLogger(__name__)

# Throttles SDK calls + simulator starts across ALL submissions.
_slots = asyncio.Semaphore(settings.MAX_CONCURRENT_RUNS)


# ── Placement rules ──────────────────────────────────────────────────────────
def _validate_placement(placement_list: list[dict]) -> list[dict]:
    """
    Backend-side game-rule checks (per-field ranges are already enforced by the
    Pydantic schema). Returns violation dicts; empty means clean.

    Weapon/ammo budgets are NOT checked here: the limits are not defined yet
    and defence ids are a different namespace from enemy ids. Add the check
    once the budget rules are final (or leave it to the SDK / simulator).
    """
    violations: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for p in placement_list:
        pos = p["defenceGridPosition"]
        key = (pos["x"], pos["y"])
        if key in seen:
            violations.append(
                {"type": "duplicate_placement", "message": f"Defence placed twice at {key}"}
            )
        seen.add(key)
    return violations


# ── Terminal-state helpers ───────────────────────────────────────────────────
async def _set_invalid(
    db: AsyncSession, run: Run, violations: list[dict], placement: list[dict] | None = None
) -> None:
    logger.warning("Run %d marked INVALID: %s", run.id, violations)
    run.sdk_status = {"valid": False, "violations": violations}
    if placement is not None:
        run.defence_placement = placement
    run.status = RunStatus.invalid
    await db.commit()
    await manager.send_status(run.id, RunStatus.invalid.value)


async def _set_failed(db: AsyncSession, run: Run, message: str) -> None:
    logger.error("Run %d marked FAILED: %s", run.id, message)
    run.status = RunStatus.failed
    await db.commit()
    await manager.send_error(run.id, message)


# ── One run ──────────────────────────────────────────────────────────────────
async def run_pipeline(
    run_id: int,
    enemies: list[dict],
    agent_file_content: bytes,
    db: AsyncSession,
) -> None:
    run = await db.get(Run, run_id)
    if run is None:
        logger.warning("Run %d not found in DB during pipeline execution", run_id)
        return

    logger.info("Starting pipeline for Run %d (enemy config with %d enemy types)...", run_id, len(enemies))

    # 1. SDK ──────────────────────────────────────────────────────────────────
    try:
        sdk_response = await sdk_service.run_agent(agent_file_content, enemies)
    except ValidationError as exc:
        logger.warning("Run %d agent returned schema validation error: %s", run_id, exc)
        await _set_invalid(db, run, [{"type": "schema", "message": str(exc)[:1000]}])
        return
    except Exception as exc:
        logger.exception("Run %d SDK call failed: %s", run_id, exc)
        run.sdk_status = {"error": str(exc)}
        await _set_failed(db, run, f"SDK call failed: {exc}")
        return

    # 2. Interpret the SDK result ─────────────────────────────────────────────
    violations = [v.model_dump() for v in sdk_response.sdkStatus.violations]
    placement = (
        [p.model_dump() for p in sdk_response.defencePlacementData]
        if sdk_response.defencePlacementData
        else None
    )

    logger.info(
        "Run %d SDK completed: valid=%s, %d placements, %d violations",
        run_id,
        sdk_response.sdkStatus.valid,
        len(placement) if placement else 0,
        len(violations),
    )

    if not sdk_response.sdkStatus.valid:
        await _set_invalid(db, run, violations)
        return

    if not placement:
        violations.append({"type": "missing_placement", "message": "No defence placement data"})
        await _set_invalid(db, run, violations)
        return

    rule_violations = _validate_placement(placement)
    if rule_violations:
        await _set_invalid(db, run, rule_violations, placement)
        return

    # 3. Valid → hand to the simulator (fire-and-forget; it calls back later) ─
    run.sdk_status = {"valid": True, "violations": []}
    run.defence_placement = placement

    if not settings.ENABLE_HEADLESS_SIMULATOR:
        logger.info(
            "Headless simulator is paused (ENABLE_HEADLESS_SIMULATOR=False). Run %d queued for on-demand interactive testing in Unity.",
            run.id,
        )
        run.status = RunStatus.pending
        await db.commit()
        await manager.send_status(run.id, RunStatus.pending.value)
        return

    run.status = RunStatus.simulating
    await db.commit()
    await manager.send_status(run.id, RunStatus.simulating.value)

    team_id_str: str | None = None
    sub = await db.get(Submission, run.submission_id)
    if sub and sub.team_id:
        team_id_str = str(sub.team_id)

    try:
        logger.info("Dispatching Run %d to simulator (team=%s)...", run.id, team_id_str)
        await simulator_service.start_simulation(run.id, enemies, placement, team_id=team_id_str)
    except Exception as exc:
        logger.exception("Run %d failed to start simulation: %s", run.id, exc)
        await _set_failed(db, run, f"Simulator start failed: {exc}")


async def _run_one(
    run_id: int, submission_id: int, enemies: list[dict], content: bytes
) -> None:
    async with _slots:
        async with AsyncSessionLocal() as db:
            try:
                await run_pipeline(run_id, enemies, content, db)
            except Exception as exc:  # never let one run take down the others
                logger.exception("run %s crashed", run_id)
                await db.rollback()
                run = await db.get(Run, run_id)
                if run is not None:
                    await _set_failed(db, run, f"Unexpected error: {exc}")
            await finalize_submission(db, submission_id)


# ── One submission ───────────────────────────────────────────────────────────
async def run_submission(
    submission_id: int, agent_file_path: str, run_ids: list[int] | None = None
) -> None:
    """
    Background task: run agent.py against every enemy config of the submission
    (or only `run_ids`, when retrying failed runs).
    """
    async with AsyncSessionLocal() as db:
        stmt = (
            select(Run.id, EnemyConfig.enemies)
            .join(EnemyConfig, Run.enemy_config_id == EnemyConfig.id)
            .where(Run.submission_id == submission_id)
            .order_by(Run.id)
        )
        if run_ids is not None:
            stmt = stmt.where(Run.id.in_(run_ids))
        targets = [(rid, enemies) for rid, enemies in (await db.execute(stmt)).all()]

        logger.info(
            "Orchestrating submission %d: starting %d run pipeline tasks...",
            submission_id,
            len(targets),
        )

        try:
            content = Path(agent_file_path).read_bytes()
        except OSError as exc:
            logger.error("agent file unreadable for submission %s: %s", submission_id, exc)
            for rid, _ in targets:
                run = await db.get(Run, rid)
                if run is not None:
                    run.sdk_status = {"error": f"agent file unreadable: {exc}"}
                    run.status = RunStatus.failed
            await db.commit()
            await finalize_submission(db, submission_id)
            return

    await asyncio.gather(
        *(_run_one(rid, submission_id, enemies, content) for rid, enemies in targets)
    )
