"""
app/services/evaluator_service.py
───────────────────────────────────
Direct in-process evaluation and scoring engine for AutoPilot Tower Defense.
Evaluates the match report produced by Unity's LoggingSystem.cs:
  - teamID: string
  - sessionID: string (e.g. from /api/team or "Not Implemented Yet")
  - success: bool (true = victory, false = defeat / unwired client flag)
  - durationSeconds: float
  - enemiesKilled: int
  - defencesDestroyed: int
  - totalDefenceDamage: float
  - totalEnemyDamage: float
  - totalDefenceCost: int | float (currency spent on placed defences)
  - defences: list[DefenceLog] (id, type, position {x,y}, cost, damageDealt, destroyed, destroyedAtTime)
  - enemies: list[EnemyLog] (id, type, damageDealt, killed, killedAtTime)
  - defenceTypeStats: list[DefenceTypeStats] (type, unitCount, totalDamageDealt, unitsDestroyed, costPerUnit, totalCost)
  - enemyTypeStats: list[EnemyTypeStats] (type, unitCount, totalDamageDealt, unitsKilled)

Marking Scheme (100 pts base with Test-Case Based Budget & Negative Marking):
  - Outcome: 25 pts (Victory = 25 pts, Defeat = 5 pts)
  - Kill Efficiency: 25 pts (enemies killed / total wave enemies)
  - Defence Survival: 25 pts (surviving defences / placed defences), scaled by the
    share of the wave killed unless the match was won
  - Speed Bonus: 10 pts (smooth continuous curve for clearing under par time)
  - Economy Score: 15 pts max, scaled like survival (under budget awards 10 base + up to +5 savings bonus; over budget awards 0 base and aggressively penalizes with -50 pts per 100% overspend)
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any

logger = logging.getLogger(__name__)


DEFENCE_COST_MAP: dict[str, int] = {
    # Prefab names (lowercase)
    "defencepenguin": 250,
    "penguin": 250,
    "defencesoilder": 200,
    "defencesoldier": 200,
    "defencesolider": 200,  # build9 spells it this way
    "soilder": 200,
    "soldier": 200,
    "defencearcher": 300,
    "archer": 300,
    "defenceshield": 150,
    "shield": 150,
    # Prefab IDs as strings
    "0": 250,
    "1": 200,
    "2": 300,
    "3": 150,
}


def unit_costs() -> dict[str, int]:
    """Cost per defenceId, as sent to the game/SDK with the level."""
    return {k: v for k, v in DEFENCE_COST_MAP.items() if k.isdigit()}


def _safe_int(val: Any, default: int = 0) -> int:
    """Safely converts value to int, avoiding booleans or non-numeric types."""
    if val is None or isinstance(val, bool):
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return int(f)
    except (ValueError, TypeError):
        return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely converts value to float, filtering booleans, NaN, and Inf."""
    if val is None or isinstance(val, bool):
        return default
    try:
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else f
    except (ValueError, TypeError):
        return default


def normalize_simulation_payload(result: Any) -> dict[str, Any]:
    """
    Normalizes simulation payloads across Unity, portable simulator, and legacy formats:
    - Normalizes Pydantic models to dicts
    - Maps alternative/legacy keys: won -> success, killed -> enemiesKilled
    - Calculates defencesDestroyed from defencesPlaced - defencesAlive if omitted
    - Resolves teamId / team_id or engine='portable' -> teamID
    """
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()
    elif hasattr(result, "__dict__") and not isinstance(result, dict):
        result = vars(result)
    elif not isinstance(result, dict):
        return {}

    payload = dict(result)

    # 1. Team ID fallback
    if not payload.get("teamID"):
        if payload.get("teamId"):
            payload["teamID"] = str(payload["teamId"])
        elif payload.get("team_id"):
            payload["teamID"] = str(payload["team_id"])
        elif payload.get("engine") == "portable":
            payload["teamID"] = "PORTABLE_SIMULATOR"

    # 2. Success / outcome mapping
    if "success" not in payload:
        if payload.get("won") is not None:
            payload["success"] = bool(payload["won"])

    # 3. Enemies killed mapping
    if "enemiesKilled" not in payload:
        if payload.get("killed") is not None:
            payload["enemiesKilled"] = payload["killed"]

    # 4. Defences destroyed mapping
    if "defencesDestroyed" not in payload:
        placed = payload.get("defencesPlaced")
        alive = payload.get("defencesAlive")
        if placed is not None and alive is not None:
            payload["defencesDestroyed"] = max(0, _safe_int(placed) - _safe_int(alive))

    return payload


