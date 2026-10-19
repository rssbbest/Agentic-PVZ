"""Referee: two defences in the same free cell. Only the first is approved."""


def get_reinforcements(state):
    return [
        {"defenceId": 1, "defenceGridPosition": {"x": 3, "y": 2}},
        {"defenceId": 3, "defenceGridPosition": {"x": 3, "y": 2}},
    ]
