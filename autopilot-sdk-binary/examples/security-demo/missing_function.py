"""Static layer: old Agent-class contract, no get_reinforcements(state).
`autopilot play` refuses to start with it."""
from autopilot_sdk import Agent


class OldStyleAgent(Agent):
    def place_defences(self, enemies):
        return [{"defenceId": 1, "defenceGridPosition": {"x": 0, "y": 0}}]
