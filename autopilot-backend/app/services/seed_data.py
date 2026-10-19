"""
app/services/seed_data.py
─────────────────────────
Seeds default test teams and team_data on application startup
so Unity simulator can immediately connect and test out of the box.
"""
from __future__ import annotations

import logging
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Team, TeamData
from app.schemas import GameSettings, SpawnSettings

logger = logging.getLogger(__name__)

# Default starting defenses within the 11x6 grid
# Levels start with an empty board; every defence comes from the agent's
# reinforcements.
DEFAULT_DEFENCES: list[dict] = []

# Default starting enemy wave
DEFAULT_ENEMIES = [
    {"enemyId": 0, "count": 6, "allowedRows": [1, 2, 3]},
    {"enemyId": 1, "count": 4, "allowedRows": [0, 1, 2, 3, 4]},
]

# Teams seeded for development and testing
DEFAULT_TEST_TEAMS = ["TEAM1", "team1"]


def build_default_team_data(team_id: int) -> TeamData:
    """Construct a default TeamData record with tournament baseline settings."""
    spawn_settings = SpawnSettings(
        interArrivalTime=6.0,
        timeDecreaseRate=0.25,
        minInterArrivalTime=0.0,
    ).model_dump()

    game_settings = GameSettings(
        reinforcementsEnabled=True,
        reinforcementIntervalSeconds=3.0,
        reinforcementHoldSeconds=0.2,
        sendScreenshotToo=False,
    ).model_dump()

    return TeamData(
        team_id=team_id,
        defence_placement_data=DEFAULT_DEFENCES,
        enemies=DEFAULT_ENEMIES,
        spawn_settings=spawn_settings,
        game_settings=game_settings,
    )


async def seed_default_teams(db: AsyncSession) -> None:
    """
    Ensure default test teams exist with initial level data and reinforcements enabled.
    Also ensures EVERY team currently in the database has a TeamData record.
    Synchronizes PostgreSQL serial sequences first to prevent 'teams_pkey' duplicate key errors.
    """
    try:
        # 1. Synchronize PostgreSQL sequence counter with existing max(id)
        try:
            await db.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('teams', 'id'), COALESCE(max(id), 1)) FROM teams;"
                )
            )
            await db.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('team_data', 'id'), COALESCE(max(id), 1)) FROM team_data;"
                )
            )
            # Ensure new columns exist on legacy databases
            await db.execute(text("ALTER TABLE team_data ADD COLUMN IF NOT EXISTS spawn_settings JSONB;"))
            await db.execute(text("ALTER TABLE team_data ADD COLUMN IF NOT EXISTS game_settings JSONB;"))
            await db.commit()
        except Exception as seq_err:
            logger.debug("Could not resync sequence or alter columns: %s", seq_err)
            await db.rollback()

        created_count = 0

        # 2. Ensure baseline test teams exist
        for team_name in DEFAULT_TEST_TEAMS:
            existing_team = (
                await db.execute(
                    select(Team).where(func.lower(Team.name) == team_name.lower()).order_by(Team.id)
                )
            ).scalars().first()

            if existing_team is None:
                team = Team(name=team_name)
                db.add(team)
                await db.flush()
                await db.refresh(team)

        # 3. Ensure EVERY team in the database has an associated TeamData record
        all_teams = (await db.execute(select(Team).order_by(Team.id))).scalars().all()
        for t in all_teams:
            existing_data = (
                await db.execute(
                    select(TeamData).where(TeamData.team_id == t.id).order_by(TeamData.id)
                )
            ).scalars().first()

            if existing_data is None:
                td = build_default_team_data(t.id)
                db.add(td)
                created_count += 1
            else:
                updated = False
                if existing_data.spawn_settings is None:
                    existing_data.spawn_settings = SpawnSettings().model_dump()
                    updated = True
                if existing_data.game_settings is None:
                    existing_data.game_settings = GameSettings().model_dump()
                    updated = True
                if updated:
                    db.add(existing_data)
                    created_count += 1

        if created_count > 0:
            await db.commit()
            logger.info("Auto-seeded/updated %d default team data row(s) for Unity simulator", created_count)
    except Exception as exc:
        logger.warning("Auto-seed routine encountered an issue, skipping seed: %s", exc)
        await db.rollback()
