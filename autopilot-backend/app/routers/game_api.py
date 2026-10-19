"""
app/routers/game_api.py
────────────────────────
Unity-facing endpoints that mirror the Flask dummy server's interface.

POST /api/team
    Body: {"teamID": "team1"}
    Returns: {"defencePlacementData": [...], "enemies": [...]}
    Looks up the team by its string *name* (team1, team2 …) so Unity
    never needs to know numeric DB ids.

POST /api/results
    Body: any JSON payload the simulator sends as a game result.
    Stores it in the results_log table and returns {"status": "ok", ...}.

POST /api/team-data/{team_name}        (admin)
    Upsert the defencePlacementData + enemies for a named team.

GET  /api/team-data                    (admin)
    List all team data rows.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import EnemyConfig, ResultsLog, Run, RunStatus, Submission, Team, TeamData
from app.schemas import (
    ApiTeamError,
    ApiTeamRequest,
    GameSettings,
    ReinforcementRequest,
    ReinforcementResponse,
    SpawnSettings,
    TeamDataOut,
    TeamDataUpsert,
    UnityMatchResult,
)
from app.security import verify_admin
from app.services import evaluator_service, sdk_service
from app.services import competition, match_tracker, sdk_updates
from app.services.accounts import bearer_team
from app.services.reinforcement_service import db_id, decide_reinforcements
from app.services.scoring import finalize_submission
from app.ws_manager import manager
import logging
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["GameAPI"])


def _get_team_agent_bytes(team_id: int) -> bytes | None:
    """Find the latest uploaded agent.py for a team."""
    upload_dir = settings.UPLOAD_DIR
    if not upload_dir.exists():
        return None
    candidates = list(upload_dir.glob(f"team{team_id}_*.py")) + list(upload_dir.glob(f"team_{team_id}_*.py"))
    if not candidates:
        direct = upload_dir / f"team_{team_id}.py"
        if direct.exists():
            candidates.append(direct)
        else:
            default_agent = upload_dir / "agent.py"
            if default_agent.exists():
                candidates.append(default_agent)
    if candidates:
        latest = max(candidates, key=lambda p: (p.stat().st_mtime, p.name))
        return latest.read_bytes()
    return None


# ── POST /api/team ────────────────────────────────────────────────────────────
@router.post(
    "/team",
    response_model=TeamDataOut,
    responses={404: {"model": ApiTeamError, "description": "Team not found or unconfigured"}},
)
async def get_team_data(body: ApiTeamRequest, db: AsyncSession = Depends(get_db),
                        authorization: str = Header(default=""),
                        x_autopilot_sdk: str = Header(default="")):
    """
    Return level data for a team: sessionID, defencePlacementData, enemies,
    spawnSettings, and gameSettings.

    With `testCase` or `evalId` (set by the SDK) the level is that test case's
    (app/services/competition.py); a login token, if sent, names the team.
    """
    logged_in = bearer_team(authorization)
    if logged_in is not None:
        body.teamID = f"{logged_in:04d}"

    target_config_id: int | None = getattr(body, "configId", None) or getattr(body, "testCaseId", None)
    team_id_raw = body.teamID.strip()

    # Support compound syntax in teamID like "3:5", "0003:5", "team3_wave5"
    if ":" in team_id_raw:
        parts = team_id_raw.split(":", 1)
        team_id_raw = parts[0].strip()
        if parts[1].strip().isdigit():
            target_config_id = int(parts[1].strip())
    elif "_" in team_id_raw:
        match = re.search(r"^(.*?)(?:_test|_config|_wave|_tc|_case|_)?(\d+)$", team_id_raw, re.IGNORECASE)
        if match and match.group(1):
            team_id_raw = match.group(1).rstrip("_")
            target_config_id = int(match.group(2))

    logger.info("Received team level data request for teamID='%s', target_config_id=%s", team_id_raw, target_config_id)
    team: Team | None = None

    # Resolve team primarily by numeric ID (e.g. "0003", "3", "1001", "team3")
    if team_id_raw.isdigit():
        team = await db.get(Team, tid) if (tid := db_id(team_id_raw)) else None
    elif team_id_raw.lower().startswith("team") and team_id_raw[4:].isdigit():
        team = await db.get(Team, tid) if (tid := db_id(team_id_raw[4:])) else None
    else:
        # Fallback by name for backward compatibility
        team = (
            await db.execute(select(Team).where(Team.name == team_id_raw).order_by(Team.id))
        ).scalars().first()
        if team is None:
            team = (
                await db.execute(
                    select(Team).where(func.lower(Team.name) == team_id_raw.lower()).order_by(Team.id)
                )
            ).scalars().first()

    if team is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Team '{team_id_raw}' not found"},
        )
    if team.banned:
        return JSONResponse(status_code=403, content={"error": "This team is banned from the competition"})
    if not (body.testCase or body.evalId or target_config_id is not None):
        # Team IDs are logins, not levels: a game with no test case named
        # plays the first public one.
        first = (await competition.public_cases(db))[:1]
        if first:
            body.testCase = first[0].code
    if body.testCase or body.evalId:
        if settings.REQUIRE_LOGIN and logged_in != team.id:
            return JSONResponse(status_code=401, content={"error": "Log in first: press L in the SDK menu (team ID and password), then play again"})
        if sdk_updates.outdated(x_autopilot_sdk):  # NEW: before the session exists, so nothing is used up
            return JSONResponse(status_code=sdk_updates.OUTDATED_STATUS, content={"error": sdk_updates.OUTDATED_MESSAGE})
        try:
            return await competition.start_session(db, team, body, logged_in)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    # Fetch associated TeamData row
    td = (
        await db.execute(select(TeamData).where(TeamData.team_id == team.id).order_by(TeamData.id))
    ).scalars().first()

    if td is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"No data configured for team '{team_id_raw}'"},
        )

    # 1. Determine enemy wave for requested test case or team default
    enemies_source = td.enemies or []
    ec: EnemyConfig | None = None
    if target_config_id is not None:
        ec = await db.get(EnemyConfig, target_config_id)
        if ec and ec.enemies:
            enemies_source = ec.enemies

    # 2. Find team's latest submission
    latest_sub = (
        await db.execute(
            select(Submission)
            .where(Submission.team_id == team.id)
            .order_by(Submission.id.desc())
        )
    ).scalars().first()

    run_for_case: Run | None = None
    if latest_sub and target_config_id is not None:
        run_for_case = (
            await db.execute(
                select(Run)
                .where(Run.submission_id == latest_sub.id, Run.enemy_config_id == target_config_id)
                .order_by(Run.id.desc())
            )
        ).scalars().first()

    # 3. Always run participant's latest agent.py so re-edits are immediately live!
    chosen_defences = None
    agent_bytes = _get_team_agent_bytes(team.id)
    if agent_bytes:
        try:
            sdk_resp = await sdk_service.run_agent(agent_bytes, enemies_source)
            if sdk_resp.sdkStatus.valid and sdk_resp.defencePlacementData is not None:
                chosen_defences = [p.model_dump() for p in sdk_resp.defencePlacementData]
        except Exception as exc:
            logger.debug("Static run_agent returned no placement: %s", exc)

    # 4. Fallback if SDK was offline or no agent found
    if chosen_defences is None and run_for_case and run_for_case.defence_placement:
        chosen_defences = run_for_case.defence_placement

    if chosen_defences is None:
        latest_run_with_placement = (
            await db.execute(
                select(Run)
                .join(Submission)
                .where(Submission.team_id == team.id, Run.defence_placement.is_not(None))
                .order_by(Run.id.desc())
            )
        ).scalars().first()
        if latest_run_with_placement and latest_run_with_placement.defence_placement:
            chosen_defences = latest_run_with_placement.defence_placement
        else:
            chosen_defences = []

    # Solution B: If no custom placement was produced by an agent and placements match
    # the 6 default seed defences, clear to [] so the board starts blank and the participant's
    # get_reinforcements controls all placements.
    from app.services.seed_data import DEFAULT_DEFENCES
    def _is_default_seed(defences: list | None) -> bool:
        if not defences or len(defences) != len(DEFAULT_DEFENCES):
            return False
        try:
            norm_actual = sorted(
                (d.get("defenceId"), d.get("defenceGridPosition", {}).get("x"), d.get("defenceGridPosition", {}).get("y"))
                for d in defences
            )
            norm_default = sorted(
                (d.get("defenceId"), d.get("defenceGridPosition", {}).get("x"), d.get("defenceGridPosition", {}).get("y"))
                for d in DEFAULT_DEFENCES
            )
            return norm_actual == norm_default
        except Exception:
            return False

    if chosen_defences is None or _is_default_seed(chosen_defences):
        chosen_defences = []

    # 5. Correlate sessionID to Run
    if run_for_case:
        run_for_case.status = RunStatus.simulating
        run_for_case.defence_placement = chosen_defences
        await db.commit()
        session_id = str(run_for_case.id)
    else:
        active_run = (
            await db.execute(
                select(Run)
                .join(Submission)
                .where(Submission.team_id == team.id, Run.status == RunStatus.simulating)
                .order_by(Run.id.desc())
            )
        ).scalars().first()
        # Free play: the "p" keeps the session from ever reading as a run id.
        session_id = str(active_run.id) if active_run else "p" + uuid.uuid4().hex[:11]

    # Spawn settings: stored overlay or defaults
    spawn_defaults = SpawnSettings().model_dump()
    if td.spawn_settings and isinstance(td.spawn_settings, dict):
        spawn_defaults.update(td.spawn_settings)

    # Game settings: stored overlay or defaults; keep sendScreenshotToo strictly false
    game_defaults = GameSettings().model_dump()
    if td.game_settings and isinstance(td.game_settings, dict):
        game_defaults.update(td.game_settings)
    game_defaults["sendScreenshotToo"] = False

    # Ensure enemies have optional parameters like allowedRows populated
    enemies_out = []
    for e in enemies_source:
        entry = dict(e)
        if "allowedRows" not in entry or entry["allowedRows"] is None:
            entry["allowedRows"] = [0, 1, 2, 3, 4, 5]  # missing = any row (API guide)
        enemies_out.append(entry)

    # Calculate match budget and opening starting cash for the test case
    from app.services.evaluator_service import (
        BOUNTY_PER_KILL, calculate_test_case_budget, calculate_unlocked_budget, get_unit_cost, unit_costs,
        wave_size,
    )
    tc_budget = ec.budget if (ec and ec.budget) else None
    # The budget scales with the number of enemies, not the number of enemy types;
    # the referee computes it from totalEnemiesInWave the same way.
    match_budget = calculate_test_case_budget(tc_budget, wave_size(enemies_source))
    opening_budget = calculate_unlocked_budget(match_budget, enemies_killed_so_far=0)
    initial_spent = float(sum(get_unit_cost(d) for d in chosen_defences))
    remaining_opening = opening_budget - initial_spent

    from app.services.reinforcement_service import record_session_initial_defences
    record_session_initial_defences(session_id, chosen_defences)

    logger.info(
        "Serving level data to Unity for team=%s (id=%d): sessionID=%s, %d defences, %d enemy entries, budget=%.1f (opening=%.1f)",
        team.name,
        team.id,
        session_id,
        len(chosen_defences),
        len(enemies_out),
        match_budget,
        opening_budget,
    )
    logger.info(
        "[INCOME CHECK] Opening budget check for team=%s (id=%d, session=%s): matchBudget=%.1f, openingBudget=%.1f, initialSpent=%.1f (%d units), remainingOpening=%.1f (%s)",
        team.name,
        team.id,
        session_id,
        match_budget,
        opening_budget,
        initial_spent,
        len(chosen_defences),
        remaining_opening,
        "AFFORDABLE" if initial_spent <= opening_budget else "OVERSPENT",
    )
    if initial_spent > opening_budget:
        logger.warning(
            "[INCOME CHECK] Team %s initial defence placement exceeds opening budget: spent=%.1f > openingBudget=%.1f (overspend=%.1f)",
            team.name,
            initial_spent,
            opening_budget,
            initial_spent - opening_budget,
        )

    return TeamDataOut(
        sessionID=session_id,
        defencePlacementData=chosen_defences,
        enemies=enemies_out,
        spawnSettings=SpawnSettings(**spawn_defaults),
        gameSettings=GameSettings(**game_defaults),
        budget=opening_budget,
        matchBudget=match_budget,
        unitCosts=unit_costs(),
        bountyPerKill=BOUNTY_PER_KILL,
        coins=int(max(0.0, remaining_opening)),
    )


# ── POST /api/state  (snapshot without a placement request) ──────────────────
@router.post("/state", status_code=200)
async def state(payload: ReinforcementRequest):
    """A board snapshot the SDK forwards when the agent proposes nothing, so the
    match record still sees every unit."""
    match_tracker.record_snapshot(payload)
    return {"status": "ok"}


# ── POST /api/screenshot  (optional, acknowledged and discarded) ─────────────
@router.post("/screenshot", status_code=200)
async def screenshot(
    screenshot: UploadFile = File(...),
    teamID: str = Form(""),
    sessionID: str = Form(""),
):
    data = await screenshot.read()
    return {"status": "ok", "bytes": len(data)}


# ── POST /api/reinforcements ──────────────────────────────────────────────────
@router.post(
    "/reinforcements",
    response_model=ReinforcementResponse,
    status_code=200,
)
async def reinforcements(
    payload: ReinforcementRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Mid-run reinforcement check called by Unity when reinforcementsEnabled is true.
    Analyzes live board snapshot and returns place or skip decision with defence placement cards.
    Appends approved reinforcements to the run's expected defence placements.
    """
    match_tracker.record_snapshot(payload)
    decision = await decide_reinforcements(payload, db)
    # The game's coin counter is an int: never send null (0 once the wave is over).
    decision.coins = int(decision.budget.remaining) if decision.budget is not None else 0
    logger.info(
        "Reinforcement ping from team=%s (session=%s, elapsed=%.1fs): activeDefences=%d, activeEnemies=%d, queued=%d -> decision=%s, placed=%d",
        payload.teamID,
        payload.sessionID,
        payload.elapsedSeconds,
        payload.counts.activeDefenceCount,
        payload.counts.activeEnemyCount,
        payload.counts.enemiesQueued,
        decision.placeDefences,
        len(decision.defencePlacementData or []),
    )
    logger.info(
        "[INCOME CHECK] Reinforcement ping processed for team=%s (session=%s, elapsed=%.1fs): activeDefences=%d, activeEnemies=%d, queued=%d | decision=%s, placed=%d, remainingBudget=%.1f",
        payload.teamID,
        payload.sessionID,
        payload.elapsedSeconds,
        payload.counts.activeDefenceCount,
        payload.counts.activeEnemyCount,
        payload.counts.enemiesQueued,
        decision.placeDefences,
        len(decision.defencePlacementData or []),
        payload.remainingBudget if payload.remainingBudget is not None else -1.0,
    )
    if decision.placeDefences and decision.defencePlacementData:
        run: Run | None = None
        session_id = str(payload.sessionID or "").strip()
        if rid := db_id(session_id):
            run = await db.get(Run, rid)

        if run is None and payload.teamID:
            team_id_raw = payload.teamID.strip()
            team: Team | None = None
            if team_id_raw.isdigit():
                team = await db.get(Team, tid) if (tid := db_id(team_id_raw)) else None
            elif team_id_raw.lower().startswith("team") and team_id_raw[4:].isdigit():
                team = await db.get(Team, tid) if (tid := db_id(team_id_raw[4:])) else None
            else:
                team = (
                    await db.execute(select(Team).where(Team.name == team_id_raw))
                ).scalars().first()
            if team:
                run = (
                    await db.execute(
                        select(Run)
                        .join(Submission)
                        .where(Submission.team_id == team.id, Run.status == RunStatus.simulating)
                        .order_by(Run.id.desc())
                    )
                ).scalars().first()

        if run is not None:
            current_placements = list(run.defence_placement or [])
            for p in decision.defencePlacementData:
                p_dump = p.model_dump() if hasattr(p, "model_dump") else dict(p)
                current_placements.append(p_dump)
            run.defence_placement = current_placements
            await db.commit()

    return decision


