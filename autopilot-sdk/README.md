# Autopilot SDK

This is the SDK source used by the unified local stack. It serves live Unity
reinforcement requests and retains the earlier evaluator submission runner.
For a self-contained executable and native platform build workflow, see the
[`autopilot-sdk-binary` project](../autopilot-sdk-binary/README.md).

## Live reinforcement agents

The backend calls the SDK at `POST /reinforcements` for each live board
snapshot. A participant's `agent.py` defines a module-level function:

```python
def get_reinforcements(state: dict) -> list[dict]:
    enemies = [
        e for e in state.get("enemies", [])
        if not e.get("isDead", False) and e.get("health", 0) > 0
    ]
    if not enemies:
        return []
    threat = min(enemies, key=lambda e: e["position"]["x"])
    row = threat["position"]["y"]
    occupied = {
        (d["position"]["x"], d["position"]["y"])
        for d in state.get("defences", []) if d.get("health", 0) > 0
    }
    for column in (2, 1, 3, 0, 4, 5, 6, 7, 8, 9, 10):
        if (column, row) not in occupied:
            return [{"defenceId": 1, "defenceGridPosition": {"x": column, "y": row}}]
    return []
```

The grid is 11 columns by 6 rows: x is 0–10 and y is 0–5. Defence IDs are
0–3. Return `[]` to skip. The backend referee drops invalid cells, unknown
defence IDs, occupied cells, and duplicate cells without moving a placement.

The SDK loads team uploads from `AUTOPILOT_AGENT_DIR`, falling back to
`UPLOAD_DIR`. Backend uploads named `team{teamID}_{submission-id}.py` are
supported; the latest file for that team is used. The unified local launcher
sets `UPLOAD_DIR` to its managed upload directory. For Docker, share that
directory read-only with the SDK container and set `AUTOPILOT_AGENT_DIR` to
the mount path.

The hook runs in an isolated subprocess with a 3.5 second default limit; the
backend request timeout is 4.5 seconds. Exceptions, policy violations,
timeouts, missing agent files, and malformed hook output fail open to an empty
candidate list so the game resumes.

See [agent development](../autopilot-sdk-binary/docs/agent-development.md),
[backend integration](../autopilot-sdk-binary/docs/backend-integration.md), and
[testing](../autopilot-sdk-binary/testing.md) for full contracts and checks.

## Play with the agent on your machine

`autopilot play` runs `agent.py` locally and sits between the game and the
backend. The game calls `127.0.0.1:5000`; the SDK answers
`/api/reinforcements` by running `get_reinforcements(state)` in the usual
guarded subprocess, sends only the candidate placements to the backend
referee, and forwards every other `/api/*` call unchanged. The agent source
is never uploaded.

```sh
autopilot play --agent agent.py --backend https://backend.example
```

When the level loads (`/api/team`), the SDK also calls the agent once with
`state["phase"] == "start"`, no enemies and the coming wave in
`state["upcomingEnemies"]`, has the referee check the result and adds the
approved placements to the level's starting defences.

The agent must pass static verification and define `get_reinforcements`, or
the command exits. The file is re-read on every reinforcement, so edits apply
without a restart. Agent errors, timeouts, empty or off-grid output, and
backend errors all answer the game with `placeDefences: false`. Because the
agent runs on the participant's machine, these checks are not enforced
against a participant who modifies the SDK.

## Legacy submission runner

The older `/run` endpoint still accepts base64 agent source and enemy entries.
It runs an `Agent` subclass implementing `place_defences(enemies)` and returns
`sdkStatus` with placement data. The `autopilot` CLI also retains `verify`,
`register`, `submit`, `status`, and `leaderboard` for that pipeline.

## Develop and test

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
```

To start the unified stack, use `python -m integration.manage start` from the
repository root. The SDK listens on `http://127.0.0.1:8001`; use the direct
snapshot and backend API procedures in the testing guide to verify it without
starting Unity.
