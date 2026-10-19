# Writing an Autopilot agent

The current Unity reinforcement runner calls one module-level function in the
submitted `agent.py` for each reinforcement pause. The function receives the
live board snapshot and returns candidate placements. The backend referee
checks each candidate and drops invalid ones; it never relocates a placement.

```python
def get_reinforcements(state: dict) -> list[dict]:
    living_enemies = [
        enemy
        for enemy in state.get("enemies", [])
        if not enemy.get("isDead", False) and enemy.get("health", 0) > 0
    ]
    if not living_enemies:
        return []

    # The smallest x coordinate is the enemy closest to the base.
    threat = min(living_enemies, key=lambda enemy: enemy["position"]["x"])
    row = threat["position"]["y"]
    occupied = {
        (d["position"]["x"], d["position"]["y"])
        for d in state.get("defences", [])
        if d.get("health", 0) > 0
    }

    # Grid is x=0..10, y=0..5. Choose an unused cell in the threatened row.
    for column in (2, 1, 3, 0, 4, 5, 6, 7, 8, 9, 10):
        if (column, row) not in occupied:
            return [{
                "defenceId": 1,
                "defenceGridPosition": {"x": column, "y": row},
            }]
    return []
```

Return `[]` to skip. `defenceId` must be one of `0`, `1`, `2`, or `3`; x is
from 0 through 10 and y from 0 through 5. Use only currently free cells. The
referee drops out-of-bounds positions, unknown defence IDs, cells occupied by
living friendly units, and duplicate cells in a batch. It does not nudge them
to another location. A skipped or failed SDK call resumes the game without
reinforcements.

The snapshot dictionary contains `teamID`, `sessionID`, `elapsedSeconds`,
`counts`, `defences`, `enemies`, and `candidatePlacements`. Counts contain
`activeDefenceCount`, `activeEnemyCount`, `enemiesQueued`, and
`totalEnemiesInWave`. Unit entries include IDs and types, current and maximum
health, and positions; enemies also include `isAttacking` and `isDead`.
See [the API guide](backend-integration.md#reinforcement-snapshot) for a full
JSON example and field definitions.

`autopilot play` adds two keys: `unitCosts` (price per defence ID, e.g.
`{"0": 250, "1": 200, "2": 300, "3": 150}`) and `budget` (`remaining`,
`spent`, `unlocked`, `total`). Every match starts with 3000 coins and each
kill earns 50 more; `total` is what killing the whole wave would bring
(3000 + 50 per enemy). The referee approves placements in the order returned until
`remaining` runs out, and the SDK prints the reason for each refusal.

`state["phase"]` is `"pause"` at the timed reinforcement pauses. With
`autopilot play` the game's first call, as the level begins, has
`state["phase"] == "start"` and three extra keys: `upcomingEnemies` (the wave:
`enemyId`, `count`, `allowedRows`), `spawnSettings` and `gameSettings`. The first
enemy may already be on the board. Its approved placements form the opening
board; an agent that returns `[]` here faces the wave with no defences.

```python
def get_reinforcements(state):
    if state.get("phase") == "start":
        rows = {r for e in state["upcomingEnemies"] for r in e.get("allowedRows") or range(6)}
        return [{"defenceId": 1, "defenceGridPosition": {"x": 1, "y": r}} for r in sorted(rows)]
    ...
```

The runner statically checks source and executes the hook in an isolated
subprocess with a sandbox. Inside it, the network reaches only loopback and
the organiser's LLM gateway (`AUTOPILOT_GATEWAY_URL`); local LLM ports,
provider SDK imports, starting programs or processes, native calls through
`ctypes` and `gc` object walks are blocked. A blocked action ends the call
and places nothing, even if the agent catches the error. Keep the strategy
quick: under `autopilot play` the hook has 15 seconds (`state["timeBudgetSeconds"]`)
and the game waits for the answer; `autopilot serve` keeps a 4 second limit for
the backend's 4.5 second request. The sandbox is not an OS security
sandbox; see `examples/security-demo` for what it does and does not cover.

## Local checks

Install the optional development tools and verify source:

```sh
python -m pip install -e '.[dev]'
autopilot verify --agent agent.py
```

`autopilot verify` checks the source policy. The live hook’s exact function
contract and behavior are tested with `pytest`; see [testing](../testing.md).
For a ready-made example, see [`examples/reinforcement_agent.py`](../examples/reinforcement_agent.py).

## Legacy submission runner

The older backend submission workflow remains available through `POST /run`
and the CLI commands `register`, `submit`, `status`, and `leaderboard`. It uses
an `Agent` subclass implementing `place_defences(enemies)` as described in the
top-level README. That interface is separate from the current Unity live
reinforcement hook, which is `get_reinforcements(state)`.
