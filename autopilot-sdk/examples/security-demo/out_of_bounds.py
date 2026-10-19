"""Referee: one placement far off the board, one legal. The off-board one is
dropped; the legal one is approved."""


def get_reinforcements(state):
    return [
        {"defenceId": 1, "defenceGridPosition": {"x": 99, "y": 0}},
        {"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 4}},
    ]
