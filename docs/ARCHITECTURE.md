# Runtime and API contracts

The live event flow (participants run the SDK; it picks the test case, runs
agent.py at every pause, logs in, and runs evals over hidden test cases) is in
[COMPETITION.md](COMPETITION.md). Below is the older upload-and-simulate pipeline.

The backend owns teams, source uploads, 50 enemy configurations, runs and scores.
Postgres persists that state. The SDK service executes participant code and
returns placements; it does not simulate combat. The adapter connects the backend
contract to native/WebGL Unity workers and supplies the local evaluator/frontend.

1. CLI/browser creates a team and posts multipart agent source to `/submissions`.
2. Backend creates runs and sends base64 source + enemies to SDK `POST /run`:
   `{"agentFileContent":"...","enemies":[{"enemyId":0,"count":5}]}`.
3. SDK returns `sdkStatus` and, if valid, `defencePlacementData`.
4. Backend calls adapter `POST /start` with `runId`, `enemies`, placements.
5. Native/WebGL Unity polls `GET /unity/jobs/next?worker=native` or `web`. No work
   returns `runId: 0`. A queued job is claimed once; optional `timeScale` selects
   speed. Practice favours the connected browser and has no scoring callback.
6. Unity instantiates real prefab defences/enemies, updates physics/animations and
   publishes `POST /unity/jobs/{id}/frame` with `{ "snapshot": ... }`. Snapshots
   contain positions, health, kinds and counters. The adapter streams them at
   `/watch/{id}` and persists completed replay files.
7. Unity posts `{ "result": ... }` to `/unity/jobs/{id}/result`:
   `engine`, `totalEnemies`, `killed`, `escaped`, `defencesPlaced`, `defencesAlive`,
   `durationSeconds`, `won`. Killed + escaped must equal assigned enemy count;
   placed defences must match the assigned formation. Identical retries are safe.
8. Adapter posts result to backend `/runs/{id}/result` with
   `X-Simulator-Secret`. Backend invokes adapter `/evaluate`, persists the numeric
   score, and updates submission mean and leaderboard. Invalid runs score zero.

Default native speed is 20x; practice requests 2x. A managed round stops after
180 simulation seconds; unresolved enemies count as escapes. This does not mean
180 wall-clock seconds. Backend's 1800-second watchdog is a separate wall-clock
infrastructure timeout.

Gateway accepts `POST /v1/chat/completions` with `messages` and `model`, proxies
to the configured OpenAI-compatible endpoint (`AUTOPILOT_LLM_BASE_URL`) using its
provider key, checks JSON output and returns a client-compatible
`choices[0].message.content`. A model/message hash keys the strategy cache.
Failures are surfaced; no deterministic fallback impersonates a successful LLM
call. `.env` is read by the launcher, which sets `AUTOPILOT_LLM_BASE_URL/API_KEY`
only for the
gateway process and `AUTOPILOT_LLM_MODEL` for agent execution.

Legacy compatibility endpoints `/api/team` and `/api/results` translate the
original game protocol to a queued team run. Since the old compiled game's
logging/end-of-wave behaviour is inaccurate, legacy results are marked explicitly
and do not replace corrected players for intended scoring.
