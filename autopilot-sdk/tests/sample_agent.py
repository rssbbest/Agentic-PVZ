from autopilot_sdk import Agent
from autopilot_sdk.contracts.schemas import TurnDecision


class MyAgent(Agent):
    def decide(self, state) -> TurnDecision:
        self.memory = getattr(self, "memory", []) + [state.turn_number]
        return TurnDecision(actions=[], metadata={"echo": self.memory})