# ── POST /api/results ─────────────────────────────────────────────────────────
@router.post("/results")
async def push_results(
    payload: UnityMatchResult,
    db: AsyncSession = Depends(get_db),
    authorization: str = Header(default=""),
):
    """
    Accept any JSON result payload from the simulator / Unity client,
    evaluate it, correlate to active run if any, and persist it in results_log.
    """
    payload_dict = payload.model_dump()
    entry = ResultsLog(data=payload_dict)
    db.add(entry)
    await db.flush()
    await db.refresh(entry)

    # Correlate to active Run: first by numeric sessionID, then by teamID
    run: Run | None = None
    team: Team | None = None
    session_id = str(payload.sessionID or "").strip()
    if rid := db_id(session_id):
        run = await db.get(Run, rid)

    if run is None and payload.teamID:
        team_id_raw = payload.teamID.strip()
        if team_id_raw.isdigit():
            team = await db.get(Team, tid) if (tid := db_id(team_id_raw)) else None
        elif team_id_raw.lower().startswith("team") and team_id_raw[4:].isdigit():
            team = await db.get(Team, tid) if (tid := db_id(team_id_raw[4:])) else None
        else:
            team = (
                await db.execute(select(Team).where(Team.name == team_id_raw))
            ).scalars().first()
            if team is None:
                team = (
                    await db.execute(select(Team).where(func.lower(Team.name) == team_id_raw.lower()))
                ).scalars().first()
        if team:
            run = (
                await db.execute(
                    select(Run)
                    .join(Submission)
                    .where(
                        Submission.team_id == team.id,
                        Run.status.in_([RunStatus.simulating, RunStatus.pending]),
                    )
                    .order_by(Run.id.desc())
                )
            ).scalars().first()

    expected_defences = run.defence_placement if run else None
    if expected_defences is None:
        # A live game: what the referee approved this session is the full list of
        # placements (the game's own result list can be incomplete), so the cost
        # score cannot be understated.
        from app.services.reinforcement_service import session_placements
        expected_defences = session_placements(str(payload.sessionID or "")) or None

    # Test-case budget & wave enemy count determination
    test_case_budget: float | None = None
    expected_enemies_count: int | None = None
    if run and run.enemy_config_id:
        enemy_config = await db.get(EnemyConfig, run.enemy_config_id)
        if enemy_config:
            if enemy_config.budget is not None and enemy_config.budget > 0:
                test_case_budget = float(enemy_config.budget)
            if enemy_config.enemies:
                expected_enemies_count = sum(
                    int(e.get("count", 1) if isinstance(e, dict) else getattr(e, "count", 1))
                    for e in enemy_config.enemies
                )
    elif team:
        td = (await db.execute(select(TeamData).where(TeamData.team_id == team.id)
                               .order_by(TeamData.id))).scalars().first()
        if td and td.enemies:
            expected_enemies_count = sum(
                int(e.get("count", 1) if isinstance(e, dict) else getattr(e, "count", 1))
                for e in td.enemies
            )

    case_wave = await competition.session_wave(db, session_id)
    if case_wave is not None:
        expected_enemies_count = case_wave

    # The game's report omits units that never dealt or took damage; complete it
    # from the snapshots and the referee's approved placements before scoring.
    from app.services.reinforcement_service import session_placements
    refused = await competition.check_result(
        db, session_id, payload_dict, bearer_team(authorization),
        match_tracker.pauses(session_id), session_placements(session_id))
    payload_dict, report_note = match_tracker.complete_results(
        session_id, payload_dict, session_placements(session_id))
    if refused:
        evaluator_output, score = {"valid": False, "error": f"Result refused: {refused}"}, None
    else:
        evaluator_output, score = await evaluator_service.evaluate(
            payload_dict,
            expected_defences=expected_defences,
            test_case_budget=test_case_budget,
            expected_total_enemies=expected_enemies_count,
            starting_coins=await competition.session_coins(db, session_id),
        )

    eval_stats = evaluator_output.get("stats") or {}
    logger.info(
        "Match results evaluated: team=%s, session=%s, duration=%.1fs, killed=%d/%d, destroyed=%d, totalDefDmg=%.1f, defCost=%.1f, budget=%.1f -> score=%s (valid=%s, run_id=%s)",
        payload.teamID,
        payload.sessionID,
        payload.durationSeconds,
        eval_stats.get("enemies_killed", payload.enemiesKilled),
        eval_stats.get("total_enemies", 0),
        payload.defencesDestroyed,
        payload.totalDefenceDamage,
        eval_stats.get("total_defence_cost", 0.0),
        eval_stats.get("test_case_budget", 0.0),
        score,
        evaluator_output.get("valid"),
        run.id if run else None,
    )
    logger.info(
        "[INCOME CHECK] Match completion audit: team=%s, killed=%d/%d (+%.1f bounty), defCost=%.1f, budget=%.1f (unlocked=%.1f, status=%s, savings=%.1f, overspent=%.1f)",
        payload.teamID,
        eval_stats.get("enemies_killed", payload.enemiesKilled),
        eval_stats.get("total_enemies", 0),
        eval_stats.get("bounty_earned", 0.0),
        eval_stats.get("total_defence_cost", 0.0),
        eval_stats.get("test_case_budget", 0.0),
        eval_stats.get("unlocked_budget", 0.0),
        eval_stats.get("cost_status", "UNKNOWN"),
        eval_stats.get("savings", 0.0),
        eval_stats.get("overspent", 0.0),
    )

    if run is not None and run.status in (RunStatus.simulating, RunStatus.pending, RunStatus.completed, RunStatus.invalid):
        if score is not None and evaluator_output.get("valid", True):
            current_best = float(run.score) if run.score is not None else 0.0
            if score >= current_best or run.score is None:
                run.score = score
                run.evaluator_output = evaluator_output
            run.status = RunStatus.completed
        else:
            if run.score is None:
                run.evaluator_output = evaluator_output
                run.score = 0.0
                run.status = RunStatus.invalid

        await db.commit()
        await finalize_submission(db, run.submission_id)
        await manager.send_result(run.id, run.score, evaluator_output)
    else:
        await db.commit()

    total = await db.scalar(select(func.count()).select_from(ResultsLog)) or 0

    game = await competition.record_result(
        db, session_id, payload_dict, evaluator_output, score,
        match_tracker.pauses(session_id), session_placements(session_id))

    from app.services.reinforcement_service import clear_session_ledger
    clear_session_ledger(str(payload.sessionID or ""))

    match_tracker.forget(session_id)
    response: dict[str, Any] = {"status": "ok", "stored": total, "evaluator_output": evaluator_output,
                                "report": report_note}
    if game is not None:
        response["testCase"] = game.test_case if game.mode == "play" else None
        response["evalSlot"] = game.eval_slot
        # A session is scored once: a repeated result gets the stored score back,
        # so the SDK prints what the boards and the admin show.
        response["evaluator_output"] = game.evaluator_output
        score = game.score if score is not None else None
    if score is not None:
        response["score"] = score
    # Unscored results still carry evaluator_output ({"valid": false, "error": ...}),
    # so the participant's SDK can say why.
    return response


