"""
app/routers/websocket.py
─────────────────────────
WS /runs/{id}/stream

Live-streams run status events to the simulator / frontend.

Message format (JSON text frame)
---------------------------------
{
    "event": "status_update" | "result" | "error",
    "data": { ... }
}

Events
------
status_update   Sent when run status changes (pending→simulating, etc.)
result          Sent when run completes: {score, evaluator_output}
error           Sent on any pipeline error: {message}

On connect the client first receives the run's CURRENT state (a status_update,
plus the result if the run already completed), so connecting late loses nothing.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.database import AsyncSessionLocal
from app.models import Run, RunStatus
from app.ws_manager import manager

router = APIRouter(tags=["WebSocket"])


@router.websocket("/runs/{run_id}/stream")
async def run_stream(run_id: int, websocket: WebSocket):
    # Register first so no broadcast can slip through between lookup and subscribe.
    await manager.connect(run_id, websocket)

    # Short-lived session: it is closed BEFORE the long-lived loop below, so a
    # connected viewer never pins a database connection.
    async with AsyncSessionLocal() as db:
        run = await db.get(Run, run_id)
        snapshot = (
            None
            if run is None
            else (run.status, float(run.score) if run.score is not None else None, run.evaluator_output)
        )

    if snapshot is None:
        await manager.disconnect(run_id, websocket)
        await websocket.close(code=4404, reason=f"Run {run_id} not found")
        return

    status, score, evaluator_output = snapshot
    # Replay to THIS client only (not a broadcast to every watcher).
    await websocket.send_text(
        json.dumps({"event": "status_update", "data": {"run_id": run_id, "status": status.value}})
    )
    if status == RunStatus.completed:
        await websocket.send_text(
            json.dumps(
                {
                    "event": "result",
                    "data": {"run_id": run_id, "score": score, "evaluator_output": evaluator_output},
                }
            )
        )

    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"event":"pong","data":{}}')
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(run_id, websocket)
