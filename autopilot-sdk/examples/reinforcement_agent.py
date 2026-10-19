"""Example agent hook for live mid-run reinforcement requests.

Every defence costs money: state["unitCosts"] (e.g. {"1": 200}) and
state["budget"]["remaining"] say what this call may spend. The referee drops
placements that do not fit, so this agent only proposes what it can pay for.
"""


def _affordable(state: dict, placements: list[dict]) -> list[dict]:
    """The placements, in order, that the remaining budget pays for."""
    remaining = (state.get("budget") or {}).get("remaining")
    if not isinstance(remaining, (int, float)):
        return placements
    costs = state.get("unitCosts") or {}
    chosen = []
    for placement in placements:
        cost = costs.get(str(placement["defenceId"]), 0)
        if cost <= remaining:
            remaining -= cost
            chosen.append(placement)
    return chosen


def _opening(state: dict) -> list[dict]:
    """Before the first enemy: one soldier at x=1 in every row enemies may
    use, as many as the budget pays for."""
    rows = sorted({
        row
        for entry in state.get("upcomingEnemies", [])
        for row in entry.get("allowedRows") or range(6)
        if isinstance(row, int) and 0 <= row <= 5
    })
    occupied = {
        (defence.get("position", {}).get("x"), defence.get("position", {}).get("y"))
        for defence in state.get("defences", [])
    }
    return _affordable(state, [
        {"defenceId": 1, "defenceGridPosition": {"x": 1, "y": row}}
        for row in rows
        if (1, row) not in occupied
    ])


def get_reinforcements(state: dict) -> list[dict]:
    """Place a soldier in the row of the living enemy closest to the base."""
    if state.get("phase") == "start":
        return _opening(state)
    living_enemies = [
        enemy
        for enemy in state.get("enemies", [])
        if not enemy.get("isDead", False) and enemy.get("health", 0) > 0
    ]
    if not living_enemies:
        return []

    urgent_enemy = min(
        living_enemies,
        key=lambda enemy: enemy.get("position", {}).get("x", float("inf")),
    )
    position = urgent_enemy.get("position", {})
    row = position.get("y")
    if not isinstance(row, int) or not 0 <= row <= 5:
        return []

    occupied = {
        (defence.get("position", {}).get("x"), defence.get("position", {}).get("y"))
        for defence in state.get("defences", [])
        if defence.get("health", 0) > 0
    }
    for column in (2, 1, 3, 0, 4, 5, 6, 7, 8, 9, 10):
        if (column, row) not in occupied:
            return _affordable(state, [{"defenceId": 1, "defenceGridPosition": {"x": column, "y": row}}])
    return []