# ── POST /api/team-data/{team_name}  (admin upsert) ──────────────────────────
@router.post(
    "/team-data/{team_name}",
    response_model=TeamDataOut,
    status_code=200,
    dependencies=[Depends(verify_admin)],
)
async def upsert_team_data(
    team_name: str,
    body: TeamDataUpsert,
    db: AsyncSession = Depends(get_db),
):
    """
    Create or replace the defencePlacementData, enemies, spawnSettings,
    and gameSettings for *team_name*.
    The team must already exist (create it via POST /teams first).
    """
    team: Team | None = None
    if team_name.isdigit():
        team = await db.get(Team, tid) if (tid := db_id(team_name)) else None
    elif team_name.lower().startswith("team") and team_name[4:].isdigit():
        team = await db.get(Team, tid) if (tid := db_id(team_name[4:])) else None
    else:
        team = (
            await db.execute(select(Team).where(Team.name == team_name))
        ).scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail=f"Team '{team_name}' not found")

    td = (
        await db.execute(select(TeamData).where(TeamData.team_id == team.id))
    ).scalar_one_or_none()

    placement = [p.model_dump() for p in body.defencePlacementData]
    enemies = [e.model_dump() for e in body.enemies]

    spawn_settings = (
        body.spawnSettings.model_dump()
        if body.spawnSettings
        else SpawnSettings().model_dump()
    )
    game_settings = (
        body.gameSettings.model_dump()
        if body.gameSettings
        else GameSettings().model_dump()
    )
    game_settings["sendScreenshotToo"] = False

    if td is None:
        td = TeamData(
            team_id=team.id,
            defence_placement_data=placement,
            enemies=enemies,
            spawn_settings=spawn_settings,
            game_settings=game_settings,
        )
        db.add(td)
    else:
        td.defence_placement_data = placement
        td.enemies = enemies
        td.spawn_settings = spawn_settings
        td.game_settings = game_settings

    await db.flush()
    await db.refresh(td)
    await db.commit()

    return TeamDataOut(
        sessionID="p" + uuid.uuid4().hex[:5],
        defencePlacementData=td.defence_placement_data,
        enemies=td.enemies,
        spawnSettings=SpawnSettings(**(td.spawn_settings or {})),
        gameSettings=GameSettings(**(td.game_settings or {})),
    )


# ── GET /api/team-data  (admin list) ─────────────────────────────────────────
@router.get(
    "/team-data",
    dependencies=[Depends(verify_admin)],
)
async def list_team_data(db: AsyncSession = Depends(get_db)):
    """Return all team data rows with their team names (admin use)."""
    rows = (await db.execute(select(TeamData))).scalars().all()
    out = []
    for td in rows:
        team = await db.get(Team, td.team_id)
        out.append(
            {
                "teamName": team.name if team else None,
                "teamId": td.team_id,
                "defencePlacementData": td.defence_placement_data,
                "enemies": td.enemies,
                "spawnSettings": td.spawn_settings,
                "gameSettings": td.game_settings,
                "createdAt": td.created_at.isoformat(),
            }
        )
    return out

