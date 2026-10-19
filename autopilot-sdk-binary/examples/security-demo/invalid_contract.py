from autopilot_sdk import Agent


class FirstAgent(Agent):
    def place_defences(self, enemies):
        return [{"defenceId": 1, "defenceGridPosition": {"x": 0, "y": 0}}]


class SecondAgent(Agent):
    def place_defences(self, enemies):
        return [{"defenceId": 3, "defenceGridPosition": {"x": 1, "y": 0}}]
