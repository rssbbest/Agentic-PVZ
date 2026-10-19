"""
app/services/reinforcement_service.py
──────────────────────────────────────
Mid-run reinforcement evaluation and validation engine.

Unity simulator freezes time (timeScale = 0) and calls POST /api/reinforcements.
This service verifies reinforcement placements against strict referee rules:
1. Strict Grid Boundaries (0 <= x <= 10, 0 <= y <= 5).
2. Valid Defence Prefab IDs (0 <= defenceId <= 3).
3. Live Cell Occupancy check: NEVER place on an occupied cell.
4. NO NUDGING: Invalid placements are strictly skipped/discarded.
5. Hard Budget Ceiling: Placements that exceed the match budget are strictly rejected.
6. NO HEURISTIC CHEATS: Backend does not generate arbitrary placements.
   The agent/LLM is solely responsible for determining placements.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import logging
from typing import Any

from app.schemas import (
    BudgetStatus,
    DefencePlacement,
    GridPosition,
    ReinforcementRequest,
    ReinforcementResponse,
)
from app.services.evaluator_service import (
    BOUNTY_PER_KILL,
    calculate_test_case_budget,
    calculate_unlocked_budget,
    get_unit_cost,
    wave_size,
)

logger = logging.getLogger(__name__)

# Board grid limits (11 columns, 6 rows)
GRID_MIN_X = 0
GRID_MAX_X = 10
GRID_MIN_Y = 0
GRID_MAX_Y = 5

VALID_DEFENCE_IDS = {0, 1, 2, 3}

# In-memory cumulative reinforcement and initial placement ledger keyed by sessionID
_SESSION_INITIAL_COST: dict[str, float] = {}
_SESSION_INITIAL_DEFENCES: dict[str, list[dict[str, Any]]] = {}
_SESSION_REINFORCEMENTS: dict[str, list[dict[str, Any]]] = {}
_SESSION_STARTING_COINS: dict[str, float | None] = {}  # the level's `coins`; None = default


def record_session_initial_defences(session_id: str, defences: list[dict[str, Any]] | list[Any]) -> None:
    """Record Turn 0 defence placement cost for a session to prevent mid-run overspending."""
    sid = str(session_id or "").strip()
    if sid:
        cost = sum(get_unit_cost(d) for d in defences)
        _SESSION_INITIAL_COST[sid] = float(cost)
        _SESSION_INITIAL_DEFENCES[sid] = [
            d.model_dump() if hasattr(d, "model_dump") else dict(d) for d in defences
        ]
        logger.info("[INCOME CHECK] Registered initial Turn 0 cost for session %s: %.1f currency (%d units)", sid, cost, len(defences))


def record_session_starting_coins(session_id: str, coins: float | None) -> None:
    sid = str(session_id or "").strip()
    if sid:
        _SESSION_STARTING_COINS[sid] = float(coins) if coins else None


async def _level_coins(db, session_id: str) -> float | None:
    """The session's level `coins` from the database; None when unknown."""
    if not (session_id.startswith("c") and len(session_id) == 16):  # sessions start_session makes
        return None
    try:
        from app.services.competition import session_coins
        return await session_coins(db, session_id)
    except Exception as exc:  # the default budget is a safe answer
        logger.warning("Starting coins of session %s unknown: %s", session_id, exc)
        return None


def session_placements(session_id: str) -> list[dict[str, Any]]:
    """Every placement approved for a session: Turn 0 defences, then reinforcements."""
    sid = str(session_id or "").strip()
    return list(_SESSION_INITIAL_DEFENCES.get(sid, [])) + list(_SESSION_REINFORCEMENTS.get(sid, []))


def db_id(text: str) -> int | None:
    """`text` as a database id (a run's sessionID, a team number), or None.
    Ids are int32 in Postgres: a longer number would fail the whole query."""
    text = (text or "").strip()
    return int(text) if text.isdigit() and 0 < int(text) < 2**31 else None


def clear_session_ledger(session_id: str) -> None:
    """Clean up session ledger after match completes."""
    sid = str(session_id or "").strip()
    _SESSION_INITIAL_COST.pop(sid, None)
    _SESSION_INITIAL_DEFENCES.pop(sid, None)
    _SESSION_REINFORCEMENTS.pop(sid, None)
    _SESSION_STARTING_COINS.pop(sid, None)


def _budget_status(unlocked: float, spent: float, total: float) -> BudgetStatus:
    return BudgetStatus(unlocked=round(unlocked, 1), spent=round(spent, 1),
                        remaining=round(max(0.0, unlocked - spent), 1), total=round(total, 1))


