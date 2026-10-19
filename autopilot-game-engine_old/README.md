> Unified checkout: use the [root setup guide](../README.md) and
> [current known bugs](../docs/KNOWN_BUGS.md). The component notes below retain
> upstream/history context and may describe earlier stand-in services or deployment defaults.

# Autopilot Unity engine

Use Unity **6000.1.17f1**, matching `ProjectSettings/ProjectVersion.txt`.
This branch is based on prototype revision `8b2cf2b303f6252c038e1f3999d50295bd6bc475`.
The supplied `build4.zip` was tested directly and preserved. It contains compiled
WebGL files, not the Unity source; these changes were rebuilt from the GitHub
source. Merge them into the source used to produce build4 if that source is newer.

## Gameplay corrections

- `EnemySpawningHandler`: wait for spawned enemies to finish before declaring a
  wave complete, reset completion state, include the last queued enemy in random
  selection, and prevent negative spawn delays.
- `Projectile`: capture shooter identity and placement when firing so a later hit
  can be logged after the shooter is destroyed; ignore duplicate hit callbacks
  and missing target or damage-visualizer components.
- `EnemyAI`: stop dead enemies moving or regenerating, handle death once, and
  update the target when a different defence blocks movement.
- `DefenceBase` / `DefenceAttack`: treat zero HP as dead, stop dead defences
  attacking, reject dead projectile targets, and use configured projectile speed.
- Combat logging: use instance IDs instead of prefab names so identical units
  have separate damage and death records. Reset logging between rounds.
- `PlacementSystem`: reset the defence count before initializing a formation.
- Runtime scripts: remove unnecessary `UnityEditor` and `NUnit` imports.
- `Main.unity`: remove the missing Canvas script reference and frame the
  integrated grid with camera position `(3.75, 3, -10)` and orthographic size `4.5`.

## Managed backend integration

`AutopilotBridge` activates when `UNITY_GATEWAY_URL` or `--autopilotGateway` is
set. In WebGL, `?autopilot=1` activates it using the page's origin. It polls the
integration service, applies assigned placements and enemy waves, runs the actual
Unity combat, publishes frames, posts results with retries, and reloads the scene
between rounds. It uses a 5-by-5 placement grid plus an enemy spawn column. The
normal GameManager flow is bypassed in this mode.

The service must implement these endpoints; **the unified repository supplies these separate components in sibling directories**:

| Endpoint | Contract |
| --- | --- |
| `GET /unity/jobs/next?worker=native` (or `web`) | Return a job with `runId`, `enemies`, `defencePlacementData`, and optional `timeScale`; `runId: 0` means no work. |
| `POST /unity/jobs/{runId}/frame` | Receive `{ "snapshot": ... }` containing unit positions, HP and counters. |
| `POST /unity/jobs/{runId}/result` | Receive `{ "result": ... }` containing engine, enemy/defence counts, duration and win state. Accept identical retries safely. |

The managed round limit is 180 simulation seconds. Remaining unresolved enemies
count as escapes. Default speed is 20x; jobs can request a speed from 1x to 20x.
Native players also support `UNITY_TIME_SCALE`. The bridge reports raw counters;
the external evaluator computes scores. No provider API keys belong in the player.

## Build and run

Install the Linux Build Support or WebGL Build Support module as appropriate.
The build helpers start directly in `Main.unity` for managed simulation.
Set `UNITY_EDITOR` below to your editor executable:

```bash
"$UNITY_EDITOR" -batchmode -nographics -quit -projectPath "$PWD" \
  -executeMethod AutopilotBuild.Linux -logFile /tmp/autopilot-linux-build.log

UNITY_GATEWAY_URL=http://127.0.0.1:8002 \
  ./Builds/Linux/Autopilot.x86_64

"$UNITY_EDITOR" -batchmode -nographics -quit -projectPath "$PWD" \
  -executeMethod AutopilotBuild.Web -logFile /tmp/autopilot-webgl-build.log
```

`AUTOPILOT_UNITY_BUILD` overrides the output path. Serve `Builds/WebGL` through
the integration service and open its URL with `?autopilot=1`. The WebGL helper
disables compression so ordinary static serving works without special encoding
headers.

## Validation performed locally

- Linux and WebGL builds passed with Unity 6000.1.17f1.
- Two scored submissions completed all 50 runs each in the native Unity player:
  baseline agent 83.7182, Gemini agent 68.6448. These use the local development
  scoring formula, not an official competition evaluator.
- Actual WebGL job claiming, result callbacks, gameplay frames and replay were
  exercised; the browser check recorded no JavaScript or gameplay exceptions.
- Across the separate SDK, integration and backend projects, 72 tests passed
  (47 SDK, 16 integration, 9 backend). Those test suites are not in this repo.
- After the camera-only change, both targets were rebuilt and a live WebGL run
  confirmed all 25 defence positions were visible. The two full submissions
  preceded that camera-only change.

This integration does not provide a secure sandbox for participant Python code.
