"""
app/schemas.py  –  Pydantic request / response schemas
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_serializer

from app.models import RunStatus, SubmissionStatus


# ── Helpers ───────────────────────────────────────────────────────────────────
class GridPosition(BaseModel):
    x: int = Field(..., ge=0, le=10, description="Column (0-10)")
    y: int = Field(..., ge=0, le=5, description="Row (0-5)")


class DefencePlacement(BaseModel):
    defenceId: int = Field(..., ge=0, description="Index into prefab list")
    defenceGridPosition: GridPosition


class EnemyEntry(BaseModel):
    enemyId: int = Field(..., ge=0, description="Index into prefab list")
    count:   int = Field(..., ge=0, description="Enemy count")
    allowedRows: list[int] | None = Field(default=None, description="Rows this enemy type may spawn in (0-5)")


# ── SDK shapes ────────────────────────────────────────────────────────────────
class Violation(BaseModel):
    type:    str
    message: str


class SDKStatus(BaseModel):
    valid:      bool
    violations: list[Violation] = []


class SDKResponse(BaseModel):
    sdkStatus:           SDKStatus
    defencePlacementData: list[DefencePlacement] | None = None


# ── Request: submit an agent ──────────────────────────────────────────────────
# (agent.py comes as multipart/form-data file upload;
#  team_id comes as a form field; enemies are never supplied by the caller)


# ── Response schemas ──────────────────────────────────────────────────────────
class TeamOut(BaseModel):
    id:         int
    name:       str
    created_at: datetime

    model_config = {"from_attributes": True}


class RunSummary(BaseModel):
    id:              int
    enemy_config_id: int
    status:          RunStatus
    score:           float | None

    model_config = {"from_attributes": True}


class SubmissionOut(BaseModel):
    id:          int
    team_id:     int
    status:      SubmissionStatus
    total_score: float | None
    created_at:  datetime
    runs:        list[RunSummary]

    model_config = {"from_attributes": True}


class EnemyConfigCreate(BaseModel):
    name:    str = Field(..., min_length=1, max_length=120)
    enemies: list[EnemyEntry] = Field(..., min_length=1)
    budget:  int | None = Field(None, ge=0, description="Optional target cost budget for this test case")

    @field_validator("enemies")
    @classmethod
    def _unique_enemy_ids(cls, v: list[EnemyEntry]) -> list[EnemyEntry]:
        ids = [e.enemyId for e in v]
        if len(ids) != len(set(ids)):
            raise ValueError("enemyId must be unique within a config")
        return v

class EnemyConfigOut(BaseModel):
    id:      int
    name:    str
    enemies: list[EnemyEntry]
    budget:  int | None = None

    model_config = {"from_attributes": True}


class RunOut(BaseModel):
    id:                 int
    submission_id:      int
    enemy_config_id:    int
    status:             RunStatus
    sdk_status:         dict[str, Any] | None
    defence_placement:  list[dict[str, Any]] | None
    evaluator_output:   dict[str, Any] | None
    score:              float | None
    created_at:         datetime

    model_config = {"from_attributes": True}


# ── POST /runs/{id}/result  –  Simulator → Backend ───────────────────────────
class SimulatorResult(BaseModel):
    """
    Payload the simulator POSTs when the game round is finished.
    The exact shape is TBD by the simulator team; the backend stores
    whatever arrives verbatim in evaluator_output after the evaluator
    processes it.
    """
    result: dict[str, Any] = Field(..., description="Raw simulator result payload")


# ── GET /leaderboard ─────────────────────────────────────────────────────────
class LeaderboardEntry(BaseModel):
    rank:          int
    team_id:       int
    team_name:     str
    submission_id: int
    score:         float          # submission total_score (average across all enemy configs)
    created_at:    datetime


# ── WebSocket messages ────────────────────────────────────────────────────────
class WsEvent(BaseModel):
    event: str                          # e.g. "status_update", "result"
    data:  dict[str, Any] = {}


# ── Team creation ─────────────────────────────────────────────────────────────
class TeamCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


# ── /api/team  –  Unity client → Backend ─────────────────────────────────────
class SpawnSettings(BaseModel):
    interArrivalTime: float = Field(6.0, description="Wait between enemy spawns, before decay")
    timeDecreaseRate: float = Field(0.25, description="Subtracted from the wait after each spawn")
    minInterArrivalTime: float = Field(0.0, description="Floor on the wait, so spawns don't get arbitrarily fast")
    spawnSequence: list[int] | None = Field(
        None, description="Fixed spawn order of enemyIds (build7 reads it); missing/null/[] = random order")
    seed: int | None = Field(
        None, description="Spawn seed (build11): the same rows and order in every game of the level")

    @model_serializer(mode="wrap")
    def _drop_unset_sequence(self, handler):
        data = handler(self)
        if not data.get("spawnSequence"):
            data.pop("spawnSequence", None)  # missing = random order, as before
        if data.get("seed") is None:
            data.pop("seed", None)
        return data


class GameSettings(BaseModel):
    reinforcementsEnabled: bool = Field(True, description="Turns the mid-run reinforcement feature on/off")
    reinforcementIntervalSeconds: float = Field(3.0, description="Seconds (in-game time) between reinforcement pings")
    reinforcementHoldSeconds: float = Field(
        0.2, description="Minimum time the game stays paused at a ping; it always waits for the reply (build7)")
    sendScreenshotToo: bool = Field(False, description="If true, each reinforcement ping uploads screenshot (kept false)")


class TeamDataOut(BaseModel):
    """Response from POST /api/team."""
    sessionID: str | None = None
    defencePlacementData: list[DefencePlacement] = []
    enemies: list[EnemyEntry] = []
    spawnSettings: SpawnSettings = Field(default_factory=SpawnSettings)
    gameSettings: GameSettings = Field(default_factory=GameSettings)
    budget: float | None = None
    unitCosts: dict[str, int] | None = Field(None, description="Cost per defenceId, e.g. {\"1\": 200}")
    matchBudget: float | None = None
    bountyPerKill: float | None = Field(None, description="Unlocked budget earned per enemy killed")
    coins: int | None = Field(None, description="Opening budget left after the starting defences (game coin counter)")


class TeamDataUpsert(BaseModel):
    """Create or replace the defencePlacementData + enemies for a team."""
    defencePlacementData: list[DefencePlacement] = Field(
        default_factory=list, description="Starting defences; empty for an empty board"
    )
    enemies: list[EnemyEntry] = Field(..., min_length=1)
    spawnSettings: SpawnSettings | None = None
    gameSettings: GameSettings | None = None


# ── /api/team errors & requests ──────────────────────────────────────────────
class ApiTeamRequest(BaseModel):
    """Body of POST /api/team."""
    teamID: str = Field(..., min_length=1)
    configId: int | None = None
    testCaseId: int | None = None
    # Added by the SDK (the game never sends them): which test case to play,
    # or which slot of an eval run, and the agent.py that will play it.
    testCase: str | None = None
    evalId: int | None = None
    evalSlot: int | None = None
    agentSha256: str | None = None


class ApiTeamError(BaseModel):
    """Error shape expected by Unity client: {"error": "..."}"""
    error: str


# ── /api/reinforcements  –  Unity mid-run snapshot & response ────────────────
class ReinforcementCounts(BaseModel):
    activeDefenceCount: int = Field(..., ge=0)
    activeEnemyCount: int = Field(..., ge=0)
    enemiesQueued: int = Field(..., ge=0)
    totalEnemiesInWave: int = Field(..., ge=0)


class UnitPosition(BaseModel):
    x: int
    y: int

    @field_validator("x", "y", mode="before")
    @classmethod
    def _nearest_cell(cls, v: Any) -> Any:
        # Units moving between cells are reported at fractional positions (6.4).
        return round(v) if isinstance(v, float) else v


class ReinforcementDefenceUnit(BaseModel):
    id: str
    type: str
    health: float
    maxHealth: float
    position: UnitPosition


class ReinforcementEnemyUnit(BaseModel):
    id: str
    type: str
    health: float
    maxHealth: float
    position: UnitPosition
    isAttacking: bool = False
    isDead: bool = False


class ReinforcementRequest(BaseModel):
    teamID: str
    sessionID: str | None = None
    elapsedSeconds: float = 0.0
    counts: ReinforcementCounts
    defences: list[ReinforcementDefenceUnit] = []
    enemies: list[ReinforcementEnemyUnit] = []
    candidatePlacements: list[DefencePlacement] = []
    budget: float | None = None
    spent: float | None = None
    remainingBudget: float | None = None
    matchBudget: float | None = None
    unlockedBudget: float | None = None

    model_config = {"extra": "allow"}


class BudgetStatus(BaseModel):
    """The team's money at this point of the match (all in the same currency as unit costs)."""
    unlocked: float = Field(..., description="Spendable so far: opening budget plus kill bounties, capped at total")
    spent: float = Field(..., description="Cost of every placement approved this match")
    remaining: float = Field(..., description="unlocked - spent")
    total: float = Field(..., description="The match budget")


