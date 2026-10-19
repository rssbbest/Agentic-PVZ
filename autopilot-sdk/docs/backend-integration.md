# SDK and backend integration

The current Unity flow is Unity → backend → SDK runner. Unity calls the
backend's `/api/reinforcements` endpoint. The backend sends the live snapshot
to the SDK runner at `POST /reinforcements`, then validates the candidate
placements and returns approved placements to Unity.

The SDK runner also retains the separate legacy `POST /run` endpoint for the
submission/evaluator pipeline. The two endpoints use different agent
contracts: live reinforcements use `get_reinforcements(state)`; `/run` uses
base64 source and `Agent.place_defences(enemies)`.

## Run the SDK service

```sh
autopilot serve --host 0.0.0.0 --port 8001
```

`GET /health` returns `{"status":"ok"}`. The reinforcement hook defaults to a
3.5 second execution limit. Configure it with
`AUTOPILOT_REINFORCEMENT_TIMEOUT_SECONDS` or `--reinforcement-timeout` (the
server caps it at 4 seconds to leave room within the backend's 4.5 second
HTTP timeout). The legacy `/run` timeout is configured separately with
`--timeout`.

## Agent file lookup

The backend's reinforcement request contains `teamID` and `snapshot`, but does
not include agent source. The SDK resolves the latest source file for that
team from `AUTOPILOT_AGENT_DIR` (default `./agents`). It supports the backend
upload name `team{teamID}_{submission-id}.py` and a direct `{teamID}.py` file,
case-insensitively. A leading `team` in the requested ID is normalized, so
`TEAM1` can resolve `team1_a1b2c3.py`. Direct files can use the exact team name
or its normalized underscore form (for example, `Friendly Team.py` or
`friendly_team.py`). If no source is found, the SDK returns an empty candidate
list; the backend skips that reinforcement cycle.

In the unified Compose setup, the backend stores uploads in `./uploads` and
mounts that directory read-only into the SDK at `/agents`. The SDK service
sets `AUTOPILOT_AGENT_DIR=/agents`. Keep this shared directory available to
both containers and do not make it writable from the SDK container.

## Backend-to-SDK request

The backend calls:

```http
POST /reinforcements
Content-Type: application/json
```

```json
{
  "teamID": "42",
  "snapshot": {
    "teamID": "42",
    "sessionID": "a1b2c3d4e5f6",
    "elapsedSeconds": 20.0,
    "counts": {
      "activeDefenceCount": 3,
      "activeEnemyCount": 4,
      "enemiesQueued": 6,
      "totalEnemiesInWave": 10
    },
    "defences": [
      {
        "id": "defenceSoilder_0",
        "type": "defenceSoilder",
        "health": 120.0,
        "maxHealth": 150.0,
        "position": {"x": 1, "y": 2}
      }
    ],
    "enemies": [
      {
        "id": "EnemClown_0",
        "type": "EnemClown",
        "health": 45.0,
        "maxHealth": 100.0,
        "position": {"x": 3, "y": 2},
        "isAttacking": true,
        "isDead": false
      }
    ],
    "candidatePlacements": []
  }
}
```

The SDK passes the validated `snapshot` dictionary to the agent function. The
outer and inner `teamID` values must match.

## SDK-to-backend response

The SDK responds with HTTP 200 and a candidate list:

```json
{
  "defencePlacementData": [
    {"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 3}}
  ]
}
```

An empty list means skip. The backend also accepts a raw list, but the SDK
uses the object form. SDK errors, agent exceptions, blocked network attempts,
invalid output, and timeouts fail open to an empty list so the game resumes.
Malformed requests return HTTP 400; the backend treats any request failure as
a skipped reinforcement.

The SDK returns candidates and does not relocate them. The backend referee
checks grid bounds (x 0–10, y 0–5), defence IDs (0–3), living-defence
occupancy, and duplicate cells in each batch. Invalid placements are dropped
individually. If no candidates survive, the backend returns
`{"placeDefences": false, "defencePlacementData": []}` to Unity.

## Manual backend setup

If deploying outside the provided Compose file, mount the backend's upload
directory read-only into the SDK container and set `AUTOPILOT_AGENT_DIR` to
that mount path. Set the backend's `SDK_URL` to the SDK service address, such
as `http://sdk:8001`. Keep the SDK service reachable only by the backend.

The Unity client talks to the backend's `/api/team`, `/api/results`, and
`/api/reinforcements` routes; it does not call the SDK directly. See the
provided backend API guide for Unity-facing routes, spawn/game settings,
screenshots, and state snapshots. The SDK does not implement the Unity-facing
HTTP API itself.
