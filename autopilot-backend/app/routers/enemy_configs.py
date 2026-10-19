"""
app/routers/enemy_configs.py
────────────────────────────
GET  /enemy_configs          – list all enemy configs
GET  /enemy_configs/{id}     – fetch one enemy config
POST /enemy_configs          – create a new enemy config   (admin only)

Enemy data is owned entirely by the backend; participants never supply it.
Every enemy config in this table is part of the event's test suite: each
submission is run against ALL of them and the scores are averaged.

On startup the backend seeds 50 test configs (4 zombie types) if the table is
empty. Adding a config after submissions exist changes the suite, so earlier
submissions are no longer scored on the same set. Do that before the event.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import EnemyConfig
from app.schemas import EnemyConfigCreate, EnemyConfigOut
from app.security import verify_admin

router = APIRouter(prefix="/enemy_configs", tags=["EnemyConfigs"])


# ── 50 test-case enemy configs (4 zombie types: enemyId 0-3) ────────────────
SEED_CONFIGS = [
    {"name": "wave_1", "enemies": [{"enemyId": 0, "count": 2}, {"enemyId": 1, "count": 1}, {"enemyId": 2, "count": 5}, {"enemyId": 3, "count": 4}]},
    {"name": "wave_2", "enemies": [{"enemyId": 0, "count": 4}, {"enemyId": 1, "count": 3}, {"enemyId": 2, "count": 2}]},
    {"name": "wave_3", "enemies": [{"enemyId": 0, "count": 9}, {"enemyId": 1, "count": 2}, {"enemyId": 3, "count": 10}]},
    {"name": "wave_4", "enemies": [{"enemyId": 0, "count": 7}, {"enemyId": 2, "count": 1}, {"enemyId": 3, "count": 1}]},
    {"name": "wave_5", "enemies": [{"enemyId": 1, "count": 2}, {"enemyId": 2, "count": 4}, {"enemyId": 3, "count": 4}]},
    {"name": "wave_6", "enemies": [{"enemyId": 0, "count": 9}, {"enemyId": 1, "count": 10}]},
    {"name": "wave_7", "enemies": [{"enemyId": 0, "count": 1}, {"enemyId": 2, "count": 9}]},
    {"name": "wave_8", "enemies": [{"enemyId": 0, "count": 4}, {"enemyId": 3, "count": 9}]},
    {"name": "wave_9", "enemies": [{"enemyId": 1, "count": 7}, {"enemyId": 2, "count": 4}]},
    {"name": "wave_10", "enemies": [{"enemyId": 1, "count": 8}, {"enemyId": 3, "count": 10}]},
    {"name": "wave_11", "enemies": [{"enemyId": 2, "count": 5}, {"enemyId": 3, "count": 1}]},
    {"name": "wave_12", "enemies": [{"enemyId": 0, "count": 3}]},
    {"name": "wave_13", "enemies": [{"enemyId": 1, "count": 7}]},
    {"name": "wave_14", "enemies": [{"enemyId": 2, "count": 6}]},
    {"name": "wave_15", "enemies": [{"enemyId": 3, "count": 5}]},
    {"name": "wave_16", "enemies": [{"enemyId": 0, "count": 3}, {"enemyId": 1, "count": 4}, {"enemyId": 2, "count": 6}, {"enemyId": 3, "count": 2}]},
    {"name": "wave_17", "enemies": [{"enemyId": 0, "count": 2}, {"enemyId": 2, "count": 7}, {"enemyId": 1, "count": 2}, {"enemyId": 3, "count": 6}]},
    {"name": "wave_18", "enemies": [{"enemyId": 0, "count": 6}, {"enemyId": 1, "count": 10}, {"enemyId": 2, "count": 5}, {"enemyId": 3, "count": 1}]},
    {"name": "wave_19", "enemies": [{"enemyId": 0, "count": 8}, {"enemyId": 1, "count": 9}, {"enemyId": 2, "count": 2}]},
    {"name": "wave_20", "enemies": [{"enemyId": 0, "count": 7}, {"enemyId": 1, "count": 2}, {"enemyId": 3, "count": 9}]},
    {"name": "wave_21", "enemies": [{"enemyId": 0, "count": 5}, {"enemyId": 2, "count": 10}, {"enemyId": 3, "count": 6}]},
    {"name": "wave_22", "enemies": [{"enemyId": 1, "count": 10}, {"enemyId": 2, "count": 4}, {"enemyId": 3, "count": 2}]},
    {"name": "wave_23", "enemies": [{"enemyId": 0, "count": 1}, {"enemyId": 1, "count": 4}]},
    {"name": "wave_24", "enemies": [{"enemyId": 0, "count": 5}, {"enemyId": 2, "count": 2}]},
    {"name": "wave_25", "enemies": [{"enemyId": 0, "count": 4}, {"enemyId": 3, "count": 2}]},
    {"name": "wave_26", "enemies": [{"enemyId": 1, "count": 7}, {"enemyId": 2, "count": 5}]},
    {"name": "wave_27", "enemies": [{"enemyId": 1, "count": 8}, {"enemyId": 3, "count": 6}]},
    {"name": "wave_28", "enemies": [{"enemyId": 2, "count": 3}, {"enemyId": 3, "count": 6}]},
    {"name": "wave_29", "enemies": [{"enemyId": 0, "count": 6}]},
    {"name": "wave_30", "enemies": [{"enemyId": 1, "count": 4}]},
    {"name": "wave_31", "enemies": [{"enemyId": 2, "count": 5}]},
    {"name": "wave_32", "enemies": [{"enemyId": 3, "count": 2}]},
    {"name": "wave_33", "enemies": [{"enemyId": 0, "count": 10}, {"enemyId": 1, "count": 3}, {"enemyId": 2, "count": 9}, {"enemyId": 3, "count": 4}]},
    {"name": "wave_34", "enemies": [{"enemyId": 0, "count": 3}, {"enemyId": 2, "count": 8}, {"enemyId": 1, "count": 7}, {"enemyId": 3, "count": 5}]},
    {"name": "wave_35", "enemies": [{"enemyId": 0, "count": 9}, {"enemyId": 1, "count": 4}, {"enemyId": 2, "count": 6}, {"enemyId": 3, "count": 1}]},
    {"name": "wave_36", "enemies": [{"enemyId": 0, "count": 4}, {"enemyId": 1, "count": 1}, {"enemyId": 2, "count": 6}]},
    {"name": "wave_37", "enemies": [{"enemyId": 0, "count": 7}, {"enemyId": 1, "count": 5}, {"enemyId": 3, "count": 2}]},
    {"name": "wave_38", "enemies": [{"enemyId": 0, "count": 4}, {"enemyId": 2, "count": 10}, {"enemyId": 3, "count": 6}]},
    {"name": "wave_39", "enemies": [{"enemyId": 1, "count": 4}, {"enemyId": 2, "count": 8}, {"enemyId": 3, "count": 7}]},
    {"name": "wave_40", "enemies": [{"enemyId": 0, "count": 8}, {"enemyId": 1, "count": 3}]},
    {"name": "wave_41", "enemies": [{"enemyId": 0, "count": 5}, {"enemyId": 2, "count": 3}]},
    {"name": "wave_42", "enemies": [{"enemyId": 0, "count": 4}, {"enemyId": 3, "count": 9}]},
    {"name": "wave_43", "enemies": [{"enemyId": 1, "count": 9}, {"enemyId": 2, "count": 5}]},
    {"name": "wave_44", "enemies": [{"enemyId": 1, "count": 10}, {"enemyId": 3, "count": 7}]},
    {"name": "wave_45", "enemies": [{"enemyId": 2, "count": 10}, {"enemyId": 3, "count": 7}]},
    {"name": "wave_46", "enemies": [{"enemyId": 0, "count": 6}]},
    {"name": "wave_47", "enemies": [{"enemyId": 1, "count": 4}]},
    {"name": "wave_48", "enemies": [{"enemyId": 2, "count": 3}]},
    {"name": "wave_49", "enemies": [{"enemyId": 3, "count": 9}]},
    {"name": "wave_50", "enemies": [{"enemyId": 0, "count": 8}, {"enemyId": 1, "count": 2}, {"enemyId": 2, "count": 1}, {"enemyId": 3, "count": 2}]},
]

async def seed_enemy_configs(db: AsyncSession) -> None:
    """Seed the 50 test configs if the table is empty."""
    if (await db.execute(select(EnemyConfig.id).limit(1))).first() is not None:
        return  # already seeded

    for cfg in SEED_CONFIGS:
        db.add(EnemyConfig(name=cfg["name"], enemies=cfg["enemies"]))
    await db.commit()


# ── GET /enemy_configs ────────────────────────────────────────────────────────
@router.get("", response_model=list[EnemyConfigOut])
async def list_configs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EnemyConfig).order_by(EnemyConfig.id))
    return result.scalars().all()


# ── GET /enemy_configs/{id} ───────────────────────────────────────────────────
@router.get("/{config_id}", response_model=EnemyConfigOut)
async def get_config(config_id: int, db: AsyncSession = Depends(get_db)):
    cfg = await db.get(EnemyConfig, config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"EnemyConfig {config_id} not found")
    return cfg


# ── POST /enemy_configs ───────────────────────────────────────────────────────
@router.post("", response_model=EnemyConfigOut, status_code=201, dependencies=[Depends(verify_admin)])
async def create_config(body: EnemyConfigCreate, db: AsyncSession = Depends(get_db)):
    cfg = EnemyConfig(name=body.name, enemies=[e.model_dump() for e in body.enemies])
    db.add(cfg)
    await db.flush()
    await db.refresh(cfg)
    return cfg
