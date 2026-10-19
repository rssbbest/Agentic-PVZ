"""Control: a legal agent. One soldier near the base in each enemy's row."""


def get_reinforcements(state):
    occupied = {(d["position"]["x"], d["position"]["y"]) for d in state["defences"] if d["health"] > 0}
    placements = []
    for enemy in state["enemies"]:
        row = enemy["position"]["y"]
        cell = next(((x, row) for x in range(11) if (x, row) not in occupied), None)
        if cell:
            occupied.add(cell)
            placements.append({"defenceId": 1, "defenceGridPosition": {"x": cell[0], "y": cell[1]}})
    return placements
