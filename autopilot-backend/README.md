# Zombie Apocalypse: Agentic Event – Backend

Participants submit an `agent.py` that defends a 5x5 grid by placing weapons. The backend runs it against a fixed suite of enemy configs, scores every run through the simulator and evaluator, and ranks teams on the average.

| Layer | Responsibility |
|---|---|
| **SDK** | Validates `agent.py` (provided API only, latency, etc.), runs it with the enemy input, returns the placement. |
| **Backend + DB** (this repo) | Owns the enemy configs, orchestrates runs, relays data between services, stores results and scores. |
| **Simulator** | Receives enemies + placement, handles spawn timing and combat itself, renders live. |
| **Evaluator** | Turns the simulator result into a score. |

## How a submission is scored

Participants send only `team_id` + `agent_file`. They never choose enemies.

```
POST /submissions
  └─ 1 Submission + 1 Run per enemy config (50 seeded)
       for each Run (max MAX_CONCURRENT_RUNS at a time):
         SDK(agent.py, that config's enemies) ─► sdkStatus + defencePlacementData
           invalid          → run `invalid` (counts 0)
           infra error      → run `failed`
           valid            → run `simulating` → simulator /start
                                  simulator ─► POST /runs/{id}/result ─► evaluator ─► run `completed` + score
       when every run is terminal:
         any run `failed`  → submission `failed` (no score; admin retries)
         otherwise         → submission `completed`, total_score = mean of run scores
```

- `agent.py` is executed once per enemy config, so the agent sees the enemies it will face in each run.
- The leaderboard is one row per team, using its best `completed` submission.
- Backend-side placement rules: no two defences on the same cell (grid range and `defenceId` 0-3 come from the schema). Weapon/ammo budgets are **not** enforced here yet.

## Run it

```bash
cp .env.example .env              # set SIMULATOR_SECRET and ADMIN_SECRET
docker compose up --build         # Postgres + backend on :8000
# or locally: pip install -r requirements.txt && uvicorn app.main:app --reload
```

Docs at `http://localhost:8000/docs`. Tables are created from `migrations/init.sql` (Docker) or `create_all` (local). **After pulling schema changes, recreate the DB** (`docker compose down -v`); neither path migrates existing tables.

`docker-compose.yml` points to `sdk`, `simulator` and `evaluator` hosts that are not defined here; add those services or change the URLs.

## Config (`.env`)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres async URL |
| `UPLOAD_DIR` | Where uploaded agent files are stored |
| `SDK_URL` / `SIMULATOR_URL` / `EVALUATOR_URL` | Service base URLs |
| `SIMULATOR_SECRET` | Simulator sends it as `X-Simulator-Secret` on `/runs/{id}/result` |
| `ADMIN_SECRET` | Organiser calls send it as `X-Admin-Secret` |
| `MAX_CONCURRENT_RUNS` | Agent runs in flight at once (default 4) |

Enemy data is not configured here; it lives in the database. The app warns on startup if either secret is still the default.

## API

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| POST | `/teams` | – | Register a team |
| POST | `/submissions` | – | Upload `agent_file` + `team_id`; starts all runs |
| GET | `/submissions/{id}` | – | Status, `total_score`, per-config runs |
| POST | `/submissions/{id}/retry` | admin | Re-run only the infra-failed runs |
| GET | `/runs/{id}` | – | SDK status, placement, evaluator output, score |
| POST | `/runs/{id}/result` | simulator | Simulator posts its result; triggers the evaluator |
| WS | `/runs/{id}/stream` | – | Live events; late joiners get the current state first |
| GET | `/enemy_configs`, `/enemy_configs/{id}` | – | The test suite |
| POST | `/enemy_configs` | admin | Add a config |
| GET | `/leaderboard` | – | Best submission per team, dense-ranked |

Add enemy configs **before** the event: a new config changes the suite, so earlier submissions would no longer be scored on the same set.

## Service contracts

**SDK** `POST {SDK_URL}/run`
```json
{ "agentFileContent": "<base64 agent.py>", "enemies": [{ "enemyId": 0, "count": 5 }] }
```
Response: `{"sdkStatus": {"valid": true, "violations": []}, "defencePlacementData": [{"defenceId": 0, "defenceGridPosition": {"x": 0, "y": 0}}]}`. When invalid, `defencePlacementData` is omitted and `violations` is e.g. `[{"type": "invalid_agent_output", "message": "..."}]`.

**Simulator** `POST {SIMULATOR_URL}/start` with `{"runId", "enemies", "defencePlacementData"}`; it later calls `POST /runs/{runId}/result` with `{"result": {...}}` and the `X-Simulator-Secret` header.

**Evaluator** `POST {EVALUATOR_URL}/evaluate` with `{"result": {...}}`; the response must contain a numeric `score`. A response without one fails the run.

## Data model

```
teams          (id, name, created_at)
submissions    (id, team_id, file_path, status, total_score, created_at)
enemy_configs  (id, name, enemies JSONB)
runs           (id, submission_id, enemy_config_id, status, sdk_status JSONB,
                defence_placement JSONB, evaluator_output JSONB, score, created_at)
               UNIQUE (submission_id, enemy_config_id)
```

Run status: `pending → simulating → completed`, or `invalid` / `failed`. Submission status: `pending`, `completed`, `failed`.

## Known limits

- State is in-process (WebSocket registry, background tasks). Run a **single** uvicorn worker. A restart mid-run leaves runs in `pending`/`simulating`; there is no timeout sweep yet.
- Runs are started concurrently up to `MAX_CONCURRENT_RUNS`; the simulator must queue or parallelise real-time games itself.
- No upload size limit; `POST /teams` is open.
