"""
app/services/sdk_service.py
──────────────────────────
Calls the external SDK server to:
  1. Run the participant's agent.py with the enemy config.
  2. Validate the output (forbidden APIs, latency, placement rules …).
  3. Return an SDKResponse with sdkStatus + defencePlacementData.

The agent file is sent as base64-encoded contents (not a path) so the SDK
service can run in a separate container with no shared volume.
"""
from __future__ import annotations

import base64

import httpx

from app.config import settings
from app.schemas import SDKResponse


async def run_agent(agent_file_content: bytes, enemies: list[dict]) -> SDKResponse:
    """
    POST the agent file + enemy config to the SDK service.

    SDK request body
    ----------------
    {
        "agentFileContent": "<base64-encoded agent.py>",
        "enemies": [{"enemyId": 0, "count": 5}, ...]
    }

    SDK response (valid)
    --------------------
    {
        "sdkStatus": {"valid": true, "violations": []},
        "defencePlacementData": [{"defenceId": 0, "defenceGridPosition": {"x":0,"y":0}}, ...]
    }

    SDK response (invalid)
    ----------------------
    {
        "sdkStatus": {
            "valid": false,
            "violations": [{"type": "forbidden_api_usage", "message": "..."}]
        }
    }
    """
    payload = {
        "agentFileContent": base64.b64encode(agent_file_content).decode("ascii"),
        "enemies": enemies,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(f"{settings.SDK_URL}/run", json=payload)
        resp.raise_for_status()
        return SDKResponse.model_validate(resp.json())


async def query_reinforcements(team_id: str, snapshot: dict) -> list[dict]:
    """
    Query the external SDK runner / participant agent for mid-run reinforcements.
    Timeout is set to 4.5s so it completes safely within the 5.0s freeze window.
    Returns raw candidate placement dicts, or empty list on failure / timeout.
    """
    try:
        async with httpx.AsyncClient(timeout=4.5) as client:
            resp = await client.post(
                f"{settings.SDK_URL}/reinforcements",
                json={"teamID": team_id, "snapshot": snapshot},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    return data.get("defencePlacementData", [])
                elif isinstance(data, list):
                    return data
    except Exception:
        # SDK offline, timed out, or unconfigured - never crash
        pass
    return []