def _normalize_expected_defences(expected_defences: list[Any] | None) -> list[dict[str, Any]]:
    """Ensures expected_defences items are converted from Pydantic models to dicts."""
    if not expected_defences:
        return []
    normalized: list[dict[str, Any]] = []
    for d in expected_defences:
        if hasattr(d, "model_dump"):
            normalized.append(d.model_dump())
        elif hasattr(d, "dict"):
            normalized.append(d.dict())
        elif isinstance(d, dict):
            normalized.append(d)
        elif hasattr(d, "__dict__"):
            normalized.append(vars(d))
    return normalized


def get_unit_cost(unit: Any, default: int = 200) -> int:
    """Derives unit cost from explicit field, type, id prefix, or card defenceId."""
    if hasattr(unit, "model_dump"):
        unit = unit.model_dump()
    elif hasattr(unit, "dict"):
        unit = unit.dict()
    elif hasattr(unit, "__dict__") and not isinstance(unit, dict):
        unit = vars(unit)
    if not isinstance(unit, dict):
        return default

    # 1. Direct explicit cost field
    raw_cost = unit.get("cost")
    if raw_cost is not None and not isinstance(raw_cost, bool):
        c = _safe_int(raw_cost, 0)
        if c > 0:
            return c

    # 2. Type lookup
    unit_type = str(unit.get("type") or "").strip().lower()
    if unit_type in DEFENCE_COST_MAP:
        return DEFENCE_COST_MAP[unit_type]

    # 3. Unit ID lookup (e.g. "defencePenguin_0" or "defencePenguin(Clone)")
    unit_id = str(unit.get("id") or "").strip().lower()
    base_id = unit_id.split("_")[0].replace("(clone)", "").strip()
    if base_id in DEFENCE_COST_MAP:
        return DEFENCE_COST_MAP[base_id]

    # 4. Placement card defenceId (0, 1, 2, 3)
    def_id = unit.get("defenceId")
    if def_id is not None and str(def_id) in DEFENCE_COST_MAP:
        return DEFENCE_COST_MAP[str(def_id)]

    return default


def wave_size(entries: list[Any] | None) -> int:
    """Number of enemies in a wave given as [{"enemyId": 0, "count": 5}, ...]."""
    total = 0
    for entry in entries or []:
        count = entry.get("count", 1) if isinstance(entry, dict) else getattr(entry, "count", 1)
        total += max(0, _safe_int(count, 1))
    return total


# Coins every match starts with (the game shows them as "Coins: N").
# 3000 makes the test cases winnable with a sound strategy (1000 and 2000 were not).
STARTING_COINS = float(os.environ.get("AUTOPILOT_STARTING_COINS") or 3000)
BOUNTY_PER_KILL = 50.0  # coins earned per enemy killed


def _start(starting_coins: float | None) -> float:
    """A level's starting coins, or the event default."""
    return float(starting_coins) if starting_coins else STARTING_COINS


def calculate_test_case_budget(test_case_budget: float | None, total_wave_enemies: int,
                               starting_coins: float | None = None) -> float:
    """
    The most a match can spend: the starting coins (the level's `coins`, else
    STARTING_COINS) plus the bounty for every enemy in the wave, so every kill
    pays out. A budget set on the test case / enemy config raises it further.
    """
    earnable = _start(starting_coins) + max(1, total_wave_enemies) * BOUNTY_PER_KILL
    return float(max(earnable, test_case_budget or 0.0))


def calculate_unlocked_budget(
    total_match_budget: float,
    enemies_killed_so_far: int,
    bounty_per_kill: float = BOUNTY_PER_KILL,
    starting_coins: float | None = None,
) -> float:
    """
    Coins available so far: the starting coins plus the bounty per enemy
    killed, capped at total_match_budget.
    """
    unlocked = _start(starting_coins) + max(0, enemies_killed_so_far) * bounty_per_kill
    return float(min(total_match_budget, unlocked))


