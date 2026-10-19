"""Shared data schemas — draft per design doc §13.

Status: implementable NOW as written, but subject to freeze by
Backend / Game-Engine teams. Field names/types must be confirmed
against engine truth before event.
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Position(BaseModel):
    x: int
    y: int


class ZombieState(BaseModel):
    id: str
    type: str  # "walker" | "runner" | "brute" | "boss" | ...
    position: Position
    health: int
    max_health: int
    speed: float
    target: Optional[Position] = None


class DefenseState(BaseModel):
    id: str
    type: str  # "wall" | "turret" | "mine" | "spike" | ...
    position: Position
    health: int
    ammo: Optional[int] = None
    cooldown_remaining: int = 0


class ResourceState(BaseModel):
    currency: int
    energy: int


class MapInfo(BaseModel):
    width: int
    height: int
    obstacles: List[Position] = []
    spawn_points: List[Position] = []
    base_position: Position


class TurnState(BaseModel):
    match_id: str
    turn_number: int
    max_turns: int
    time_limit_seconds: float
    map: MapInfo
    zombies: List[ZombieState] = []
    defenses: List[DefenseState] = []
    resources: ResourceState
    score_so_far: float = 0.0
    events: List[str] = []


class ActionType(str, Enum):
    PLACE_DEFENSE = "place_defense"
    UPGRADE_DEFENSE = "upgrade_defense"
    REMOVE_DEFENSE = "remove_defense"
    REPAIR_DEFENSE = "repair_defense"
    NO_OP = "no_op"


class Action(BaseModel):
    type: ActionType
    defense_type: Optional[str] = None
    position: Optional[Position] = None
    target_id: Optional[str] = None


class TurnDecision(BaseModel):
    actions: List[Action] = []
    metadata: Dict[str, Any] = {}


class MatchBootstrap(BaseModel):
    match_id: str
    seed: int
    map: MapInfo
    max_turns: int
    time_limit_seconds: float
    starting_resources: ResourceState
    rules: Dict[str, Any] = {}


class MatchResult(BaseModel):
    match_id: str
    outcome: str  # "won" | "lost" | "timed_out"
    final_score: float
    turns_survived: int
    breakdown: Dict[str, Any] = {}


# ── Placement contract (legacy submission runner, POST /run) ─────────────────
# Field names are camelCase on purpose: they match the backend wire format
# (autopilot-backend app/schemas.py) one-to-one.
GRID_MAX_X = 10
GRID_MAX_Y = 5
MAX_DEFENCE_ID = 3


class GridPosition(BaseModel):
    x: int = Field(..., ge=0, le=GRID_MAX_X)
    y: int = Field(..., ge=0, le=GRID_MAX_Y)


class DefencePlacement(BaseModel):
    defenceId: int = Field(..., ge=0, le=MAX_DEFENCE_ID)
    defenceGridPosition: GridPosition


class EnemyEntry(BaseModel):
    enemyId: int = Field(..., ge=0)
    count: int = Field(..., ge=0)
    allowedRows: Optional[List[int]] = None


class Violation(BaseModel):
    type: str
    message: str


class SdkStatus(BaseModel):
    valid: bool
    violations: List[Violation] = []


class RunRequest(BaseModel):
    agentFileContent: str  # base64-encoded agent.py
    enemies: List[EnemyEntry]


class RunResponse(BaseModel):
    sdkStatus: SdkStatus
    defencePlacementData: Optional[List[DefencePlacement]] = None


# ── Live reinforcement contract (backend SDK service, POST /reinforcements) ─
class ReinforcementCounts(BaseModel):
    activeDefenceCount: int = Field(..., ge=0)
    activeEnemyCount: int = Field(..., ge=0)
    enemiesQueued: int = Field(..., ge=0)
    totalEnemiesInWave: int = Field(..., ge=0)


class UnitPosition(BaseModel):
    x: int
    y: int


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


class ReinforcementSnapshot(BaseModel):
    model_config = {"extra": "allow"}

    teamID: str
    sessionID: Optional[str] = None
    elapsedSeconds: float = 0.0
    counts: ReinforcementCounts
    defences: List[ReinforcementDefenceUnit] = []
    enemies: List[ReinforcementEnemyUnit] = []
    candidatePlacements: List[DefencePlacement] = []


class ReinforcementCall(BaseModel):
    teamID: str
    snapshot: ReinforcementSnapshot
