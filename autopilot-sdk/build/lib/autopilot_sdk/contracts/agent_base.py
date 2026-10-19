"""Agent contract — participant-facing public API (design doc §6)."""
from abc import ABC
from typing import Any, Dict, List

from .schemas import (
    DefencePlacement,
    EnemyEntry,
    MatchBootstrap,
    MatchResult,
    TurnDecision,
    TurnState,
)


class Agent(ABC):
    """Base class every participant must subclass in their agent.py.

    Lifecycle (all calls made by the SDK, never by the participant directly):

        __init__()          -> once, when the match starts
        on_match_start()    -> once, right after __init__, with match config
        decide(state)       -> once per turn, MUST be implemented
        on_match_end(result)-> once, when the match ends (win/lose/timeout)

    Placement mode (what the backend runs today, via `autopilot serve`):

        __init__()               -> once per enemy config
        place_defences(enemies)  -> legacy submission runner placement

    Live reinforcement mode (what the Unity backend calls today):

        module-level get_reinforcements(state) -> candidate placements

    The reinforcement runner loads that function from the participant's
    agent.py. It does not require the Agent class.

    An agent must override at least one of decide() / place_defences().

    Anything stored on `self` during decide()/on_match_start() is
    automatically preserved across turns by the SDK as long as it is
    picklable (lists, dicts, strings, numbers, numpy arrays, plain
    objects, etc. — NOT open file handles, sockets, locks).
    """

    def __init__(self, config: Dict[str, Any] | None = None):
        self.config: Dict[str, Any] = config or {}

    def on_match_start(self, match_info: MatchBootstrap) -> None:
        """Optional. Called once before the first turn."""
        pass

    def decide(self, state: TurnState) -> TurnDecision:
        """Turn mode. Called once per turn. Must return within
        state.time_limit_seconds or the SDK submits a no-op."""
        return TurnDecision(actions=[])

    def place_defences(self, enemies: List[EnemyEntry]) -> List[DefencePlacement]:
        """Placement mode. Given the enemy wave, return where each defence
        goes on the 11x6 grid (x 0-10, y 0-5, defenceId 0-3, one per cell).
        Plain dicts with the same shape are accepted too."""
        raise NotImplementedError("place_defences() is not implemented")

    def on_match_end(self, result: MatchResult) -> None:
        """Optional. Called once when the match ends. Cannot affect score."""
        pass
