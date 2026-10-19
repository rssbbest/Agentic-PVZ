"""
app/routers/leaderboards.py
───────────────────────────
Public (live, public test cases) and private (hidden test cases, eval runs)
leaderboards; how they are computed is in app/services/leaderboards.py.

Anyone (under /api, so the public site forwards it):
    GET /api/leaderboard/public           the live board
    GET /api/leaderboard/private          the final board once published, else {"published": false}
    GET /api/leaderboard/stream?board=…   Server-Sent Events: the board again whenever it changes
Organisers (X-Admin-Secret):
    GET /admin/api/leaderboard            both boards (private even unpublished) and the settings
    PUT /admin/api/leaderboard/settings   {evalOpen, privatePublished, privateRule: latest|best}
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.security import verify_admin
from app.services import competition, leaderboards

router = APIRouter(prefix="/api/leaderboard", tags=["Leaderboard"])
admin = APIRouter(prefix="/admin/api/leaderboard", tags=["Admin"], dependencies=[Depends(verify_admin)])
KEEPALIVE_S = 20.0


def _view(name: str, data: dict) -> dict:
    """What anyone may see: the private rows only once published."""
    if name == "private" and not data.get("published"):
        return {"board": "private", "published": False, "hiddenCases": data.get("hiddenCases"), "rows": []}
    return data


@router.get("/public")
async def public_board():
    return await leaderboards.board("public")


@router.get("/private")
async def private_board():
    return _view("private", await leaderboards.board("private"))


@router.get("/stream")
async def stream(board: str = Query("public", pattern="^(public|private)$")):
    async def events():
        sent = None
        while True:
            since = leaderboards.version()
            data = _view(board, await leaderboards.board(board))
            key = json.dumps({k: v for k, v in data.items() if k != "updatedAt"}, sort_keys=True, default=str)
            if key != sent:
                sent = key
                yield f"event: board\ndata: {json.dumps(data, default=str)}\n\n"
            else:
                yield ": keep-alive\n\n"
            await leaderboards.wait_for_change(since, KEEPALIVE_S)
            await asyncio.sleep(leaderboards.MIN_REBUILD_S)  # one update for a burst of games

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class SettingsIn(BaseModel):
    evalOpen: bool | None = None
    privatePublished: bool | None = None
    privateRule: str | None = None


@admin.get("")
async def admin_boards(db: AsyncSession = Depends(get_db)):
    return {"settings": await leaderboards.get_settings(db),
            "public": await leaderboards.board("public"),
            "private": await leaderboards.board("private")}


@admin.put("/settings")
async def admin_settings(body: SettingsIn, db: AsyncSession = Depends(get_db)):
    if body.privateRule is not None and body.privateRule not in leaderboards.RULES:
        raise HTTPException(422, f"privateRule must be one of {list(leaderboards.RULES)}")
    settings = {**await leaderboards.get_settings(db), **body.model_dump(exclude_none=True)}
    await competition.put_setting(db, leaderboards.SETTINGS_KEY, settings)
    leaderboards.changed(force=True)
    return settings