def _reject(rejected: list | None, item: Any, reason: str) -> None:
    if rejected is None:
        return
    data = item.model_dump() if hasattr(item, "model_dump") else (item if isinstance(item, dict) else {})
    rejected.append({
        "defenceId": data.get("defenceId"),
        "defenceGridPosition": data.get("defenceGridPosition"),
        "reason": reason,
    })


def validate_reinforcements(
    candidates: list[DefencePlacement] | list[dict[str, Any]],
    snapshot: ReinforcementRequest,
    rejected: list | None = None,
) -> list[DefencePlacement]:
    """
    Validates candidate placements strictly according to referee rules:
    - Verifies grid boundaries (0 <= x <= 10, 0 <= y <= 5).
    - Verifies defenceId in {0, 1, 2, 3}.
    - Verifies cell is not already occupied by a living defence on the board.
    - Verifies cell is not placed twice in the same batch.
    - Verifies placement cost does not breach the remaining budget (Hard Budget Ceiling).
    - NO NUDGING: If any check fails, the placement is discarded/skipped.
    """
    # 1. Map all cells currently occupied by living friendly defences
    occupied: set[tuple[int, int]] = {
        (d.position.x, d.position.y)
        for d in snapshot.defences
        if d.health > 0
    }

    # Derive budget ceiling and current spent
    if snapshot.unlockedBudget is not None and snapshot.unlockedBudget > 0:
        budget = float(snapshot.unlockedBudget)
    elif snapshot.budget is not None and snapshot.budget > 0:
        budget = float(snapshot.budget)
    else:
        budget = calculate_test_case_budget(None, snapshot.counts.totalEnemiesInWave)

    match_budget = float(snapshot.matchBudget or snapshot.budget or budget)

    if snapshot.spent is not None:
        current_spent = float(snapshot.spent)
    else:
        current_spent = float(sum(get_unit_cost(d) for d in snapshot.defences))

    valid_placements: list[DefencePlacement] = []
    seen_in_batch: set[tuple[int, int]] = set()

    for item in candidates:
        if isinstance(item, dict):
            try:
                candidate = DefencePlacement.model_validate(item)
            except Exception as e:
                logger.warning("Skipping placement: invalid schema structure (%s)", e)
                _reject(rejected, item, "not a valid placement (needs defenceId and defenceGridPosition x/y)")
                continue
        else:
            candidate = item

        pos = candidate.defenceGridPosition
        grid_key = (pos.x, pos.y)

        # Rule 1: Grid boundary validation
        if not (GRID_MIN_X <= pos.x <= GRID_MAX_X and GRID_MIN_Y <= pos.y <= GRID_MAX_Y):
            logger.warning(
                "Skipping placement: position (%d, %d) is out of grid boundaries [0..10, 0..5]",
                pos.x,
                pos.y,
            )
            _reject(rejected, candidate, f"off the board: ({pos.x},{pos.y}) is outside x 0-10, y 0-5")
            continue

        # Rule 2: Valid defence prefab ID
        if candidate.defenceId not in VALID_DEFENCE_IDS:
            logger.warning(
                "Skipping placement: invalid defenceId %d (must be 0, 1, 2, or 3)",
                candidate.defenceId,
            )
            _reject(rejected, candidate, f"unknown defenceId {candidate.defenceId} (use 0-3)")
            continue

        # Rule 3: Living unit cell occupancy check (NO NUDGING)
        if grid_key in occupied:
            logger.warning(
                "Skipping placement: cell (%d, %d) is already occupied by a living defence (NO NUDGING)",
                pos.x,
                pos.y,
            )
            _reject(rejected, candidate, f"cell ({pos.x},{pos.y}) already has a defence")
            continue

        # Rule 4: Duplicate in same batch
        if grid_key in seen_in_batch:
            logger.warning(
                "Skipping placement: cell (%d, %d) was already claimed in this reinforcement batch",
                pos.x,
                pos.y,
            )
            _reject(rejected, candidate, f"cell ({pos.x},{pos.y}) is used twice in this batch")
            continue

        # Rule 5: Hard budget ceiling (income check)
        unit_cost = get_unit_cost(candidate)
        if current_spent + unit_cost > budget:
            logger.warning(
                "[INCOME CHECK] Skipping placement: cost %d exceeds remaining unlocked budget %.1f (spent=%.1f, unlockedBudget=%.1f, matchBudget=%.1f)",
                unit_cost,
                max(0.0, budget - current_spent),
                current_spent,
                budget,
                match_budget,
            )
            _reject(rejected, candidate,
                    f"over budget: costs {unit_cost}, only {max(0.0, budget - current_spent):g} left")
            continue

        # Approved
        current_spent += unit_cost
        valid_placements.append(candidate)
        seen_in_batch.add(grid_key)
        occupied.add(grid_key)
        logger.info(
            "[INCOME CHECK] Reinforcement placement approved: defenceId=%d at (%d, %d) (cost=%d) -> new spent=%.1f, remaining unlocked budget=%.1f",
            candidate.defenceId,
            pos.x,
            pos.y,
            unit_cost,
            current_spent,
            max(0.0, budget - current_spent),
        )

    return valid_placements


