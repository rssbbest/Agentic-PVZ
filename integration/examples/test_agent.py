"""Deterministic baseline: ranged back line, shield front, penguin reserve."""
from autopilot_sdk import Agent


class TestAgent(Agent):
    def place_defences(self, enemies):
        heavy = any(e.enemyId in (2, 3) and e.count > 0 for e in enemies)
        return [
            {'defenceId': kind, 'defenceGridPosition': {'x': x, 'y': y}}
            for y in range(5)
            for x, kind in [(0, 1), (1, 2), (2, 1), (3, 0 if heavy else 1), (4, 3)]
        ]