class RejectedPlacement(BaseModel):
    defenceId: Any = None
    defenceGridPosition: Any = None
    reason: str


class ReinforcementResponse(BaseModel):
    placeDefences: bool = False
    defencePlacementData: list[DefencePlacement] | None = None
    # Extra fields for the SDK/agent; the game ignores fields it does not know.
    budget: BudgetStatus | None = None
    rejected: list[RejectedPlacement] | None = None
    # The budget left, in whole units, for the game's coin counter (build7+).
    coins: int | None = None


# ── Unity End-of-Run Match Result schemas ────────────────────────────────────
class DefenceUnitLog(BaseModel):
    id: str
    type: str
    position: dict[str, Any] | UnitPosition | GridPosition
    cost: int | float = 0
    damageDealt: float = 0.0
    destroyed: bool = False
    destroyedAtTime: float = -1.0


class EnemyUnitLog(BaseModel):
    id: str
    type: str
    damageDealt: float = 0.0
    killed: bool = False
    killedAtTime: float = -1.0


class DefenceTypeStat(BaseModel):
    type: str
    unitCount: int = 0
    totalDamageDealt: float = 0.0
    unitsDestroyed: int = 0
    costPerUnit: int | float = 0
    totalCost: int | float = 0


class EnemyTypeStat(BaseModel):
    type: str
    unitCount: int = 0
    totalDamageDealt: float = 0.0
    unitsKilled: int = 0


class UnityMatchResult(BaseModel):
    teamID: str
    sessionID: str = "Not Implemented Yet"
    success: bool = False
    durationSeconds: float
    enemiesKilled: int = 0
    defencesDestroyed: int = 0
    totalDefenceDamage: float = 0.0
    totalEnemyDamage: float = 0.0
    totalDefenceCost: int | float | None = None
    totalCost: int | float | None = None
    defences: list[DefenceUnitLog] = []
    enemies: list[EnemyUnitLog] = []
    defenceTypeStats: list[DefenceTypeStat] = []
    enemyTypeStats: list[EnemyTypeStat] = []