@asynccontextmanager
async def _db_session(db: Any = None):
    """The request's session when given (one session per request; tests inject
    theirs), else a new one."""
    if db is not None:
        yield db
        return
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session


async def decide_reinforcements(snapshot: ReinforcementRequest, db: Any = None) -> ReinforcementResponse:
    """
    Evaluates reinforcement candidates from the agent / SDK.
    No heuristic guessing: if no candidate is valid or available, returns placeDefences=False.
    """
    # Wave complete or no living/queued enemies
    if snapshot.counts.activeEnemyCount <= 0 and snapshot.counts.enemiesQueued <= 0:
        return ReinforcementResponse(placeDefences=False, defencePlacementData=[])

    # 1. Resolve run and enemy config from DB to compute accurate budget & spent
    run_obj = None
    enemy_config_obj = None
    target_team_id = snapshot.teamID

    try:
        from app.models import EnemyConfig, Run, RunStatus, Submission, Team
        from sqlalchemy import select

        async with _db_session(db) as db:
            session_id = str(snapshot.sessionID or "").strip()
            if session_id and session_id not in _SESSION_STARTING_COINS:  # e.g. after a restart
                _SESSION_STARTING_COINS[session_id] = await _level_coins(db, session_id)
            run_obj = await db.get(Run, rid) if (rid := db_id(session_id)) else None
            if run_obj:
                if run_obj.enemy_config_id:
                    enemy_config_obj = await db.get(EnemyConfig, run_obj.enemy_config_id)
                sub_obj = await db.get(Submission, run_obj.submission_id)
                if sub_obj and sub_obj.team_id:
                    target_team_id = str(sub_obj.team_id)

            if run_obj is None and snapshot.teamID:
                team_id_raw = snapshot.teamID.strip()
                team: Team | None = None
                if team_id_raw.isdigit():
                    team = await db.get(Team, tid) if (tid := db_id(team_id_raw)) else None
                elif team_id_raw.lower().startswith("team") and team_id_raw[4:].isdigit():
                    team = await db.get(Team, tid) if (tid := db_id(team_id_raw[4:])) else None
                else:
                    team = (await db.execute(select(Team).where(Team.name == team_id_raw))).scalars().first()
                if team:
                    # Agents are stored per team id (team{id}_*.py), whatever the game sent.
                    target_team_id = str(team.id)
                    run_obj = (
                        await db.execute(
                            select(Run)
                            .join(Submission)
                            .where(Submission.team_id == team.id, Run.status == RunStatus.simulating)
                            .order_by(Run.id.desc())
                        )
                    ).scalars().first()
                    if run_obj and run_obj.enemy_config_id:
                        enemy_config_obj = await db.get(EnemyConfig, run_obj.enemy_config_id)
    except Exception as exc:
        logger.debug("Could not resolve run/enemy_config from DB for reinforcements: %s", exc)

    total_wave_enemies = snapshot.counts.totalEnemiesInWave
    if total_wave_enemies <= 0 and enemy_config_obj and enemy_config_obj.enemies:
        total_wave_enemies = wave_size(enemy_config_obj.enemies)

    raw_budget = enemy_config_obj.budget if enemy_config_obj else None
    starting_coins = _SESSION_STARTING_COINS.get(str(snapshot.sessionID or "").strip())
    match_budget = calculate_test_case_budget(raw_budget, total_wave_enemies, starting_coins)

    # Derive killed enemies and bounties so far
    enemies_killed = max(0, total_wave_enemies - snapshot.counts.enemiesQueued - snapshot.counts.activeEnemyCount)
    dead_in_list = sum(1 for e in snapshot.enemies if getattr(e, "isDead", False))
    enemies_killed = max(enemies_killed, dead_in_list)
    bounty_earned = float(enemies_killed * BOUNTY_PER_KILL)

    unlocked_budget = calculate_unlocked_budget(match_budget, enemies_killed, starting_coins=starting_coins)

    session_key = str(snapshot.sessionID or snapshot.teamID or "").strip()
    session_history = _SESSION_REINFORCEMENTS.get(session_key, []) if session_key else []
    cumulative_reinforcements_cost = sum(get_unit_cost(d) for d in session_history)
    living_board_cost = sum(get_unit_cost(d) for d in snapshot.defences)

    initial_turn0_cost = _SESSION_INITIAL_COST.get(session_key) if session_key else None
    if initial_turn0_cost is not None:
        current_spent = float(initial_turn0_cost + cumulative_reinforcements_cost)
    elif run_obj and run_obj.defence_placement:
        run_placement_cost = sum(get_unit_cost(d) for d in run_obj.defence_placement)
        current_spent = float(max(run_placement_cost, living_board_cost + cumulative_reinforcements_cost))
    else:
        current_spent = float(living_board_cost + cumulative_reinforcements_cost)

    remaining_budget = max(0.0, unlocked_budget - current_spent)

    snapshot.matchBudget = match_budget
    snapshot.unlockedBudget = unlocked_budget
    snapshot.budget = unlocked_budget
    snapshot.spent = current_spent
    snapshot.remainingBudget = remaining_budget

    logger.info(
        "[INCOME CHECK] Reinforcement ping for team=%s (session=%s, elapsed=%.1fs): waveEnemies=%d, killed=%d (+%.1f bounty), active=%d, queued=%d | matchBudget=%.1f, unlockedBudget=%.1f, spent=%.1f, remainingBudget=%.1f",
        snapshot.teamID,
        snapshot.sessionID,
        snapshot.elapsedSeconds,
        total_wave_enemies,
        enemies_killed,
        bounty_earned,
        snapshot.counts.activeEnemyCount,
        snapshot.counts.enemiesQueued,
        match_budget,
        unlocked_budget,
        current_spent,
        remaining_budget,
    )

    candidates: list[Any] = list(snapshot.candidatePlacements or [])

    # If no candidate placements in request payload, query the participant's agent via SDK
    if not candidates:
        try:
            from app.services.sdk_service import query_reinforcements
            snapshot_dict = snapshot.model_dump()
            snapshot_dict["matchBudget"] = match_budget
            snapshot_dict["unlockedBudget"] = unlocked_budget
            snapshot_dict["budget"] = unlocked_budget
            snapshot_dict["spent"] = current_spent
            snapshot_dict["remainingBudget"] = remaining_budget
            snapshot_dict["teamID"] = target_team_id  # the agent service checks they match
            raw_candidates = await query_reinforcements(target_team_id, snapshot_dict)
            candidates.extend(raw_candidates)
        except Exception as err:
            logger.debug("Could not query SDK for reinforcements: %s", err)

    # If the agent proposed nothing (or timed out / failed), do NOT run any heuristic code!
    if not candidates:
        logger.info(
            "[INCOME CHECK] No reinforcement candidates provided for team %s; skipping (remaining budget: %.1f).",
            snapshot.teamID,
            remaining_budget,
        )
        return ReinforcementResponse(placeDefences=False, defencePlacementData=[],
                                     budget=_budget_status(unlocked_budget, current_spent, match_budget))

    # Strictly validate candidates (no nudging, out-of-bounds check, occupancy check, budget check)
    rejected: list[dict[str, Any]] = []
    approved = validate_reinforcements(candidates, snapshot, rejected)

    if approved:
        if session_key:
            _SESSION_REINFORCEMENTS.setdefault(session_key, []).extend(
                [p.model_dump() if hasattr(p, "model_dump") else dict(p) for p in approved]
            )
        new_spent = current_spent + sum(get_unit_cost(p) for p in approved)
        rem_unlocked = max(0.0, unlocked_budget - new_spent)
        logger.info(
            "[INCOME CHECK] Reinforcements approved for team %s: %d placed (out of %d candidates). Total spent: %.1f, remaining unlocked budget: %.1f (match budget: %.1f)",
            snapshot.teamID,
            len(approved),
            len(candidates),
            new_spent,
            rem_unlocked,
            match_budget,
        )
        return ReinforcementResponse(
            placeDefences=True,
            defencePlacementData=approved,
            budget=_budget_status(unlocked_budget, new_spent, match_budget),
            rejected=rejected or None,
        )

    logger.info(
        "[INCOME CHECK] All %d reinforcement candidates for team %s were invalid or unaffordable and skipped. Unlocked budget: %.1f, spent: %.1f, remaining: %.1f",
        len(candidates),
        snapshot.teamID,
        unlocked_budget,
        current_spent,
        remaining_budget,
    )
    return ReinforcementResponse(placeDefences=False, defencePlacementData=[],
                                 budget=_budget_status(unlocked_budget, current_spent, match_budget),
                                 rejected=rejected or None)

