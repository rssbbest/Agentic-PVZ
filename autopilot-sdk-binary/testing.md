# SDK and binary testing (without Unity)

This guide checks the agent contract and SDK service with local HTTP requests.
No browser, Unity player, or live game is required. The backend can be tested
through its FastAPI endpoint, or the SDK can be exercised directly.

## Automated SDK checks

From this directory, install the development dependencies and run the suite:

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
```

The suite includes the legacy `/run` route and the current reinforcement route.
`tests/test_reinforcements.py` checks the new snapshot schema, team upload
lookup, valid placements, empty/failed agent behavior, network-policy rejects,
execution timeouts, malformed requests, and the HTTP response shape.

## Test the SDK HTTP service directly

Create one local team agent. The `team42_*.py` naming matches files saved by
the backend submission endpoint:

```sh
mkdir -p /tmp/autopilot-agents
cat >/tmp/autopilot-agents/team42_local.py <<'PY'
def get_reinforcements(state: dict) -> list[dict]:
    if not state.get("enemies"):
        return []
    return [{"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 2}}]
PY
AUTOPILOT_AGENT_DIR=/tmp/autopilot-agents \
  ./bin/autopilot-linux-x86_64 serve --host 127.0.0.1 --port 18001
```

In another terminal, check service health:

```sh
curl -i http://127.0.0.1:18001/health
```

Send the same request the backend sends to the SDK:

```sh
curl -i http://127.0.0.1:18001/reinforcements \
  -H 'Content-Type: application/json' \
  --data '{
    "teamID":"42",
    "snapshot":{
      "teamID":"42","sessionID":"local-test","elapsedSeconds":20,
      "counts":{"activeDefenceCount":1,"activeEnemyCount":1,"enemiesQueued":0,"totalEnemiesInWave":1},
      "defences":[],
      "enemies":[{"id":"EnemClown_0","type":"EnemClown","health":45,"maxHealth":100,"position":{"x":3,"y":2},"isAttacking":false,"isDead":false}],
      "candidatePlacements":[]
    }
  }'
```

Expected response:

```json
{"defencePlacementData":[{"defenceId":1,"defenceGridPosition":{"x":2,"y":2}}]}
```

Change `teamID` in both places to an ID with no matching agent file; the SDK
should return `{"defencePlacementData":[]}`. Set the inner `teamID` to a
different value from the outer one; the SDK should return HTTP 400.

## Test through the backend without Unity

The unified Compose file runs the backend and SDK together and shares the
uploaded agent directory read-only with the SDK. From `autopilot-backend/`:

```sh
docker compose up --build -d db backend sdk
```

Upload an agent through `POST /submissions` first, or place a local test file
named `team42_local.py` in `autopilot-backend/uploads/`. Then POST a
reinforcement snapshot directly to the backend route:

```sh
curl -i http://127.0.0.1:8000/api/reinforcements \
  -H 'Content-Type: application/json' \
  --data @/tmp/reinforcement-snapshot.json
```

Use the snapshot JSON from the direct SDK example above (without the outer
`teamID` wrapper). The backend calls the SDK, validates proposed cells against
living defences and grid rules, and returns a Unity-facing response such as
`{"placeDefences":true,"defencePlacementData":[...]}`. Empty, timed-out, or
invalid SDK output returns `placeDefences: false`; the game can resume without
reinforcements. This manual POST exercises the same backend and SDK services
as Unity without launching the game.

To inspect calls and skip behavior:

```sh
docker compose logs -f backend sdk
docker compose down
```

## Rebuild and check the standalone binary

Build on the OS and CPU architecture you want to distribute:

```sh
python -m pip install '.[build]'
python build_binary.py
./dist/autopilot-linux-x86_64 version
./dist/autopilot-linux-x86_64 verify --agent examples/reinforcement_agent.py
```

For the Linux binary, repeat the direct `/health` and `/reinforcements` curl
requests with `./dist/autopilot-linux-x86_64` in place of the bundled binary.
The reinforcement endpoint test exercises the frozen process launcher as well
as the HTTP contract. Windows and macOS builds are produced on native GitHub
Actions runners and smoke-checked with their `version` command.

## Unity API dummy server

`dummyserver(1).py` is a mock for the Unity-facing backend API, not the SDK
runner. It can be started separately to inspect `/api/team`, `/api/results`,
and the mock reinforcement responses, but it does not call this SDK. To test
the real backend-to-SDK boundary without Unity, POST to the backend's
`/api/reinforcements` route as shown above.

## Verified in this branch

The following checks were run against the updated source and rebuilt Linux
x86_64 executable:

- `PYTHONPATH=src python -m pytest -q tests/test_reinforcements.py tests/test_placement_service.py` — **21 passed**.
- `PYTHONPATH=src python -m pytest -q` — **53 passed**.
- Unified backend tests — **5 passed**.
- Unified integration tests — **18 passed** (one upstream Starlette deprecation warning).
- `bin/autopilot-linux-x86_64 version` — reported `autopilot 0.1.0`.
- `bin/autopilot-linux-x86_64 verify --agent examples/reinforcement_agent.py` — static verification passed.
- Frozen binary `POST /reinforcements` with a local team snapshot — HTTP 200 and one valid candidate placement.
- Backend FastAPI `POST /api/reinforcements` with SDK_URL pointed at the frozen binary service — HTTP 200, `placeDefences: true`, and the expected placement. This was an in-process ASGI request; no database, Unity process, or live game was started.
- `git diff --check` — passed before commit.

The Docker Compose stack itself was not started here because this environment
has Docker Engine but no `docker compose` plugin. The service boundary was
tested directly and through the real backend ASGI app instead.
