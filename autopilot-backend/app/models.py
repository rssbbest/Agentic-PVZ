"""
app/models.py  –  SQLAlchemy ORM models (mirrors migrations/init.sql)
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ── Enum ─────────────────────────────────────────────────────────────────────
class RunStatus(str, enum.Enum):
    pending    = "pending"
    invalid    = "invalid"
    simulating = "simulating"
    completed  = "completed"
    failed     = "failed"


class SubmissionStatus(str, enum.Enum):
    pending   = "pending"     # runs still in flight
    completed = "completed"   # every run finished; total_score is set
    failed    = "failed"      # >=1 run hit an infrastructure error; needs retry


# ── Team ─────────────────────────────────────────────────────────────────────
class Team(Base):
    __tablename__ = "teams"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True)
    name:       Mapped[str]      = mapped_column(String(120), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Competition accounts (app/services/accounts.py). A team logs in with its
    # 4-digit ID and password once approved; a banned team cannot play at all.
    password_hash: Mapped[str | None]  = mapped_column(String(200), nullable=True)
    approved:      Mapped[bool]        = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    banned:        Mapped[bool]        = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Registration answers ({question id: answer}) and organiser notes
    profile:       Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    submissions: Mapped[list["Submission"]] = relationship(back_populates="team")
    team_data:   Mapped[list["TeamData"]]   = relationship(back_populates="team")


# ── Submission ────────────────────────────────────────────────────────────────
class Submission(Base):
    __tablename__ = "submissions"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True)
    team_id:    Mapped[int]      = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    file_path:  Mapped[str]      = mapped_column(Text, nullable=False)

    # A submission is run against every enemy config (one Run each).
    # total_score = average of the per-config run scores (invalid runs count 0).
    status:      Mapped[SubmissionStatus] = mapped_column(
        Enum(SubmissionStatus, name="submission_status"),
        nullable=False, default=SubmissionStatus.pending,
    )
    total_score: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    team: Mapped["Team"]       = relationship(back_populates="submissions")
    runs: Mapped[list["Run"]]  = relationship(back_populates="submission", order_by="Run.id")


# ── TeamData ──────────────────────────────────────────────────────────────────
class TeamData(Base):
    """Per-team pre-configured defence placement and enemy data.

    Stored as JSONB so shapes can evolve without schema migrations.

    defence_placement_data example:
        [{"defenceId": 0, "defenceGridPosition": {"x": 0, "y": 0}}, ...]
    enemies example:
        [{"enemyId": 0, "count": 5}, ...]
    """
    __tablename__ = "team_data"

    id:      Mapped[int]        = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int]        = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    defence_placement_data: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    enemies:                Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    spawn_settings:         Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    game_settings:          Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    team: Mapped["Team"] = relationship(back_populates="team_data")


# ── EnemyConfig ───────────────────────────────────────────────────────────────
class EnemyConfig(Base):
    __tablename__ = "enemy_configs"

    id:      Mapped[int]  = mapped_column(Integer, primary_key=True)
    name:    Mapped[str]  = mapped_column(String(120), nullable=False, default="config")
    enemies: Mapped[list] = mapped_column(JSONB, nullable=False)
    budget:  Mapped[int | None] = mapped_column(Integer, nullable=True)
    # enemies format: [{"enemyId": 0, "count": 5}, ...]

    runs: Mapped[list["Run"]] = relationship(back_populates="enemy_config")


# ── Run ───────────────────────────────────────────────────────────────────────
class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("submission_id", "enemy_config_id"),)

    id:               Mapped[int]           = mapped_column(Integer, primary_key=True)
    submission_id:    Mapped[int]           = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    enemy_config_id:  Mapped[int]           = mapped_column(
        ForeignKey("enemy_configs.id"), nullable=False
    )
    status:           Mapped[RunStatus]     = mapped_column(
        Enum(RunStatus, name="run_status"), nullable=False, default=RunStatus.pending
    )

    # JSONB blobs – flexible, no extra migrations when SDK/evaluator shapes evolve
    sdk_status:         Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    defence_placement:  Mapped[list | None] = mapped_column(JSONB, nullable=True)
    evaluator_output:   Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    score:      Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    created_at: Mapped[datetime]     = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    submission:   Mapped["Submission"]  = relationship(back_populates="runs")
    enemy_config: Mapped["EnemyConfig"] = relationship(back_populates="runs")


# ── ResultsLog ────────────────────────────────────────────────────────────────
class ResultsLog(Base):
    """Stores verbatim result payloads from POST /api/results.

    data example: {"teamID": "team1", "score": 42, ...} – any JSON object.
    """
    __tablename__ = "results_log"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True)
    data:        Mapped[dict]     = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ── Competition: test cases, game sessions, eval runs, settings ──────────────
class TestCase(Base):
    """A level the SDK can ask for by code. Public ones (hidden=False) are
    listed to participants for `play`; hidden ones are only played inside an
    `eval` run and their levels are never listed."""
    __tablename__ = "test_cases"
    __test__ = False  # not a pytest class

    id:       Mapped[int]          = mapped_column(Integer, primary_key=True)
    code:     Mapped[str]          = mapped_column(String(40), unique=True, nullable=False)
    name:     Mapped[str]          = mapped_column(String(200), nullable=False, default="")
    hidden:   Mapped[bool]         = mapped_column(Boolean, nullable=False, default=False)
    active:   Mapped[bool]         = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int]          = mapped_column(Integer, nullable=False, default=0)
    # {"enemies": [...], "spawnSettings": {...}, "gameSettings": {...},
    #  "defencePlacementData": [...]} – the same shape as a team's level
    level:    Mapped[dict]         = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime]   = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EvalRun(Base):
    """One published run: every active test case of a board (public or the
    hidden ones for private), played once, with the agent.py that played them.
    Its score (the sum over the cases) is what the leaderboard shows."""
    __tablename__ = "eval_runs"

    id:            Mapped[int]          = mapped_column(Integer, primary_key=True)
    team_id:       Mapped[int]          = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    board:         Mapped[str]          = mapped_column(String(10), nullable=False, default="private")
    status:        Mapped[str]          = mapped_column(String(20), nullable=False, default="running")
    case_codes:    Mapped[list]         = mapped_column(JSONB, nullable=False)
    agent_source:  Mapped[str]          = mapped_column(Text, nullable=False, default="")
    agent_sha256:  Mapped[str]          = mapped_column(String(64), nullable=False, default="")
    sdk_version:   Mapped[str | None]   = mapped_column(String(40), nullable=True)
    score:         Mapped[float | None] = mapped_column(Float, nullable=True)
    log_path:      Mapped[str | None]   = mapped_column(Text, nullable=True)
    summary:       Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
    started_at:    Mapped[datetime]     = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GameSession(Base):
    """One game the SDK started for a test case (play or eval), with what the
    backend saw of it: the pauses, the referee's placements and the result."""
    __tablename__ = "game_sessions"

    session_id:    Mapped[str]          = mapped_column(String(40), primary_key=True)
    team_id:       Mapped[int]          = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    test_case:     Mapped[str]          = mapped_column(String(40), nullable=False)
    mode:          Mapped[str]          = mapped_column(String(10), nullable=False, default="play")
    eval_id:       Mapped[int | None]   = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=True)
    eval_slot:     Mapped[int | None]   = mapped_column(Integer, nullable=True)
    agent_sha256:  Mapped[str | None]   = mapped_column(String(64), nullable=True)
    score:         Mapped[float | None] = mapped_column(Float, nullable=True)
    success:       Mapped[bool | None]  = mapped_column(Boolean, nullable=True)
    pauses:        Mapped[int]          = mapped_column(Integer, nullable=False, default=0)
    placements:    Mapped[list | None]  = mapped_column(JSONB, nullable=True)
    result:        Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
    evaluator_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at:    Mapped[datetime]     = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CompetitionSetting(Base):
    """Small organiser-edited documents, e.g. the registration form."""
    __tablename__ = "competition_settings"

    key:   Mapped[str]  = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