def validate_simulation_result(
    result: dict[str, Any],
    expected_defences: list[dict[str, Any]] | None = None,
) -> tuple[bool, str | None]:
    """
    Validates the Unity simulation report for engine crashes, corruption,
    and invalid defense placement data before computing any score.

    Returns:
        (is_valid, error_message)
    """
    result = normalize_simulation_payload(result)
    expected_defences = _normalize_expected_defences(expected_defences)

    if not isinstance(result, dict) or not result:
        return False, "Simulation result is empty or not a valid dictionary"

    # 1. Required fields check with strict type enforcement
    team_id = result.get("teamID")
    if not isinstance(team_id, str) or not team_id.strip():
        return False, "Missing or empty required field: 'teamID'"

    if "success" not in result or not isinstance(result["success"], bool):
        return False, "Missing or invalid 'success' field: must be a boolean"

    # 2. Crash & Duration Sanity Check
    duration = result.get("durationSeconds")
    if (
        duration is None
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or math.isnan(duration)
        or math.isinf(duration)
        or duration <= 0.05
    ):
        return False, f"Invalid simulation duration ({duration}s). Unity engine likely crashed or aborted on startup."

    # 3. Numeric Types and Non-Negative Sanity Checks (strictly disallowing booleans)
    raw_killed = result.get("enemiesKilled", 0)
    if isinstance(raw_killed, bool) or (isinstance(raw_killed, float) and (math.isnan(raw_killed) or math.isinf(raw_killed))):
        return False, f"enemiesKilled must be a non-negative integer, got: {raw_killed}"
    enemies_killed = _safe_int(raw_killed, -1)
    if enemies_killed < 0:
        return False, f"enemiesKilled must be a non-negative integer, got: {raw_killed}"

    raw_destroyed = result.get("defencesDestroyed", 0)
    if isinstance(raw_destroyed, bool) or (isinstance(raw_destroyed, float) and (math.isnan(raw_destroyed) or math.isinf(raw_destroyed))):
        return False, f"defencesDestroyed must be a non-negative integer, got: {raw_destroyed}"
    defences_destroyed = _safe_int(raw_destroyed, -1)
    if defences_destroyed < 0:
        return False, f"defencesDestroyed must be a non-negative integer, got: {raw_destroyed}"

    total_def_dmg = result.get("totalDefenceDamage", 0.0)
    if (
        isinstance(total_def_dmg, bool)
        or not isinstance(total_def_dmg, (int, float))
        or math.isnan(total_def_dmg)
        or math.isinf(total_def_dmg)
        or total_def_dmg < 0
    ):
        return False, f"totalDefenceDamage cannot be negative or invalid, got: {total_def_dmg}"

    total_enemy_dmg = result.get("totalEnemyDamage", 0.0)
    if (
        isinstance(total_enemy_dmg, bool)
        or not isinstance(total_enemy_dmg, (int, float))
        or math.isnan(total_enemy_dmg)
        or math.isinf(total_enemy_dmg)
        or total_enemy_dmg < 0
    ):
        return False, f"totalEnemyDamage cannot be negative or invalid, got: {total_enemy_dmg}"

    # 4. List Structures Check
    defences = result.get("defences", [])
    if defences is not None and not isinstance(defences, list):
        return False, "'defences' field must be a list if provided"
    if defences is None:
        defences = []

    enemies = result.get("enemies", [])
    if enemies is not None and not isinstance(enemies, list):
        return False, "'enemies' field must be a list if provided"
    if enemies is None:
        enemies = []

    defence_type_stats = result.get("defenceTypeStats", [])
    if defence_type_stats is not None and not isinstance(defence_type_stats, list):
        return False, "'defenceTypeStats' field must be a list if provided"
    if defence_type_stats is None:
        defence_type_stats = []

    enemy_type_stats = result.get("enemyTypeStats", [])
    if enemy_type_stats is not None and not isinstance(enemy_type_stats, list):
        return False, "'enemyTypeStats' field must be a list if provided"
    if enemy_type_stats is None:
        enemy_type_stats = []

    # 5. Logical Consistency / Anti-Corruption Checks
    type_def_count = sum(_safe_int(s.get("unitCount", 0)) for s in defence_type_stats if isinstance(s, dict))
    simulated_def_count = max(len(defences), type_def_count, _safe_int(result.get("defencesPlaced"), 0))
    expected_count = len(expected_defences) if expected_defences else 0
    total_defences_count = max(simulated_def_count, expected_count)
    if defences_destroyed > total_defences_count:
        return (
            False,
            f"Impossible stats: defencesDestroyed ({defences_destroyed}) exceeds defences placed ({total_defences_count}).",
        )

    type_enemy_count = sum(_safe_int(s.get("unitCount", 0)) for s in enemy_type_stats if isinstance(s, dict))
    total_enemies_count = max(len(enemies), type_enemy_count, _safe_int(result.get("totalEnemies"), 0))
    if enemies_killed > total_enemies_count:
        return (
            False,
            f"Impossible stats: enemiesKilled ({enemies_killed}) exceeds total enemies spawned ({total_enemies_count}).",
        )

    return True, None


