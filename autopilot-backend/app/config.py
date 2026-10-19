"""
app/config.py  –  centralised settings via pydantic-settings
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEFAULT_SECRET = "change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENVIRONMENT: str = "development"

    # Postgres async URL: postgresql+asyncpg://user:pass@host:port/dbname
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/zombie_defense"

    # Directory for storing uploaded agent.py files
    UPLOAD_DIR: Path = Path("./uploads")

    # External service base URLs
    SDK_URL: str = "http://localhost:8001"
    SIMULATOR_URL: str = "http://localhost:8002"
    EVALUATOR_URL: str = "http://localhost:8003"

    # Shared secret the simulator must send in the X-Simulator-Secret header
    # when calling POST /runs/{id}/result
    SIMULATOR_SECRET: str = INSECURE_DEFAULT_SECRET

    # Secret for organiser-only endpoints (X-Admin-Secret header):
    # POST /enemy_configs, POST /submissions/{id}/retry
    ADMIN_SECRET: str = INSECURE_DEFAULT_SECRET

    # How many agent runs (SDK call + simulator start) are in flight at once.
    # Every submission is run against ALL enemy configs, so this throttles the
    # fan-out and protects the SDK / simulator services.
    MAX_CONCURRENT_RUNS: int = 4

    # Toggle to pause/enable automatic headless simulation dispatch
    ENABLE_HEADLESS_SIMULATOR: bool = False

    # Competition accounts. Signs the login tokens the SDK sends with every game
    # (defaults to a key derived from ADMIN_SECRET). With REQUIRE_LOGIN, `play`
    # needs a logged-in team too; `eval` always does.
    TEAM_TOKEN_SECRET: str = ""
    REQUIRE_LOGIN: bool = False
    # Same secret as the LLM gateway's: the login reply then carries the
    # team's own LLM token, so participants never copy one by hand.
    AUTOPILOT_GATEWAY_TEAM_SECRET: str = ""


settings = Settings()
