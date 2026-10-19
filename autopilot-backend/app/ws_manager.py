"""
app/ws_manager.py  –  WebSocket connection manager

Keeps a registry of active connections per run_id so the backend can
broadcast status updates to the simulator / frontend in real time.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict

from fastapi import WebSocket
from starlette.websockets import WebSocketState


class ConnectionManager:
    def __init__(self) -> None:
        # run_id → list of active WebSocket connections
        self._connections: dict[int, list[WebSocket]] = defaultdict(list)
        self._lock = asyncio.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    async def connect(self, run_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[run_id].append(websocket)

    async def disconnect(self, run_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            conns = self._connections.get(run_id, [])
            if websocket in conns:
                conns.remove(websocket)
            if not conns:
                self._connections.pop(run_id, None)

    # ── Broadcasting ──────────────────────────────────────────────────────────
    async def broadcast(self, run_id: int, event: str, data: dict) -> None:
        """Send a JSON event to all clients watching a specific run."""
        payload = json.dumps({"event": event, "data": data})
        dead: list[WebSocket] = []

        async with self._lock:
            conns = list(self._connections.get(run_id, []))

        for ws in conns:
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_text(payload)
                else:
                    dead.append(ws)
            except Exception:
                dead.append(ws)

        # Prune dead connections
        for ws in dead:
            await self.disconnect(run_id, ws)

    async def send_status(self, run_id: int, status: str) -> None:
        await self.broadcast(run_id, "status_update", {"run_id": run_id, "status": status})

    async def send_result(self, run_id: int, score: float | None, evaluator_output: dict) -> None:
        await self.broadcast(
            run_id,
            "result",
            {"run_id": run_id, "score": score, "evaluator_output": evaluator_output},
        )

    async def send_error(self, run_id: int, message: str) -> None:
        await self.broadcast(run_id, "error", {"run_id": run_id, "message": message})


# Singleton shared across the app
manager = ConnectionManager()