def calculate_score(
    simulator_result: dict[str, Any],
    expected_defences: list[dict[str, Any]] | None = None,
    test_case_budget: float | None = None,
    par_time: float = 120.0,
    max_simulation_time: float = 180.0,
    expected_total_enemies: int | None = None,
    starting_coins: float | None = None,
) -> tuple[float, dict[str, float], dict[str, Any]]:
    """
    Computes a clean, multi-factor score (0-100) and breakdown directly
    from Unity's battle report:

    Score Allocation (Total 100 pts base):
    - Outcome: 25 pts for Victory (5 pts for Defeat)
    - Kill Efficiency: 25 pts (enemies killed / total wave enemies)
    - Defence Survival: 25 pts (surviving defences / placed defences)
    - Speed Bonus: 10 pts (smooth continuous curve for clearing under par time)
    - Economy Score: 15 pts (10 base + savings bonus up to +5 pts, or negative penalty for overspending)
    """
    simulator_result = normalize_simulation_payload(simulator_result)
    expected_defences = _normalize_expected_defences(expected_defences)

    # 1. Match Outcome (25 pts max)
    # Unity's success flag is authoritative (isWin in GameManager / LosingTrigger).
    # - If success is explicitly True: VICTORY.
    # - If success is explicitly False: DEFEAT (e.g. enemy reached the base / LosingTrigger).
    # - Only if success is None (omitted in legacy / custom payloads) do we infer outcome from wave kills.
    raw_success = simulator_result.get("success")
    enemies_list = simulator_result.get("enemies")
    if not isinstance(enemies_list, list):
        enemies_list = []

    enemies_killed = _safe_int(simulator_result.get("enemiesKilled", 0))

    enemy_type_stats = simulator_result.get("enemyTypeStats")
    if not isinstance(enemy_type_stats, list):
        enemy_type_stats = []

    total_enemies_from_types = sum(
        _safe_int(t.get("unitCount", 0)) for t in enemy_type_stats if isinstance(t, dict)
    )
    total_enemies = max(
        len(enemies_list),
        enemies_killed,
        total_enemies_from_types,
        _safe_int(simulator_result.get("totalEnemies"), 0),
        _safe_int(expected_total_enemies, 0),
        1,
    )

    any_survived_in_list = any(
        not bool(e.get("killed", False))
        for e in enemies_list
        if isinstance(e, dict)
    )

    if raw_success is True:
        is_victory = True
        victory_inferred = False
    elif raw_success is False:
        is_victory = False
        victory_inferred = False
    elif raw_success is None and not any_survived_in_list and (
        (
            len(enemies_list) > 0
            and all(bool(e.get("killed", False)) for e in enemies_list if isinstance(e, dict))
            and (total_enemies_from_types == 0 or enemies_killed >= total_enemies_from_types)
        )
        or (total_enemies_from_types > 0 and enemies_killed >= total_enemies_from_types)
    ):
        is_victory = True
        victory_inferred = True
    else:
        is_victory = False
        victory_inferred = False

    outcome_label = "VICTORY" if is_victory else "DEFEAT"
    outcome_score = 25.0 if is_victory else 0.0

    # 2. Kill Efficiency (25 pts max)
    kill_ratio = min(1.0, max(0.0, enemies_killed / total_enemies))
    kill_score = round(kill_ratio * 25.0, 2)
    # Survival and economy are earned for the share of the wave actually handled:
    # a defeat after a few seconds must not score defences that were never
    # reached, or money that was never needed.
    cleared_ratio = 1.0 if is_victory else kill_ratio

    # 3. Defence Survival (25 pts max)
    defences_list = simulator_result.get("defences")
    if not isinstance(defences_list, list):
        defences_list = []

    defences_destroyed = _safe_int(simulator_result.get("defencesDestroyed", 0))

    defence_type_stats = simulator_result.get("defenceTypeStats")
    if not isinstance(defence_type_stats, list):
        defence_type_stats = []

    total_defences_from_types = sum(
        _safe_int(t.get("unitCount", 0)) for t in defence_type_stats if isinstance(t, dict)
    )
    expected_count = len(expected_defences) if expected_defences else 0
    defences_placed = max(
        len(defences_list),
        total_defences_from_types,
        expected_count,
        _safe_int(simulator_result.get("defencesPlaced"), 0),
    )

    if defences_placed > 0:
        if defences_list and len(defences_list) == defences_placed:
            defences_survived = sum(
                1 for d in defences_list if isinstance(d, dict) and not d.get("destroyed", False)
            )
        else:
            defences_survived = max(0, defences_placed - defences_destroyed)
        survival_ratio = min(1.0, max(0.0, defences_survived / defences_placed))
        survival_score = round(survival_ratio * cleared_ratio * 25.0, 2)
    else:
        defences_survived = 0
        survival_score = 0.0

    # 4. Clear Speed Bonus (10 pts max) - Smooth continuous transition at par_time
    duration = _safe_float(simulator_result.get("durationSeconds", 0.0))
    time_taken = max(0.1, duration)

    if is_victory:
        safe_par = max(0.1, par_time)
        if time_taken <= safe_par:
            time_factor = (safe_par - time_taken) / safe_par
            speed_bonus = round(5.0 + (time_factor * 5.0), 2)
        else:
            time_remaining = max(0.0, max_simulation_time - time_taken)
            denom = max(1.0, max_simulation_time - safe_par)
            speed_bonus = round(min(5.0, max(0.0, (time_remaining / denom) * 5.0)), 2)
    else:
        speed_bonus = 0.0

    # 5. Economy & Cost Score (15 pts base with test-case based negative marking)
    budget = calculate_test_case_budget(test_case_budget, total_enemies, starting_coins)

    # Derive total currency spent on defences
    payload_cost = _safe_float(
        simulator_result.get("totalDefenceCost")
        or simulator_result.get("totalCost")
        or 0.0
    )
    if payload_cost > 0:
        total_defence_cost = payload_cost
    else:
        calc_def_costs = sum(get_unit_cost(d) for d in defences_list if isinstance(d, dict))
        type_costs = sum(
            _safe_float(t.get("totalCost", 0.0)) or (_safe_float(t.get("unitCount", 0.0)) * get_unit_cost(t))
            for t in defence_type_stats
            if isinstance(t, dict)
        )
        expected_costs = sum(get_unit_cost(d) for d in (expected_defences or []) if isinstance(d, dict))
        total_defence_cost = max(float(calc_def_costs), float(type_costs), float(expected_costs))
        if total_defence_cost == 0.0 and defences_placed > 0:
            total_defence_cost = float(defences_placed * 100)

    # If within or under budget: 10 base + up to 5 bonus points for savings
    # If over budget: Strict negative marking penalty (0 base + -50 pts per 100% overspend)
    # If no defences were placed at all, no economy score/bonus is awarded (prevents 0-defence exploit)
    if defences_placed == 0:
        cost_score = 0.0
    elif total_defence_cost <= budget:
        savings_ratio = (budget - total_defence_cost) / max(1.0, budget)
        cost_score = round((10.0 + (savings_ratio * 5.0)) * cleared_ratio, 2)
    else:
        overspend_ratio = (total_defence_cost - budget) / max(1.0, budget)
        cost_score = round(-(overspend_ratio * 50.0), 2)

    # A lost game scores only its kills: no outcome, survival, speed or economy points.
    if not is_victory:
        survival_score = speed_bonus = cost_score = 0.0

    # 6. Final Score (0 - 100)
    raw_score = outcome_score + kill_score + survival_score + speed_bonus + cost_score
    final_score = round(max(0.0, min(100.0, raw_score)), 2)

    breakdown = {
        "outcome_score": outcome_score,
        "kill_score": kill_score,
        "survival_score": survival_score,
        "speed_bonus": speed_bonus,
        "cost_score": cost_score,
    }

    bounty_earned = round(float(enemies_killed * BOUNTY_PER_KILL), 2)
    unlocked_budget = round(calculate_unlocked_budget(budget, enemies_killed, starting_coins=starting_coins), 2)
    savings_amount = round(max(0.0, budget - total_defence_cost), 2)
    overspent_amount = round(max(0.0, total_defence_cost - budget), 2)

    total_def_dmg = _safe_float(simulator_result.get("totalDefenceDamage", 0.0))
    stats = {
        "team_id": str(simulator_result.get("teamID") or ""),
        "session_id": str(simulator_result.get("sessionID") or "Not Implemented Yet"),
        "outcome": outcome_label,
        "victory_inferred": victory_inferred,
        "duration_seconds": round(duration, 2),
        "enemies_killed": enemies_killed,
        "total_enemies": total_enemies,
        "bounty_earned": bounty_earned,
        "unlocked_budget": unlocked_budget,
        "defences_placed": defences_placed,
        "defences_survived": defences_survived,
        "defences_destroyed": defences_destroyed,
        "total_defence_cost": round(total_defence_cost, 2),
        "test_case_budget": round(budget, 2),
        "savings": savings_amount,
        "overspent": overspent_amount,
        "cost_status": "UNDER_BUDGET" if total_defence_cost <= budget else "OVER_BUDGET",
        "damage_per_cost": round(total_def_dmg / max(1.0, total_defence_cost), 2),
        "total_defence_damage": total_def_dmg,
        "total_enemy_damage": _safe_float(simulator_result.get("totalEnemyDamage", 0.0)),
        "defence_type_stats": defence_type_stats,
        "enemy_type_stats": enemy_type_stats,
    }

    return final_score, breakdown, stats


