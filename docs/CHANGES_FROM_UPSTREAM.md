# Changes versus the cloned code

Exact imported commits are in [upstream-revisions.json](upstream-revisions.json).
The engine includes commit `a597b12`, previously pushed as
`fix/build4-gameplay-integration`, based on prototype `8b2cf2b`. The compiled
`build4.zip` was tested separately and is not treated as editable Unity source.

## Contract mismatches resolved

| Previously | Unified behaviour |
| --- | --- |
| Unity manually requests `/api/team` with a four-digit team ID; backend schedules a run for each of 50 configurations through `/start`. | Adapter queues each assigned run; `AutopilotBridge` polls it with the exact `runId`. Legacy team endpoints remain as optional compatibility. |
| Unity reports team-level damage logs to `/api/results`; backend expects `{result: ...}` at `/runs/{id}/result`, authenticated with `X-Simulator-Secret`. | Bridge reports validated counters for the assigned run; adapter sends the authenticated callback. |
| SDK/backend accept coordinates 0–4; Unity's grid dimensions depend on inspector settings. | Managed mode uses 5×5 defence cells and an extra spawn column. |
| Backend exposes run status/score, without live Unity unit state for the browser. | Unity publishes positions and HP; adapter serves live snapshots, recorded replays and a control page. |
| Simulator/evaluator implementations were external dependencies. | Included adapter provides the required `/start` and `/evaluate` contracts, using real Unity or an explicitly selectable portable approximation. |

The original **placement JSON already agreed**: `defencePlacementData`,
`defenceId`, `defenceGridPosition: {x, y}`. No incompatible replacement schema
was introduced.

## Unity changes

| Files | Fix/addition |
| --- | --- |
| `EnemySpawningHandler.cs` | Reset wave completion, correct exclusive integer `Random.Range` upper bound, clamp spawn delays, wait for active enemies to die, seed managed spawning. |
| `Projectile.cs` | Capture shooter identity/cell at firing time, handle destroyed shooter/missing components, prevent duplicate hit processing. |
| `EnemyAI.cs` | Stop dead movement/regeneration, avoid repeated death handling, update stale targets, resolve managed kills/escapes once. |
| `DefenceBase.cs`, `DefenceAttack.cs` | Handle HP exactly zero, stop dead attacks, reject dead targets, use projectile-speed setting. |
| Enemy/defence attack and death logs | Use instance IDs instead of prefab names so identical units do not merge. |
| `PlacementSystem.cs` | Reset defence count and retain defence kind for frames. |
| `GameManager.cs`, `LosingTrigger.cs` | Bypass interactive round flow in managed mode, reset logging, guard repeated end panels, handle managed breaches without pausing all work. |
| Runtime imports | Remove unnecessary `UnityEditor` and NUnit imports that interfere with player builds. |
| `CustomGridSystem.cs`, `Main.unity` | Align managed grid, remove dangling Canvas script, reframe camera for all 25 placements. |
| `AutopilotBridge.cs` (new) | Job polling, real gameplay orchestration, frames, counter results, result retries, scene reset. |
| `Assets/Editor/AutopilotBuild.cs` (new) | Linux and uncompressed WebGL build helpers targeting Main directly. |

## SDK/backend changes from the imported revisions

- SDK `runtime/placement.py`: catch EOF/OSError when an agent exits before sending
  a result, returning `agent_crashed` instead of an internal HTTP 500. Includes a
  recovery test and ten security demonstration examples.
- Backend `evaluator_service.py`: reject missing, boolean and non-finite scores.
- Backend `orchestrator.py` / `config.py`: watchdog for missing simulator callbacks;
  timed-out runs become failed rather than waiting indefinitely. Settings ignore
  unrelated root `.env` fields in the unified repo.
- Backend tests cover invalid scores and watchdog behaviour.
- SDK test fixtures use paths relative to their test files so tests also run from
  the unified root directory.

The SDK's pre-existing CLI, runner service, placement contract and provider
filters were already in imported revision `5da1c37`; this unified work did not
originate those features. The provider denylist remains an incomplete security
boundary.

## New unified components

- `integration/service.py`: queued Unity protocol, original-build compatibility,
  evaluator, uploads, frontend/API proxy, practice, replay persistence, matching
  result validation, safe identical retries and worker heartbeats.
- `integration/static/`: browser formation editor, submission progress and replay.
- `integration/gateway.py`: local OpenAI-compatible adapter, JSON responses, saved strategy
  cache, visible provider errors; no invented fallback results.
- `integration/combat.py`, `engine_data.py`: portable simulation and scene/prefab
  catalogue; portable mode is explicitly different from real Unity.
- `integration/manage.py`, `settings.py`: root `.env` loading, role-specific child
  environments, separate Docker database/volume, private generated secrets,
  loopback services and owned-process lifecycle management.
- `integration/unity.py`: configured editor/player paths instead of requiring one
  developer's machine layout; optional compatibility libraries.
- `integration/live_test.py`: fresh submissions instead of hardcoded historical
  IDs, baseline-only mode and optional outage/retry check.
- `integration/browser_test.py`: optional submission ID/headless mode rather than
  assuming submission 11 exists on a new database.
- Root `agent.py`, `.env.example`, requirements, Makefile, setup script and docs:
  a single-clone setup and participant handoff.

Machine-generated editor/URP settings, `.vscode`, Python/Unity caches, binaries,
private runtime files and actual API keys were excluded from the imported source.

Unity Git attributes were scoped for the nested directory and ordinary Git assets;
macro declarations only work at repository root. The original template is
preserved in `upstream-unity.gitattributes.txt`. Git LFS is not required.

## Asset packaging correction

442 available Git LFS objects were resolved to real files and checked against
upstream SHA-256 identifiers. Another 884 missing objects were all in the
third-party Layer Lab icon demo pack. Their data was absent from the preserved
clones. That demo pack was omitted; the one Main-scene reference to its missing
next-arrow icon was replaced with the project's existing `arrow3.png` sprite.
Combat prefabs, unit balance and the managed gameplay code were preserved.
All packaged assets are ordinary Git files, without unresolved LFS placeholders.

The launcher waits briefly for owned processes to exit and permits socket reuse
after shutdown, preventing false port-conflict reports during a mode switch.

Unity Plastic SCM workspace metadata is omitted from the unified repository.
