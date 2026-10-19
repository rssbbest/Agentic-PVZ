"""
app/services/match_tracker.py  –  the full picture of a live match

The game's end-of-match report lists only units that dealt or took damage: a
defence that never fired, or an enemy that walked through an empty lane, is
missing. Every reinforcement snapshot, however, carries every live defence and
enemy. This module remembers each unit seen in any snapshot of a session
(`/api/reinforcements`, `/api/state`) and completes the final report with the
units it left out, so scoring and the participant's summary count everything.
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFENCE_TYPES = {0: "defencePenguin", 1: "defenceSoilder", 2: "defenceArcher", 3: "defenceShield"}
SESSION_TTL_S = 6 * 3600

# sessionID -> {"defences": {id: unit}, "enemies": {id: unit}, "seen": monotonic time}
_SESSIONS: dict[str, dict[str, Any]] = {}


def _as_dict(unit: Any) -> dict[str, Any]:
    return unit.model_dump() if hasattr(unit, "model_dump") else dict(unit or {})


def _cell(position: Any) -> tuple[int, int] | None:
    position = _as_dict(position)
    try:
        return round(float(position["x"])), round(float(position["y"]))
    except (KeyError, TypeError, ValueError):
        return None


_last_prune = 0.0


def _prune() -> None:
    global _last_prune
    if time.monotonic() - _last_prune < 60:  # a few hundred matches: once a minute is plenty
        return
    _last_prune = time.monotonic()
    cutoff = time.monotonic() - SESSION_TTL_S
    for sid in [s for s, m in _SESSIONS.items() if m["seen"] < cutoff]:
        _SESSIONS.pop(sid, None)


def record_snapshot(snapshot: Any) -> None:
    """Remember every defence and enemy in a game snapshot."""
    snap = _as_dict(snapshot)
    sid = str(snap.get("sessionID") or "").strip()
    # The SDK's opening call (t=0) describes the level, not the game's units;
    # its placements are covered by the referee's approved list.
    if not sid or not float(snap.get("elapsedSeconds") or 0) > 0:
        return
    _prune()
    match = _SESSIONS.setdefault(sid, {"defences": {}, "enemies": {}, "seen": 0.0, "pauses": 0})
    match["seen"] = time.monotonic()
    match["pauses"] = match.get("pauses", 0) + 1
    for raw in snap.get("defences") or []:
        unit = _as_dict(raw)
        if unit.get("id"):
            match["defences"][str(unit["id"])] = {
                "type": unit.get("type"), "cell": _cell(unit.get("position")),
                "health": unit.get("health"),
            }
    for raw in snap.get("enemies") or []:
        unit = _as_dict(raw)
        if unit.get("id"):
            known = match["enemies"].get(str(unit["id"]), {})
            match["enemies"][str(unit["id"])] = {
                "type": unit.get("type"), "cell": _cell(unit.get("position")),
                "dead": bool(known.get("dead")) or bool(unit.get("isDead")) or (unit.get("health") or 0) <= 0,
            }


def pauses(session_id: str) -> int:
    """How many pause snapshots the backend received for this game."""
    return int((_SESSIONS.get(str(session_id or "").strip()) or {}).get("pauses", 0))


def complete_results(session_id: str, result: dict[str, Any],
                     approved: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """The game's report plus every unit it left out. Returns (completed, note).

    Defences: every one seen in a snapshot, plus approved placements placed
    after the last snapshot. Missing defences were not destroyed (the game
    reports destroyed ones). Enemies: every one seen in a snapshot; missing
    ones count as killed only if a snapshot saw them dead.
    """
    match = _SESSIONS.get(str(session_id or "").strip()) or {"defences": {}, "enemies": {}}
    completed = dict(result)
    defences = [dict(d) for d in result.get("defences") or [] if isinstance(d, dict)]
    enemies = [dict(e) for e in result.get("enemies") or [] if isinstance(e, dict)]
    reported_def = {str(d.get("id")) for d in defences}
    reported_cells = {_cell(d.get("position")) for d in defences}
    reported_enemies = {str(e.get("id")) for e in enemies}

    added_def = 0
    for uid, unit in match["defences"].items():
        if uid in reported_def:
            continue
        cell = unit["cell"] or (None, None)
        defences.append({"id": uid, "type": unit["type"], "position": {"x": cell[0], "y": cell[1]},
                         "damageDealt": 0.0, "destroyed": False})
        reported_cells.add(unit["cell"])
        added_def += 1
    for placement in approved or []:
        cell = _cell(placement.get("defenceGridPosition"))
        if cell is None or cell in reported_cells:
            continue
        kind = placement.get("defenceId")
        defences.append({"id": f"placed_{cell[0]}_{cell[1]}", "type": DEFENCE_TYPES.get(kind, str(kind)),
                         "position": {"x": cell[0], "y": cell[1]}, "damageDealt": 0.0, "destroyed": False})
        reported_cells.add(cell)
        added_def += 1

    added_enemies = 0
    for uid, unit in match["enemies"].items():
        if uid in reported_enemies:
            continue
        enemies.append({"id": uid, "type": unit["type"], "damageDealt": 0.0, "killed": bool(unit["dead"])})
        added_enemies += 1

    completed["defences"] = defences
    completed["enemies"] = enemies
    completed["defencesDestroyed"] = sum(1 for d in defences if d.get("destroyed"))
    completed["enemiesKilled"] = max(int(result.get("enemiesKilled") or 0),
                                     sum(1 for e in enemies if e.get("killed")))
    # Type stats were built from the incomplete lists; the scorer falls back to the lists.
    completed.pop("defenceTypeStats", None)
    completed.pop("enemyTypeStats", None)
    note = {
        "reportedDefences": len(result.get("defences") or []),
        "reportedEnemies": len(result.get("enemies") or []),
        "addedDefences": added_def,
        "addedEnemies": added_enemies,
    }
    if added_def or added_enemies:
        logger.info("Completed the game's report for session %s: +%d defences, +%d enemies seen in snapshots",
                    session_id, added_def, added_enemies)
    return completed, note


def forget(session_id: str) -> None:
    _SESSIONS.pop(str(session_id or "").strip(), None)
