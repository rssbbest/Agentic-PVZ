from autopilot_sdk import Agent
from autopilot_sdk.contracts.schemas import DefencePlacement, GridPosition


class PlacementAgent(Agent):
    def place_defences(self, enemies):
        total = sum(e.count for e in enemies)
        placement = [
            DefencePlacement(defenceId=i % 4, defenceGridPosition=GridPosition(x=i, y=4))
            for i in range(min(total, 5))
        ]
        # Dicts are accepted too.
        placement.append({"defenceId": 0, "defenceGridPosition": {"x": 0, "y": 0}})
        return placement
