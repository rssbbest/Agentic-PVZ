"""Referee: places on the cell a living defence already holds. The referee
drops it and never moves it to another cell."""


def get_reinforcements(state):
    held = state["defences"][0]["position"]
    return [{"defenceId": 3, "defenceGridPosition": {"x": held["x"], "y": held["y"]}}]
