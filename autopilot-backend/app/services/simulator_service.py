"""
app/services/simulator_service.py
──────────────────────────────────
Sends the enemy config + defence placement to the simulator.

The simulator is asynchronous: it receives the data, starts the game,
renders it in real time, and later POSTs the result back to
POST /runs/{id}/result.

This module only fires the "start" call; it does NOT wait for completion.
"""
from __future__ import annotations

import httpx

from app.config import settings


async def start_simulation(
    run_id: int,
    enemies: list[dict],
    defence_placement: list[dict],
    team_id: str | None = None,
) -> None:
    """
    POST start payload to the simulator.

    Simulator request body
    ----------------------
    {
        "runId": 42,
        "teamId": "3",
        "enemies": [{"enemyId": 0, "count": 5}, ...],
        "defencePlacementData": [{"defenceId": 0, "defenceGridPosition": {"x":0,"y":0}}, ...]
    }

    The simulator will POST its result to:
        POST <backend>/runs/{runId}/result
    """
    payload = {
        "runId": run_id,
        "teamId": team_id,
        "enemies": enemies,
        "defencePlacementData": defence_placement,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{settings.SIMULATOR_URL}/start", json=payload)
        resp.raise_for_status()
