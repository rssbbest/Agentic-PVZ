"""`verify`: is agent.py ready to play?

Static checks (the same scan every pause runs), then a dry run: the agent is
called on a sample opening and a sample pause exactly as in a game, with the
real time limit, and what it returns is checked the way the SDK checks it
before the referee sees it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from autopilot_sdk.config import MAX_AGENT_TIME_S
from autopilot_sdk.contracts.schemas import ReinforcementSnapshot

UNIT_COSTS = {"0": 250, "1": 200, "2": 300, "3": 150}
WAVE = [{"enemyId": 0, "count": 4, "allowedRows": [1, 2, 3]}, {"enemyId": 2, "count": 3, "allowedRows": [0, 1, 2]},
        {"enemyId": 3, "count": 3, "allowedRows": [3, 4, 5]}]


def _unit(kind: str, i: int, x: float, y: int, health: float, max_health: float, **extra) -> dict:
    return {"id": f"{kind}_{i}", "type": kind, "health": health, "maxHealth": max_health,
            "position": {"x": x, "y": y}, **extra}


def sample_states(time_limit: float = MAX_AGENT_TIME_S) -> list[tuple[str, dict]]:
    """An opening and a mid-game pause, shaped like the states `play` gives the agent."""
    opening = ReinforcementSnapshot.model_validate({
        "teamID": "0000", "sessionID": "dry-run", "elapsedSeconds": 0.4,
        "counts": {"activeDefenceCount": 0, "activeEnemyCount": 1, "enemiesQueued": 9, "totalEnemiesInWave": 10},
        "defences": [], "enemies": [_unit("EnemClown", 0, 10, 2, 200, 200, isAttacking=False, isDead=False)],
    }).model_dump(mode="json")
    opening.update(phase="start", timeBudgetSeconds=time_limit, unitCosts=dict(UNIT_COSTS),
                   budget={"remaining": 3000.0, "spent": 0.0, "unlocked": 3000.0, "total": 3500.0},
                   upcomingEnemies=WAVE,
                   spawnSettings={"interArrivalTime": 6.0, "timeDecreaseRate": 0.25, "minInterArrivalTime": 1.0},
                   gameSettings={"reinforcementsEnabled": True, "reinforcementIntervalSeconds": 3.0,
                                 "reinforcementHoldSeconds": 0.2})
    pause = ReinforcementSnapshot.model_validate({
        "teamID": "0000", "sessionID": "dry-run", "elapsedSeconds": 12.0,
        "counts": {"activeDefenceCount": 2, "activeEnemyCount": 3, "enemiesQueued": 5, "totalEnemiesInWave": 10},
        "defences": [_unit("defenceShield", 0, 3, 2, 640, 700), _unit("defenceArcher", 1, 1, 2, 100, 100)],
        "enemies": [_unit("EnemyWizard", 2, 6, 1, 420, 500, isAttacking=False, isDead=False),
                    _unit("EnemyPirate", 3, 8, 4, 300, 300, isAttacking=False, isDead=False),
                    _unit("EnemClown", 0, 4, 2, 60, 200, isAttacking=True, isDead=False)],
    }).model_dump(mode="json")
    pause.update(phase="pause", timeBudgetSeconds=time_limit, unitCosts=dict(UNIT_COSTS),
                 budget={"remaining": 2350.0, "spent": 450.0, "unlocked": 2800.0, "total": 3500.0})
    return [("opening", opening), ("pause", pause)]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)


def verify_agent(agent_path: Path | str, dry_run: bool = True, time_limit: float = MAX_AGENT_TIME_S) -> Report:
    from autopilot_sdk.local_proxy import AgentFileError, check_agent_file, wire_candidates
    from autopilot_sdk.runtime.reinforcements import run_reinforcements
    from autopilot_sdk.verifier.static_scan import scan_file

    report = Report()
    path = Path(agent_path).expanduser()
    if not path.is_file():
        report.checks.append(Check("agent.py found", False, f"no file at {path}"))
        return report
    scan = scan_file(str(path))
    detail = "; ".join(map(str, scan.violations)) or ("; ".join(map(str, scan.warnings)) or "imports and calls allowed")
    report.checks.append(Check("Safety scan", scan.passed, detail))
    try:
        check_agent_file(path)
        report.checks.append(Check("get_reinforcements(state) defined", True))
    except AgentFileError as exc:
        report.checks.append(Check("get_reinforcements(state) defined", False, str(exc)))
    if not dry_run or not report.passed:
        return report
    source = path.read_bytes()
    for label, state in sample_states(time_limit):
        started = time.monotonic()
        raw = run_reinforcements(source, state, time_limit)
        took = time.monotonic() - started
        candidates = wire_candidates(raw if isinstance(raw, list) else [])
        cost = sum(UNIT_COSTS.get(str(c["defenceId"]), 0) for c in candidates)
        ok = took <= time_limit + 0.5
        report.checks.append(Check(
            f"Dry run: {label}", ok,
            f"{len(candidates)} placement(s), cost {cost} of {state['budget']['remaining']:g} coins, "
            f"{took:.1f} s of {time_limit:g} s" + ("" if raw or candidates else " (nothing proposed: is that intended?)")))
    return report