async def evaluate(
    simulator_result: dict[str, Any],
    expected_defences: list[dict[str, Any]] | None = None,
    test_case_budget: float | None = None,
    expected_total_enemies: int | None = None,
    starting_coins: float | None = None,
) -> tuple[dict[str, Any], float | None]:
    """
    Direct in-process evaluation function called by autopilot-backend.

    1. Validates the simulation payload for engine crashes or corrupted stats.
    2. Calculates score and breakdown if valid.
    3. Returns (output_dict, final_score) or (error_dict, None) on failure.
    """
    if hasattr(simulator_result, "model_dump"):
        simulator_result = simulator_result.model_dump()

    is_valid, error_msg = validate_simulation_result(simulator_result, expected_defences)
    if not is_valid:
        logger.warning("Simulation result failed validation: %s", error_msg)
        return {"valid": False, "error": error_msg}, None

    score, breakdown, stats = calculate_score(
        simulator_result,
        expected_defences=expected_defences,
        test_case_budget=test_case_budget,
        expected_total_enemies=expected_total_enemies,
        starting_coins=starting_coins,
    )

    logger.info(
        "Evaluation finished: outcome=%s, score=%.2f, cost=%.1f/%.1f (%s), kills=%d/%d, survived=%d/%d",
        stats.get("outcome"),
        score,
        stats.get("total_defence_cost", 0.0),
        stats.get("test_case_budget", 0.0),
        stats.get("cost_status"),
        stats.get("enemies_killed", 0),
        stats.get("total_enemies", 0),
        stats.get("defences_survived", 0),
        stats.get("defences_placed", 0),
    )

    logger.info(
        "[INCOME CHECK] Evaluation budget audit: team=%s, session=%s: matchBudget=%.1f, unlockedBudget=%.1f, totalDefenceCost=%.1f (%s) | enemiesKilled=%d/%d (+%.1f bounty) | savings=%.1f, overspent=%.1f | costScore=%.2f",
        stats.get("team_id"),
        stats.get("session_id"),
        stats.get("test_case_budget", 0.0),
        stats.get("unlocked_budget", 0.0),
        stats.get("total_defence_cost", 0.0),
        stats.get("cost_status"),
        stats.get("enemies_killed", 0),
        stats.get("total_enemies", 0),
        stats.get("bounty_earned", 0.0),
        stats.get("savings", 0.0),
        stats.get("overspent", 0.0),
        breakdown.get("cost_score", 0.0),
    )

    output = {
        "valid": True,
        "score": score,
        "breakdown": breakdown,
        "stats": stats,
    }

    return output, score
