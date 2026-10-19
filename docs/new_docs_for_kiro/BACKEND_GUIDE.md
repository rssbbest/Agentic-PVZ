# Backend guide (`autopilot-backend/`)

The complete reference for the backend: what it does, every route, how data
flows through it, what it stores (in Postgres and in memory), and where each
piece of logic lives. Use it to find the right file before changing anything.

> **Keep this file current.** Every change to `autopilot-backend/` (routes,
> schemas, tables, env vars, scoring, budget rules) must update the matching
> section here and add a line to [Changelog](#18-changelog). The SDK side is in
> [SDK_GUIDE.md](SDK_GUIDE.md).

---

## Table of contents

1. [What the backend is](#1-what-the-backend-is)
2. [Who talks to the backend](#2-who-talks-to-the-backend)
3. [Tech stack and runtime constraints](#3-tech-stack-and-runtime-constraints)
4. [File map](#4-file-map)
5. [Configuration (env vars)](#5-configuration-env-vars)
6. [Startup sequence](#6-startup-sequence)
7. [Authentication and identity](#7-authentication-and-identity)
8. [Database schema](#8-database-schema)
9. [In-memory state](#9-in-memory-state-single-worker-only)
10. [Route reference (all routes)](#10-route-reference-all-routes)
11. [Live game flow, step by step](#11-live-game-flow-step-by-step)
12. [Eval flow](#12-eval-flow)
13. [The referee (`/api/reinforcements`)](#13-the-referee-apireinforcements)
14. [Economy: budget, coins, bounty](#14-economy-budget-coins-bounty)
15. [Result completion and scoring](#15-result-completion-and-scoring)
16. [Legacy pipeline: submissions, runs, simulator](#16-legacy-pipeline-submissions-runs-simulator)
17. [Deployment, tests, quirks, how-to](#17-deployment-tests-quirks-how-to)
18. [Changelog](#18-changelog)

---

## 1. What the backend is

A single FastAPI app (`app.main:app`) backed by Postgres. It is the source of
truth for the competition:

- **Team accounts**: registration, approval, ban, password login, signed login
  tokens, and each team's LLM-gateway token.
- **Test cases (levels)**: public ones (played with `play`) and hidden ones
  (played only inside an `eval`).
- **Game sessions**: one row per game the SDK starts; records pauses,
  referee-approved placements, the full result and the score.
- **The referee**: validates every placement the agent proposes (grid, unit
  type, free cell, budget) and says why it rejected the others.
- **Scoring**: completes the game's end-of-match report from the snapshots it
  saw, validates it and scores it out of 100 (in-process, no external
  evaluator).
- **Eval runs**: the official score, the mean over all active hidden test cases,
  with the frozen `agent.py` source and the uploaded run log.
- **Admin dashboard** (`/admin`) and **registration page** (`/register`), both
  static HTML served by the backend.
- **Legacy pipeline** (still mounted, not exposed publicly in the event
  deployment): agent uploads (`/submissions`), 50 enemy configs, runs, an
  external SDK service and simulator, and a submissions leaderboard.

## 2. Who talks to the backend

The code does **not** have the backend calling Unity in the live competition
flow. The Unity WebGL game runs in the participant's browser and sends every
API call to the SDK on the same laptop (`http://127.0.0.1:5000`). The SDK
runs the agent locally and forwards the calls to the backend. The backend only
answers HTTP requests; it never initiates a connection to the game.

```
participant laptop                                        organiser server
┌───────────────────────────────┐                        ┌──────────────────────────────────┐
│ Browser: Unity WebGL (build7) │                        │ Caddy (HTTPS)                    │
│        │ http://127.0.0.1:5000│                        │  /backend/api/*  → backend:8010   │
│        ▼                      │  HTTPS /backend/api/*  │  /llm/v1/*       → gateway:8004   │
│ SDK local proxy (autopilot)  ─┼───────────────────────▶│  /  (game files) /download/      │
│   runs agent.py at pauses     │                        │                                  │
│   agent LLM calls ────────────┼───────────────────────▶│ backend (FastAPI) ⇄ Postgres     │
└───────────────────────────────┘   HTTPS /llm/v1/*      │ gateway → LLM provider           │
                                                         └──────────────────────────────────┘
```

| Caller | Calls | Purpose |
|---|---|---|
| SDK local proxy (on behalf of the game) | `/api/login`, `/api/team`, `/api/reinforcements`, `/api/state`, `/api/results`, `/api/screenshot` | Play a game |
| SDK (its own calls) | `/api/test-cases`, `/api/eval/info`, `/api/eval/start`, `/api/eval/{id}/finish`, `/health` | Pick test cases, run an eval |
| (no caller in the repo yet) | `/api/me`, `/api/eval/{id}` | Available for the SDK or a participant page |
| Browser (registration page) | `/register`, `/api/registration/form`, `/api/register` | Team sign-up |
| Browser (admin dashboard) | `/admin`, `/admin/api/*` with `X-Admin-Secret` | Organiser tools |
| `tools/seed_levels.py` | `/teams`, `/api/team-data*` | Seed legacy team levels (see §17) |
| Legacy simulator adapter (`integration/service.py`) | `/runs/{id}/result` (`X-Simulator-Secret`), `/runs/{id}`, `/submissions/{id}`, `/teams`, `/submissions`, `/leaderboard`, `/enemy_configs` | Legacy pipeline only |

Outbound calls the backend itself makes (all legacy, all optional):

| Target | Call | Where | Timeout | On failure |
|---|---|---|---|---|
| `SDK_URL` (SDK in `serve` mode) | `POST /run` | `services/sdk_service.run_agent` | 120 s | legacy run marked `failed`; `/api/team` legacy branch falls back |
| `SDK_URL` | `POST /reinforcements` | `services/sdk_service.query_reinforcements` | 4.5 s | returns `[]` silently |
| `SIMULATOR_URL` | `POST /start` | `services/simulator_service.start_simulation` | 30 s | legacy run marked `failed` |

`EVALUATOR_URL` is defined in config but **unused**: scoring is in-process
(`services/evaluator_service.py`).

## 3. Tech stack and runtime constraints

- Python 3.12 image (3.10+ works), FastAPI 0.111, Pydantic 2.7, SQLAlchemy 2.0
  async with asyncpg, httpx, aiofiles, python-multipart. Pinned in
  `autopilot-backend/requirements.txt`.
- Postgres 16 (`postgres:16-alpine`), DB name `zombie_defense`.
- **Run exactly one uvicorn worker.** Budget ledgers, approved placements and
  pause snapshots of live games, the password-check cache, the WebSocket
  registry and the legacy run semaphore live in process memory (§9). The event
  compose file pins `--workers 1`.
- A restart loses the in-memory state of every game in progress. Those games
  still score on `/api/results`, but without referee-approved placements and
  snapshots (see §15).

Ports by launch method:

| Launch | Port | Notes |
|---|---|---|
| Root `docker-compose.yml` (event) | 8010 (internal) | Caddy exposes it at `https://<site>/backend/...` |
| `make start` / `integration/manage.py` (local dev) | 8000 | binds 127.0.0.1 |
| `autopilot-backend/docker-compose.yml` (standalone) | 8000 | runs init.sql and an SDK `serve` container |
| `python autopilot-backend/main.py` | 5000, reload | clashes with the SDK local proxy port 5000 |

## 4. File map

```
autopilot-backend/
├── main.py                      dev runner: uvicorn app.main:app on :5000 with reload
├── Dockerfile                   standalone image (port 8000)
├── docker-compose.yml           standalone stack: db + backend + sdk serve (legacy)
├── requirements.txt
├── .env.example                 legacy-oriented env template
├── migrations/init.sql          legacy tables only (teams, submissions, enemy_configs,
│                                runs, team_data, results_log) + TEAM1/team1 seed
├── test_cases/
│   ├── reinforcement_test_cases.json   public cases TC01..TC10 (seeded into test_cases)
│   └── hidden_test_cases.json          example hidden cases H01..H05
├── tests/                       pytest (see §17)
└── app/
    ├── main.py                  FastAPI app, lifespan (create tables, migrate, seed), CORS,
    │                            request-logging middleware, router registration, /health
    ├── config.py                Settings (pydantic-settings, reads .env in CWD)
    ├── database.py              async engine, pool size, open_pool(), get_db dependency, Base
    ├── models.py                ORM models (all tables)
    ├── schemas.py               Pydantic request/response models
    ├── security.py              X-Simulator-Secret / X-Admin-Secret checks
    ├── ws_manager.py            WebSocket registry for legacy run streams
    ├── routers/
    │   ├── game_api.py          /api/team, /api/reinforcements, /api/state, /api/screenshot,
    │   │                        /api/results, /api/team-data (admin)
    │   ├── competition.py       login, register, me, test cases, eval, /register, /admin pages,
    │   │                        and the /admin/api/* router
    │   ├── submissions.py       POST /teams, /submissions (legacy)
    │   ├── runs.py              /runs/{id}, /runs/{id}/result (legacy)
    │   ├── enemy_configs.py     /enemy_configs + the 50 seeded configs (legacy)
    │   ├── leaderboard.py       /leaderboard (legacy submissions only)
    │   └── websocket.py         WS /runs/{id}/stream (legacy)
    ├── services/
    │   ├── accounts.py          password hashing, login tokens, LLM token, team code
    │   ├── competition.py       test cases, game sessions, eval progress, registration form,
    │   │                        level reply builder, test-case seeding
    │   ├── reinforcement_service.py  the referee + per-session placement ledger, db_id()
    │   ├── match_tracker.py     per-session unit memory from snapshots; completes reports
    │   ├── evaluator_service.py result validation, scoring formula, unit costs, budget maths
    │   ├── orchestrator.py      legacy: submission → runs → SDK → simulator
    │   ├── scoring.py           legacy: roll run scores up into a submission score
    │   ├── sdk_service.py       legacy: HTTP client for SDK serve (/run, /reinforcements)
    │   ├── simulator_service.py legacy: HTTP client for simulator /start
    │   └── seed_data.py         TEAM1/team1 + a TeamData row for every team
    └── static/
        ├── admin.html           organiser dashboard (calls /admin/api/*)
        └── register.html        registration form (calls /api/registration/form, /api/register)
```

## 5. Configuration (env vars)

`app/config.py` (`Settings`, pydantic-settings; reads `.env` from the working
directory, extra keys ignored):

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `ENVIRONMENT` | `development` | nothing | label only |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:password@localhost:5432/zombie_defense` | `database.py` | async Postgres URL |
| `UPLOAD_DIR` | `./uploads` | submissions, `/api/team` legacy, eval logs | legacy agent uploads `team{id}_<hex>.py`; eval logs in `eval_logs/` |
| `SDK_URL` | `http://localhost:8001` | `sdk_service` | legacy SDK `serve` service |
| `SIMULATOR_URL` | `http://localhost:8002` | `simulator_service` | legacy simulator adapter |
| `EVALUATOR_URL` | `http://localhost:8003` | nothing | unused (scoring is in-process) |
| `SIMULATOR_SECRET` | `change-me-in-production` | `security.verify_simulator` | `X-Simulator-Secret` for `/runs/{id}/result` |
| `ADMIN_SECRET` | `change-me-in-production` | `security.verify_admin`, token key fallback | `X-Admin-Secret` for admin routes |
| `MAX_CONCURRENT_RUNS` | `4` | `orchestrator._slots` | legacy runs in flight at once |
| `ENABLE_HEADLESS_SIMULATOR` | `False` | `orchestrator` | legacy: if false, valid runs stay `pending` instead of going to the simulator |
| `TEAM_TOKEN_SECRET` | `""` | `accounts._key` | HMAC key for login tokens; empty = derived from `ADMIN_SECRET` |
| `REQUIRE_LOGIN` | `False` | `game_api.get_team_data` | true: `play` also needs a login (eval always does) |
| `AUTOPILOT_GATEWAY_TEAM_SECRET` | `""` | `accounts.llm_token` | same secret as the LLM gateway; login replies then carry `llmToken` |

Read directly from `os.environ`:

| Variable | Default | Where | Meaning |
|---|---|---|---|
| `DB_POOL_SIZE` | `10` (event compose: 60) | `database.py` | connections opened at startup; `max_overflow=20` |
| `AUTOPILOT_STARTING_COINS` | `3000` | `evaluator_service.STARTING_COINS` | coins each match starts with |

On startup the app logs a warning if `SIMULATOR_SECRET` or `ADMIN_SECRET` is
still `change-me-in-production`. `AUTOPILOT_REINFORCEMENT_TIMEOUT_SECONDS` in
`autopilot-backend/.env.example` is read by the SDK container, not the backend.

Event deployment env (root `docker-compose.yml`, values from root `.env`, see
`deploy/.env.example`): `DATABASE_URL`, `UPLOAD_DIR=/data/uploads`,
`ADMIN_SECRET`, `SIMULATOR_SECRET`, `AUTOPILOT_STARTING_COINS`,
`DB_POOL_SIZE`, `AUTOPILOT_GATEWAY_TEAM_SECRET` (= `GATEWAY_TEAM_SECRET`),
`REQUIRE_LOGIN`. `SDK_URL` and `SIMULATOR_URL` are not set there, so the legacy
outbound calls fail fast and are ignored.

## 6. Startup sequence

`app/main.py` `lifespan()`:

1. Warn about default secrets.
2. `Base.metadata.create_all` creates missing tables (never missing columns).
3. Hand-written column migrations (idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`):
   `enemy_configs.budget`; `teams.password_hash`, `teams.approved` (default true),
   `teams.banned` (default false), `teams.profile`.
4. `seed_enemy_configs` (`routers/enemy_configs.py`): inserts the 50
   `SEED_CONFIGS` (`wave_1`..`wave_50`, enemy ids 0-3) if the table is empty.
5. `seed_default_teams` (`services/seed_data.py`): resyncs the `teams` and
   `team_data` id sequences, adds `team_data.spawn_settings`/`game_settings`
   columns if missing, creates teams `TEAM1` and `team1` if absent, and makes
   sure **every** team has a `team_data` row (default level: empty board,
   6× enemy 0 rows 1-3, 4× enemy 1 rows 0-4). Errors are logged and skipped.
6. `seed_test_cases` (`services/competition.py`): if `test_cases` is empty,
   loads `test_cases/reinforcement_test_cases.json` as public (`TC01`..`TC10`)
   and `hidden_test_cases.json` as hidden (`H01`..`H05`). Code = `code` field,
   else `TC%02d`/`H%02d` of `testCaseId` or position; name = the part after
   `<CODE>_` with underscores as spaces.
7. `open_pool()` opens `DB_POOL_SIZE` connections at once (a SCRAM login on the
   event loop stalls every request, so it is paid up front).

Middleware: CORS `allow_origins=["*"]` with credentials, all methods and
headers; an HTTP middleware logs `--> METHOD path from host` and
`<-- METHOD path [status] (ms)` for every request.

Routers are mounted in this order: `submissions.teams_router`,
`submissions.router`, `enemy_configs.router`, `runs.router`,
`leaderboard.router`, `websocket.router`, `game_api.router`,
`competition.router`, `competition.admin`. Plus `GET /health` → `{"status":"ok"}`.

## 7. Authentication and identity

### 7.1 Team ID ("team code")

A team's public ID is its database id zero-padded to 4 digits
(`accounts.team_code`: 42 → `"0042"`). Everywhere a team ID is parsed:

- `reinforcement_service.db_id(text)`: digits only, `0 < n < 2^31`, else `None`
  (stops long free-play session ids from reaching Postgres as int32 and 500-ing).
- `"teamN"` (case-insensitive) is accepted as id `N`.
- Otherwise game routes fall back to lookup **by team name** (exact, then
  case-insensitive), kept for old builds that typed a name.

### 7.2 Passwords (`services/accounts.py`)

- `hash_password`: PBKDF2-HMAC-SHA256, 100 000 iterations, 16-byte random salt.
  Stored as `pbkdf2_sha256$<iterations>$<salt b64>$<digest b64>` in
  `teams.password_hash`.
- `check_password`: constant-time compare. Successful checks are cached in
  `_VERIFIED` keyed by `sha256(stored_hash + "\0" + password)` (max 20 000,
  then cleared), so repeated logins from the game cost nothing. A password
  change changes the key. Hashing/checking runs in `asyncio.to_thread`.

### 7.3 Login token (Bearer)

- `issue_token(team_id)` → `"<team_id>.<expires_unix>.<sig>"`, where
  `sig = HMAC-SHA256(key, "<team_id>.<expires>")` hex, first 32 chars.
- `key = sha256(TEAM_TOKEN_SECRET or "team-token:" + ADMIN_SECRET)`. Changing
  either secret invalidates all tokens.
- TTL `TOKEN_TTL_S = 86400` (24 h).
- `bearer_team(authorization)` strips `Bearer ` and returns the team id or
  `None`; `require_team` is the FastAPI dependency (401
  `"Log in first (team ID and password in the game)"`).
- Where it matters: `/api/team` (a valid token **overrides** the `teamID` in the
  body), eval routes and `/api/me` (required).

### 7.4 LLM token

`accounts.llm_token(team_id)` → `"<code>.<HMAC-SHA256(AUTOPILOT_GATEWAY_TEAM_SECRET, code) hex[:20]>"`.
Identical to `integration/gateway.py team_token()` (a test checks this), so the
gateway accepts it and rate-limits per team (60 calls/min default). `None` if
the secret is unset.

### 7.5 Shared secrets (`app/security.py`)

- `verify_admin`: header `X-Admin-Secret` must equal `ADMIN_SECRET`
  (`secrets.compare_digest`), else 401 `"Admin secret required"`.
- `verify_simulator`: header `X-Simulator-Secret` must equal `SIMULATOR_SECRET`,
  else 401 `"Unauthorized simulator"`.

## 8. Database schema

All models are in `app/models.py`. `migrations/init.sql` only covers the legacy
tables (and an older `teams` without the account columns); the competition
tables exist only through `create_all`. There is no Alembic migration history.

### Competition tables

**`teams`**
| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | the team code is `id` zero-padded |
| `name` | varchar(120) unique | registration normalises whitespace; uniqueness checked case-insensitively in code |
| `created_at` | timestamptz | |
| `password_hash` | varchar(200) null | PBKDF2 string; null = cannot log in |
| `approved` | bool, default true | registration sets it from the form's `autoApprove` |
| `banned` | bool, default false | banned teams cannot log in or start games |
| `profile` | JSONB null | `{"answers": {questionId: value}, "notes": "..."}` |

Relationships: `submissions`, `team_data`. Deleting a team cascades to
submissions, runs, team_data, game_sessions and eval_runs (FK `ON DELETE CASCADE`).

**`test_cases`**
| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `code` | varchar(40) unique | always upper-case (`TC01`, `H03`); pattern `^[A-Za-z0-9_-]+$` on admin input |
| `name` | varchar(200) | |
| `hidden` | bool | hidden = only playable inside an eval; never listed to participants |
| `active` | bool | inactive cases are not listed or playable, and not in new evals |
| `position` | int | sort order (then `code`) |
| `level` | JSONB | `{"enemies": [...], "spawnSettings": {...}, "gameSettings": {...}, "defencePlacementData": [...]}` (normalised by `competition.normalise_level`) |
| `updated_at` | timestamptz | `onupdate=now()` |

**`game_sessions`** (one game the SDK started for a test case)
| Column | Type | Notes |
|---|---|---|
| `session_id` | varchar(40) PK | `"c"` + 15 hex chars |
| `team_id` | FK teams | |
| `test_case` | varchar(40) | the case code |
| `mode` | varchar(10) | `play` or `eval` |
| `eval_id` | FK eval_runs null | |
| `eval_slot` | int null | 1-based slot in the eval |
| `agent_sha256` | varchar(64) null | sent by the SDK in `/api/team` |
| `score` | float null | set on result (0.0 if unscored) |
| `success` | bool null | the game's `success` |
| `pauses` | int | snapshots received (`match_tracker.pauses`) |
| `placements` | JSONB null | every referee-approved placement (opening + reinforcements) |
| `result` | JSONB null | the completed report that was scored |
| `evaluator_output` | JSONB null | `{"valid", "score", "breakdown", "stats"}` or `{"valid": false, "error"}` |
| `started_at` / `finished_at` | timestamptz | `finished_at` set once; a second result is ignored |

**`eval_runs`**
| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | `evalId` |
| `team_id` | FK teams | |
| `status` | varchar(20) | `running` → `finished` / `incomplete`; `abandoned` when the team starts another eval |
| `case_codes` | JSONB list | the hidden case codes frozen at start, in order; slot N = `case_codes[N-1]` |
| `agent_source` | text | the full `agent.py` sent at start (≤ 512 KiB) |
| `agent_sha256` | varchar(64) | sha256 of `agent_source` |
| `sdk_version` | varchar(40) null | |
| `score` | float null | mean over slots (unplayed = 0), 2 decimals |
| `log_path` | text null | `UPLOAD_DIR/eval_logs/eval{id}_team{team}.jsonl.gz` |
| `summary` | JSONB null | whatever the SDK sends at finish |
| `started_at` / `finished_at` | timestamptz | |

**`competition_settings`**: `key` varchar(60) PK, `value` JSONB. Only key used:
`registration_form`.

### Legacy tables

**`submissions`**: `id`, `team_id` FK, `file_path` (on disk), `status`
enum `submission_status` (`pending`/`completed`/`failed`), `total_score`
numeric(10,4), `created_at`.

**`enemy_configs`**: `id`, `name`, `enemies` JSONB (`[{"enemyId", "count"}]`),
`budget` int null.

**`runs`**: `id`, `submission_id` FK, `enemy_config_id` FK, `status` enum
`run_status` (`pending`/`invalid`/`simulating`/`completed`/`failed`),
`sdk_status` JSONB, `defence_placement` JSONB, `evaluator_output` JSONB,
`score` numeric(10,4), `created_at`. Unique `(submission_id, enemy_config_id)`.

**`team_data`**: per-team level for the legacy `/api/team` branch: `id`,
`team_id` FK unique, `defence_placement_data` JSONB, `enemies` JSONB,
`spawn_settings` JSONB, `game_settings` JSONB, `created_at`.

**`results_log`**: `id`, `data` JSONB (the raw `/api/results` payload as
parsed), `received_at`. Every result is appended here, whatever the flow.

## 9. In-memory state (single worker only)

| Store | File | Key | Content | Cleared |
|---|---|---|---|---|
| `_SESSION_INITIAL_COST` | `reinforcement_service.py` | sessionID | cost of the level's starting defences | `/api/results` |
| `_SESSION_INITIAL_DEFENCES` | `reinforcement_service.py` | sessionID | the starting defences (Turn 0) | `/api/results` |
| `_SESSION_REINFORCEMENTS` | `reinforcement_service.py` | sessionID, else teamID | every approved reinforcement | `/api/results` |
| `_SESSIONS` | `match_tracker.py` | sessionID | `{"defences": {id: {type, cell, health}}, "enemies": {id: {type, cell, dead}}, "seen", "pauses"}` | `/api/results`, or after 6 h idle (pruned at most once a minute) |
| `_VERIFIED` | `accounts.py` | hash of (stored hash, password) | passed password checks | at 20 000 entries |
| `manager` | `ws_manager.py` | run id | open WebSockets | on disconnect |
| `_slots` | `orchestrator.py` | – | semaphore of `MAX_CONCURRENT_RUNS` | – |

`session_placements(sid)` = Turn 0 defences + approved reinforcements: this is
what ends up in `game_sessions.placements` and is used to complete and score
the report.

## 10. Route reference (all routes)

"Public" = reachable through Caddy in the event deployment
(`/backend/api/*`, `/backend/health`, `/backend/register`, `/backend/admin`,
`/backend/admin/*`; `/register` and `/admin` redirect there). Everything else
is internal (reachable only inside the compose network or on localhost).

Error bodies: FastAPI routes return `{"detail": "..."}`; `/api/team` returns
`{"error": "..."}` (the shape the Unity game shows).

### 10.1 Summary table

| Method | Path | Auth | Router file | Public | Purpose |
|---|---|---|---|---|---|
| GET | `/health` | – | `main.py` | yes | liveness |
| POST | `/api/login` | – | `competition.py` | yes | password login → token |
| GET | `/api/registration/form` | – | `competition.py` | yes | registration questions |
| POST | `/api/register` | – | `competition.py` | yes | create a team |
| GET | `/api/me` | Bearer | `competition.py` | yes | the team's own stats |
| GET | `/api/test-cases` | – | `competition.py` | yes | public active test cases |
| GET | `/api/eval/info` | – | `competition.py` | yes | number of hidden cases |
| POST | `/api/eval/start` | Bearer | `competition.py` | yes | start an eval run |
| GET | `/api/eval/{eval_id}` | Bearer | `competition.py` | yes | eval progress |
| POST | `/api/eval/{eval_id}/finish` | Bearer | `competition.py` | yes | close an eval, upload the log |
| GET | `/register` | – | `competition.py` | yes | `static/register.html` |
| GET | `/admin` | – (page) | `competition.py` | yes | `static/admin.html` |
| GET | `/admin/api/overview` | Admin | `competition.py` | yes | counts |
| GET | `/admin/api/teams` | Admin | `competition.py` | yes | every team + stats |
| POST | `/admin/api/teams` | Admin | `competition.py` | yes | create a team |
| GET | `/admin/api/teams/{team_id}` | Admin | `competition.py` | yes | one team, its games and evals |
| PATCH | `/admin/api/teams/{team_id}` | Admin | `competition.py` | yes | edit team |
| DELETE | `/admin/api/teams/{team_id}` | Admin | `competition.py` | yes | delete team (cascade) |
| GET | `/admin/api/sessions/{session_id}` | Admin | `competition.py` | yes | one game in full |
| GET | `/admin/api/evals` | Admin | `competition.py` | yes | latest 500 eval runs |
| GET | `/admin/api/evals/{eval_id}` | Admin | `competition.py` | yes | one eval + source + games |
| GET | `/admin/api/evals/{eval_id}/log` | Admin | `competition.py` | yes | download the `.jsonl.gz` log |
| GET | `/admin/api/test-cases` | Admin | `competition.py` | yes | all test cases |
| POST | `/admin/api/test-cases` | Admin | `competition.py` | yes | add a test case |
| PUT | `/admin/api/test-cases/{code}` | Admin | `competition.py` | yes | edit a test case |
| DELETE | `/admin/api/test-cases/{code}` | Admin | `competition.py` | yes | delete a test case |
| GET | `/admin/api/registration-form` | Admin | `competition.py` | yes | the full form |
| PUT | `/admin/api/registration-form` | Admin | `competition.py` | yes | replace the form |
| POST | `/api/team` | optional Bearer | `game_api.py` | yes | start a game, get the level |
| POST | `/api/reinforcements` | – | `game_api.py` | yes | pause: referee the agent's placements |
| POST | `/api/state` | – | `game_api.py` | yes | pause snapshot with no placements |
| POST | `/api/screenshot` | – | `game_api.py` | yes | accepted and discarded |
| POST | `/api/results` | – | `game_api.py` | yes | end of game: complete, score, store |
| POST | `/api/team-data/{team_name}` | Admin | `game_api.py` | yes (under `/api/`) | legacy: upsert a team's level |
| GET | `/api/team-data` | Admin | `game_api.py` | yes (under `/api/`) | legacy: list team levels |
| POST | `/teams` | – | `submissions.py` | no | legacy: create a team |
| POST | `/submissions` | – | `submissions.py` | no | legacy: upload agent.py |
| GET | `/submissions/{id}` | – | `submissions.py` | no | legacy: submission status |
| POST | `/submissions/{id}/retry` | Admin | `submissions.py` | no | legacy: retry failed runs |
| GET | `/runs/{id}` | – | `runs.py` | no | legacy: run state |
| POST | `/runs/{id}/result` | Simulator | `runs.py` | no | legacy: simulator result webhook |
| WS | `/runs/{id}/stream` | – | `websocket.py` | no | legacy: live run events |
| GET | `/enemy_configs` | – | `enemy_configs.py` | no | legacy: 50 configs |
| GET | `/enemy_configs/{id}` | – | `enemy_configs.py` | no | legacy: one config |
| POST | `/enemy_configs` | Admin | `enemy_configs.py` | no | legacy: add a config |
| GET | `/leaderboard` | – | `leaderboard.py` | no | legacy: best submission per team |

FastAPI's `/docs` and `/openapi.json` are also served (not exposed by Caddy).

### 10.2 Competition: participants (`routers/competition.py`)

**`POST /api/login`**
- Body `LoginIn`: `{"teamID": str 1-20, "password": str 1-200}`. `teamID`
  accepts `0042`, `42` or `team42`.
- 200 `{"token", "teamID": "0042", "teamName", "expiresIn": 86400, "llmToken": str|null}`.
- 401 `Wrong team ID or password` (unknown team, no password set, wrong password);
  403 `This team is banned from the competition`; 403
  `Your registration is waiting for the organisers' approval`.

**`GET /api/registration/form`** → `{"title", "description", "open", "questions"}`
(stored form merged over `DEFAULT_FORM`; `autoApprove` is not exposed).

**`POST /api/register`** (201)
- Body `RegisterIn`: `{"teamName": 2-60 chars, "password": 8-200 chars, "answers": {questionId: value}}`.
- 403 if the form is closed; 409 if the name is taken (case-insensitive,
  whitespace collapsed); 422 from `check_answers` (missing required answer,
  bad email, not a number, value not in `select` options). Strings are trimmed
  and cut to 2000 chars; unknown answer keys are dropped.
- Creates the team with `approved = form.autoApprove`, `profile.answers`.
- 201 body: `{"teamID": "0042", "teamName", "approved"}`.

**`GET /api/me`** (Bearer) → `{"teamID", "teamName", "approved", "banned", ...stats}`
where stats (`_team_stats`) = `gamesPlayed` (finished `play` games),
`gamesStarted`, `bestPlayScores` `{caseCode: best}`, `evals`, `bestEval`,
`lastEval`, `lastActive`.

**`GET /api/test-cases`** → `[{"code", "name", "enemies": total count, "teamID"?}]`
for active, non-hidden cases ordered by `position, code`. `teamID` is added
when a team exists whose name equals the code (old SDKs played test cases as
teams seeded by `tools/seed_levels.py`).

**`GET /api/eval/info`** → `{"hiddenTestCases": n}` (active hidden cases).

**`POST /api/eval/start`** (Bearer, 201)
- Body `{"agentSource": str, "sdkVersion": str|null}`; 413 if source > 512 KiB;
  403 if banned; 409 `No hidden test cases are set up yet`.
- Every `running` eval of the team becomes `abandoned`.
- Creates `EvalRun(status="running", case_codes=[active hidden codes in order], agent_source, agent_sha256, sdk_version)`.
- 201 `{"evalId", "slots": n, "agentSha256"}`.

**`GET /api/eval/{eval_id}`** (Bearer, own runs only else 404) →
`{"evalId", "status", "score", "slots": [{"slot", "played", "score", "won"}]}`.

**`POST /api/eval/{eval_id}/finish`** (Bearer)
- Body `{"log": base64(gzip(jsonl))|null, "summary": {}}`.
- Only acts while `status == "running"`; otherwise returns the current state.
- Log checks: valid base64 + gzip (422 otherwise), ≤ 20 MiB compressed and
  ≤ 200 MiB decompressed (413). Saved to `UPLOAD_DIR/eval_logs/eval{id}_team{team}.jsonl.gz`.
- `score = round(mean(slot.score or 0), 2)`; `status = finished` if every slot
  was played, else `incomplete`; stores `summary`, `finished_at`.
- Returns `{"evalId", "status", "score", "slots"}`.

**`GET /register`**, **`GET /admin`**: serve `app/static/register.html` and
`app/static/admin.html` (not in the OpenAPI schema). The admin page asks for
the admin secret, keeps it in `sessionStorage` (`apAdmin`) and calls
`/admin/api/*` relative to its own URL.

### 10.3 Competition: organisers (`/admin/api/*`, header `X-Admin-Secret`)

| Route | Body / reply |
|---|---|
| `GET /overview` | `{"teams", "pending" (not approved, not banned), "banned", "games" (finished sessions), "evals", "publicCases", "hiddenCases"}` |
| `GET /teams` | `[team row + stats]`; team row = `{"id", "teamID", "name", "approved", "banned", "hasPassword", "answers", "notes", "createdAt"}` |
| `POST /teams` (201) | `TeamIn {"name" (required), "password"?, "approved"? (default true), "banned"?, "answers"?, "notes"?}`; 409 name taken |
| `GET /teams/{id}` | team row + stats + `"games": [game row]` (newest first) + `"evalRuns": [eval row + slots]` |
| `PATCH /teams/{id}` | any `TeamIn` field; a new password is re-hashed; `answers`/`notes` merge into `profile` |
| `DELETE /teams/{id}` (204) | deletes the team and (cascade) everything it played |
| `GET /sessions/{session_id}` | game row + `"result"`, `"evaluatorOutput"`, `"placementList"` |
| `GET /evals` | latest 500 eval rows `{"evalId", "teamID", "status", "score", "cases", "agentSha256", "sdkVersion", "hasLog", "summary", "startedAt", "finishedAt"}` |
| `GET /evals/{id}` | eval row + `"agentSource"`, `"slots"`, `"games"` |
| `GET /evals/{id}/log` | file download `application/gzip`; 404 if none |
| `GET /test-cases` | all cases ordered hidden, position, code: `{"code", "name", "hidden", "active", "position", "level", "enemies", "updatedAt"}` |
| `POST /test-cases` (201) | `CaseIn {"code", "name"?, "hidden"?, "active"?, "position"?, "level"}`; code upper-cased; 409 if exists; level via `normalise_level` |
| `PUT /test-cases/{code}` | any `CaseIn` field (renaming checks for clashes) |
| `DELETE /test-cases/{code}` (204) | past games keep their scores; an eval that still points to it gets 410 for that slot |
| `GET /registration-form` | the full stored form (with `autoApprove`) |
| `PUT /registration-form` | `{"title", "description", "open", "autoApprove", "questions": [{"id", "label", "type", "required", "options", "help"}]}`; validated by `validate_form` (unique ids, not `teamName`/`password`, type in text/textarea/email/number/url/select/checkbox) |

Game row (`_game_row`): `{"sessionID", "testCase", "mode", "evalId", "evalSlot", "score", "won", "pauses", "placements" (count), "agentSha256", "killed", "enemies", "duration", "startedAt", "finishedAt"}`.

`normalise_level(level)`: `enemies` must be a non-empty list of dicts with
`enemyId` and `count >= 1` (422 otherwise); `spawnSettings`/`gameSettings`
are merged over the schema defaults; `defencePlacementData` defaults to `[]`.

### 10.4 Game API (`routers/game_api.py`)

These are the calls the Unity game makes (through the SDK proxy). Details of
the processing are in §11, §13 and §15.

**`POST /api/team`** → `TeamDataOut`
- Body `ApiTeamRequest`: `{"teamID": str, "configId"?, "testCaseId"?, "testCase"?, "evalId"?, "evalSlot"?, "agentSha256"?}`.
  The game sends only `teamID`; the SDK adds `testCase` (play) or
  `evalId`+`evalSlot` (eval) and `agentSha256`.
- Header `Authorization: Bearer <token>` optional; if valid, `teamID` is replaced
  by the logged-in team's code.
- Reply:
  ```json
  {
    "sessionID": "c1a2b3c4d5e6f7a8",
    "defencePlacementData": [],
    "enemies": [{"enemyId": 0, "count": 2, "allowedRows": [1, 2, 3]}],
    "spawnSettings": {"interArrivalTime": 6.0, "timeDecreaseRate": 0.25, "minInterArrivalTime": 1.0},
    "gameSettings": {"reinforcementsEnabled": true, "reinforcementIntervalSeconds": 3.0,
                     "reinforcementHoldSeconds": 0.2, "sendScreenshotToo": false},
    "budget": 3000.0, "matchBudget": 3600.0, "unitCosts": {"0": 250, "1": 200, "2": 300, "3": 150},
    "bountyPerKill": 50.0, "coins": 3000
  }
  ```
- Errors as `{"error": ...}`: 404 team not found / no data (legacy) / unknown
  test case / unknown eval run; 403 banned; 401 login needed; 409 eval not
  running or slot already played; 410 hidden case removed; 422 bad slot.

**`POST /api/reinforcements`** → `ReinforcementResponse`
- Body `ReinforcementRequest` (extra fields allowed):
  ```json
  {
    "teamID": "0042", "sessionID": "c1a2...", "elapsedSeconds": 12.0,
    "counts": {"activeDefenceCount": 1, "activeEnemyCount": 2, "enemiesQueued": 8, "totalEnemiesInWave": 12},
    "defences": [{"id": "defenceSoilder_0", "type": "defenceSoilder", "health": 90, "maxHealth": 100, "position": {"x": 1, "y": 1}}],
    "enemies":  [{"id": "EnemClown_0", "type": "EnemClown", "health": 50, "maxHealth": 100, "position": {"x": 6.4, "y": 1}, "isAttacking": false, "isDead": false}],
    "candidatePlacements": [{"defenceId": 1, "defenceGridPosition": {"x": 4, "y": 2}}],
    "budget": null, "spent": null, "remainingBudget": null, "matchBudget": null, "unlockedBudget": null
  }
  ```
  Unit positions that are floats are rounded to the nearest cell.
  `candidatePlacements` uses `DefencePlacement`, whose schema already limits
  `x` 0-10, `y` 0-5 and `defenceId >= 0`: an out-of-range candidate makes the
  whole request 422 rather than a "rejected" entry.
- Reply:
  ```json
  {
    "placeDefences": true,
    "defencePlacementData": [{"defenceId": 1, "defenceGridPosition": {"x": 4, "y": 2}}],
    "budget": {"unlocked": 3050.0, "spent": 400.0, "remaining": 2650.0, "total": 3600.0},
    "rejected": [{"defenceId": 1, "defenceGridPosition": {"x": 1, "y": 1}, "reason": "cell (1,1) already has a defence"}],
    "coins": 2650
  }
  ```
  `budget`/`rejected`/`coins` are for the SDK and agent; the game ignores
  fields it does not know (build7 uses `coins` for its counter).

**`POST /api/state`** → `{"status": "ok"}`. Same body as reinforcements; only
recorded by `match_tracker` (the SDK sends it when the agent proposes nothing,
so the backend still sees every unit).

**`POST /api/screenshot`** (multipart `screenshot` file, form `teamID`,
`sessionID`) → `{"status": "ok", "bytes": n}`. Discarded.

**`POST /api/results`**
- Body `UnityMatchResult`: `teamID` (required), `sessionID` (default
  `"Not Implemented Yet"`), `success`, `durationSeconds` (required),
  `enemiesKilled`, `defencesDestroyed`, `totalDefenceDamage`,
  `totalEnemyDamage`, `totalDefenceCost`, `totalCost`, `defences[]`
  (`id, type, position, cost, damageDealt, destroyed, destroyedAtTime`),
  `enemies[]` (`id, type, damageDealt, killed, killedAtTime`),
  `defenceTypeStats[]`, `enemyTypeStats[]`. Unknown fields are dropped.
- Reply: `{"status": "ok", "stored": <results_log row count>, "evaluator_output": {...}, "report": {"reportedDefences", "reportedEnemies", "addedDefences", "addedEnemies"}, "testCase"?: code (play only), "evalSlot"?: n, "score"?: float}`.
  `score` is absent when the result failed validation (`evaluator_output` then
  is `{"valid": false, "error": "..."}`).

**`POST /api/team-data/{team_name}`** (admin) and **`GET /api/team-data`**
(admin): legacy per-team levels in `team_data`. Upsert body `TeamDataUpsert`
`{"defencePlacementData": [], "enemies": [...] (min 1), "spawnSettings"?, "gameSettings"?}`;
the team must exist; `sendScreenshotToo` forced false. The list returns
`[{"teamName", "teamId", "defencePlacementData", "enemies", "spawnSettings", "gameSettings", "createdAt"}]`.

### 10.5 Legacy routes

See §16 for the processing.

| Route | Body / reply |
|---|---|
| `POST /teams` (201) | `{"name"}` → `{"id", "name", "created_at"}`; also creates a default `team_data` row; 409 duplicate |
| `POST /submissions` (201) | multipart `agent_file` + form `team_id` → `SubmissionOut {"id", "team_id", "status", "total_score", "created_at", "runs": [{"id", "enemy_config_id", "status", "score"}]}`; 404 team; 503 no enemy configs |
| `GET /submissions/{id}` | `SubmissionOut` |
| `POST /submissions/{id}/retry` (admin) | resets `failed` runs to `pending` and re-runs them; 409 if none |
| `GET /runs/{id}` | `RunOut {"id", "submission_id", "enemy_config_id", "status", "sdk_status", "defence_placement", "evaluator_output", "score", "created_at"}` |
| `POST /runs/{id}/result` (simulator) | `{"result": {...}}` → `RunOut`; 404 unknown; 409 unless `simulating`; 502 evaluator exception |
| `WS /runs/{id}/stream` | on connect: `status_update` (+ `result` if completed); then `status_update` / `result` / `error` events `{"event", "data"}`; text `ping` → `pong`; close 4404 if run unknown |
| `GET /enemy_configs`, `GET /enemy_configs/{id}` | `{"id", "name", "enemies", "budget"}` |
| `POST /enemy_configs` (admin, 201) | `EnemyConfigCreate {"name", "enemies" (unique enemyId), "budget"?}` (`budget` is accepted but not saved) |
| `GET /leaderboard?limit=50` (1-200) | `[{"rank", "team_id", "team_name", "submission_id", "score", "created_at"}]`: best scored submission (status completed or pending) per team, dense rank, ties to the earlier submission |

There is **no leaderboard endpoint for the competition flow** (eval runs). The
admin dashboard shows `bestEval`/`lastEval` per team; `COMPETITION.md` leaves the
ranking rule to the organisers.

## 11. Live game flow, step by step

What the backend does for one game. The SDK's side is in
[SDK_GUIDE.md](SDK_GUIDE.md).

```
SDK                                   backend
 │ POST /api/login {teamID,password}   ─▶ check password/banned/approved → token, llmToken
 │ POST /api/team {teamID, testCase |   ─▶ resolve team → competition.start_session
 │      evalId+evalSlot, agentSha256}      → GameSession row (session "c…")
 │      + Bearer token                     → level_reply: level + budget + coins
 │                                         → ledger: Turn 0 defences/cost
 │ POST /api/reinforcements (opening    ─▶ match_tracker (snapshots with t > 0)
 │      and each pause, with candidates)   → referee: budget, validation, ledger
 │ POST /api/state (pause, no candidates)─▶ match_tracker only
 │ POST /api/results                    ─▶ results_log row → complete report → evaluate
 │                                         → GameSession result/score → clear memory
```

### 11.1 `POST /api/team` in detail (`game_api.get_team_data`)

1. `bearer_team(Authorization)`; if valid, `body.teamID = "%04d" % team`.
2. Compound IDs (legacy): `"3:5"` → team `3`, config `5`; `"team3_wave5"`
   (regex `^(.*?)(?:_test|_config|_wave|_tc|_case|_)?(\d+)$`) → team `team3`,
   config `5`. `configId`/`testCaseId` in the body also set the config.
3. Resolve the team (§7.1). 404 `{"error": "Team '<id>' not found"}`; 403 if banned.
4. **Competition branch** (`testCase` or `evalId` present):
   - `REQUIRE_LOGIN` and token team ≠ resolved team → 401.
   - `competition.start_session(db, team, body, logged_in)`:
     - **eval**: token team must equal the team (401); eval run exists and
       belongs to the team (404); `status == "running"` (409); `1 <= evalSlot <= len(case_codes)`
       (422); slot not already finished (409); the case still exists (410).
     - **play**: the case exists, is not hidden and is active (404).
     - Inserts `GameSession(session_id="c"+15 hex, team_id, test_case, mode, eval_id, eval_slot, agent_sha256)` and commits.
     - `level_reply(...)` builds `TeamDataOut`:
       spawn/game settings merged over defaults; `sendScreenshotToo=false`;
       `reinforcementIntervalSeconds` capped at 3.0 (`MAX_INTERVAL_S`) so the
       first pause comes at the start of the battle; missing `allowedRows` →
       `[0, 1, 2]`; `matchBudget = calculate_test_case_budget(None, wave_size)`;
       `budget` = unlocked budget at 0 kills; `coins = max(0, budget - cost(starting defences))`;
       `record_session_initial_defences(session_id, defences)`.
   - `HTTPException`s become `{"error": detail}` with the same status.
   - `team.approved` is **not** checked here: an unapproved team can `play`
     without a login unless `REQUIRE_LOGIN` is true (login itself refuses
     unapproved teams).
5. **Legacy branch** (no `testCase`/`evalId`; old game builds, local dev):
   - Needs a `team_data` row (404 otherwise). Enemies = `team_data.enemies`, or
     the `EnemyConfig` named by the config id.
   - Looks for the team's latest submission and its run for that config.
   - If an uploaded agent exists in `UPLOAD_DIR` (`team{id}_*.py`,
     `team_{id}_*.py`, `team_{id}.py`, or `agent.py`; newest mtime wins), calls
     SDK `POST /run` to get fresh starting placements. Falls back to that run's
     placement, then the team's latest run with a placement, then `[]`.
     Placements equal to `DEFAULT_DEFENCES` (now empty) are cleared.
   - If a run matched: run → `simulating` with these defences, `sessionID = str(run.id)`.
     Else `sessionID` = the team's latest `simulating` run id, or `"p"` + 11 hex.
   - Same settings/budget/coins logic as `level_reply`, using
     `EnemyConfig.budget` when present; logs `[INCOME CHECK]` lines.

### 11.2 Pauses

- The game's first `/api/reinforcements` call, as the level starts, is the
  agent's opening (`phase="start"` on the SDK side); later calls are the timed
  pauses. The SDK forwards a call to `POST /api/reinforcements` when the agent
  proposed placements (in `candidatePlacements`) and sends `POST /api/state`
  when it proposed nothing.
- Both call `match_tracker.record_snapshot(payload)`: ignored if no
  `sessionID` or `elapsedSeconds <= 0` (a guard from an older SDK that made a
  synthetic t=0 opening call); otherwise remembers every defence
  (`type`, `cell`, `health`) and enemy (`type`, `cell`, `dead` once seen dead)
  by unit id and increments `pauses`.
- `/api/reinforcements` then runs the referee (§13), sets
  `coins = int(budget.remaining)`, and, only in the legacy case where the
  session maps to a run, appends approved placements to `run.defence_placement`.

### 11.3 `POST /api/results` in detail (`game_api.push_results`)

1. Append the parsed payload to `results_log`.
2. Legacy correlation: `run` = Run with id `db_id(sessionID)`, else the team's
   latest `simulating`/`pending` run.
3. `expected_defences` = `run.defence_placement`, else
   `session_placements(sessionID)` (everything the referee approved).
4. Legacy budget/wave: from the run's `EnemyConfig` (`budget`, total count),
   else from the team's `team_data` enemies.
5. `competition.session_wave(db, sessionID)`: for a competition session, the
   wave size of its test case overrides the expected enemy count.
6. `match_tracker.complete_results(sessionID, payload, session_placements)`
   adds units the game left out (§15.1) → `payload_dict`, `report_note`.
7. `evaluator_service.evaluate(payload_dict, expected_defences, test_case_budget, expected_total_enemies)`
   → `(evaluator_output, score)` (§15.2).
8. Legacy: if a run matched, keep the best score (`completed`), or set
   `invalid` with 0 if unscored and it had no score; `finalize_submission`;
   WebSocket `result` broadcast.
9. `competition.record_result(...)`: for a competition session, stores
   `result`, `evaluator_output`, `score` (0.0 if `None`), `success`, `pauses`,
   `placements`, `finished_at`. If the session already has a result, the
   first one is kept and a warning logged.
10. `clear_session_ledger(sessionID)` and `match_tracker.forget(sessionID)`.
11. Reply (§10.4).

## 12. Eval flow

1. SDK `GET /api/eval/info` (how many hidden cases) and, with the login token,
   `POST /api/eval/start {agentSource, sdkVersion}` → `{evalId, slots, agentSha256}`.
   Older running evals of the team become `abandoned`. The hidden case list is
   frozen in `case_codes` at this moment.
2. For slot 1..N the SDK sends `POST /api/team {teamID, evalId, evalSlot, agentSha256}`
   + Bearer. Each slot can finish only once (409 on a replay). Each game then
   runs like §11 and its result lands in a `game_sessions` row with
   `mode="eval"`, `eval_id`, `eval_slot`.
3. `GET /api/eval/{id}` shows per-slot progress at any time.
4. `POST /api/eval/{id}/finish {log, summary}` stores the log, computes the mean
   (unplayed slots count 0) and sets `finished` or `incomplete`.

Admin views: `/admin/api/evals`, `/admin/api/evals/{id}` (with `agentSource`
for re-running top teams on organiser hardware), `/admin/api/evals/{id}/log`.

## 13. The referee (`/api/reinforcements`)

`services/reinforcement_service.decide_reinforcements(snapshot, db)`:

1. **Wave over**: if `activeEnemyCount <= 0` and `enemiesQueued <= 0` →
   `{"placeDefences": false, "defencePlacementData": []}` (no budget info).
2. **Legacy lookup** (wrapped in try/except): run by numeric sessionID or the
   team's `simulating` run → its `EnemyConfig` (for `budget`) and the team id
   used to query the SDK service.
3. **Budget** (§14):
   - `total_wave = counts.totalEnemiesInWave` (or the config's wave size if 0).
   - `match_budget = calculate_test_case_budget(config.budget, total_wave)`.
   - `killed = max(total_wave - enemiesQueued - activeEnemyCount, number of isDead enemies)`.
   - `unlocked = calculate_unlocked_budget(match_budget, killed)`.
4. **Spent so far** (`session_key = sessionID or teamID`):
   - session has a Turn 0 record → `initial cost + cost of approved reinforcements`;
   - else legacy run with a placement → `max(run placement cost, living board cost + reinforcements)`;
   - else `living board cost + reinforcements`.
   Destroyed units are never refunded. `remaining = max(0, unlocked - spent)`.
5. The snapshot's own budget fields are **overwritten** with these values: the
   client's numbers are never trusted.
6. **Candidates** = `snapshot.candidatePlacements`; if empty, legacy fallback:
   `sdk_service.query_reinforcements(team, snapshot)` (SDK serve,
   `POST /reinforcements`, 4.5 s, `[]` on any failure).
7. No candidates → `placeDefences=false` + `budget`.
8. `validate_reinforcements(candidates, snapshot, rejected)`, in order, per
   candidate (first failure wins, no nudging to another cell):
   | Check | Rejection reason |
   |---|---|
   | dict parses as `DefencePlacement` | `not a valid placement (needs defenceId and defenceGridPosition x/y)` |
   | `0 <= x <= 10`, `0 <= y <= 5` | `off the board: (x,y) is outside x 0-10, y 0-5` |
   | `defenceId in {0,1,2,3}` | `unknown defenceId N (use 0-3)` |
   | cell not held by a living defence (`health > 0`) | `cell (x,y) already has a defence` |
   | cell not used earlier in the same batch | `cell (x,y) is used twice in this batch` |
   | `spent + cost <= unlocked` | `over budget: costs C, only R left` |
   Approved placements add their cost to `spent` and mark the cell occupied,
   so later candidates in the batch see them.
9. Approved placements are appended to `_SESSION_REINFORCEMENTS[session_key]`.
   Reply `placeDefences=true`, the approved list, `budget` (`unlocked`, new
   `spent`, `remaining`, `total=match_budget`) and `rejected` (or null).
   If none were approved: `placeDefences=false`, `budget`, `rejected`.

The referee never invents placements: the agent alone decides.

## 14. Economy: budget, coins, bounty

All in `services/evaluator_service.py`:

| Constant / function | Value / rule |
|---|---|
| `STARTING_COINS` | `AUTOPILOT_STARTING_COINS` env, default 3000 |
| `BOUNTY_PER_KILL` | 50 |
| `DEFENCE_COST_MAP` / `unit_costs()` | `0` penguin 250, `1` soldier ("soilder") 200, `2` archer 300, `3` shield 150 |
| `get_unit_cost(unit)` | explicit `cost` > 0 → type name → id prefix (`defencePenguin_0`, `(Clone)` stripped) → `defenceId` → default 200 |
| `wave_size(entries)` | sum of `count` |
| `calculate_test_case_budget(tc_budget, wave)` | `max(STARTING_COINS + max(1, wave) × 50, tc_budget or 0)`: the match budget |
| `calculate_unlocked_budget(match, killed)` | `min(match, STARTING_COINS + killed × 50)`: spendable so far |

Example: 12-enemy test case → match budget 3600, opening budget 3000, and
every kill unlocks 50 more up to 3600. Test-case levels never set a budget
override (`level_reply` passes `None`); legacy `EnemyConfig.budget` can raise
it.

Defence type names used by the game and `match_tracker.DEFENCE_TYPES`:
`0 defencePenguin`, `1 defenceSoilder`, `2 defenceArcher`, `3 defenceShield`.
Board: 11 columns (x 0-10) × 6 rows (y 0-5).

## 15. Result completion and scoring

### 15.1 Completing the report (`match_tracker.complete_results`)

The game's report lists only units that dealt or took damage. Before scoring:

- **Defences**: add every defence seen in a snapshot but missing from the report
  (`damageDealt 0`, `destroyed false`), then every approved placement whose
  cell is still not covered (placed after the last snapshot), as
  `placed_<x>_<y>`.
- **Enemies**: add every enemy seen in a snapshot but missing, `killed` if any
  snapshot saw it dead.
- Recompute `defencesDestroyed` (count of `destroyed`), set
  `enemiesKilled = max(reported, killed in list)`, and **drop**
  `defenceTypeStats`/`enemyTypeStats` (they were built from the incomplete
  lists; the scorer falls back to the lists).
- Returns the note `{"reportedDefences", "reportedEnemies", "addedDefences", "addedEnemies"}`.

### 15.2 Validation (`validate_simulation_result`)

After `normalize_simulation_payload` (maps `won`→`success`, `killed`→`enemiesKilled`,
`teamId`/`team_id`→`teamID`, `engine=="portable"`→`teamID="PORTABLE_SIMULATOR"`,
`defencesPlaced - defencesAlive`→`defencesDestroyed`), the result is invalid if:

- empty; `teamID` missing/empty; `success` missing or not a bool;
- `durationSeconds` missing, not a number, NaN/inf, or `<= 0.05` (crash);
- `enemiesKilled` / `defencesDestroyed` negative, bool or NaN;
- `totalDefenceDamage` / `totalEnemyDamage` negative or not a number;
- `defences`, `enemies`, `defenceTypeStats`, `enemyTypeStats` not lists;
- `defencesDestroyed > max(len(defences), type stat count, defencesPlaced, len(expected))`;
- `enemiesKilled > max(len(enemies), type stat count, totalEnemies)`.

Invalid → `({"valid": false, "error": msg}, None)`; the game session still
gets a row with score 0.

### 15.3 Score (`calculate_score`, 0-100)

Derived counts:
- `total_enemies = max(len(enemies), enemiesKilled, enemyTypeStats count, totalEnemies, expected_total_enemies, 1)`.
- `defences_placed = max(len(defences), defenceTypeStats count, len(expected_defences), defencesPlaced)`.
- `survived` = non-destroyed in the list if the list is complete, else `placed - destroyed`.
- Victory = `success` (if `success` is absent: inferred from all enemies killed).

| Part | Max | Rule |
|---|---|---|
| Outcome | 25 | 25 victory, 5 defeat |
| Kill efficiency | 25 | `25 × killed / total_enemies` |
| Defence survival | 25 | `25 × survived / placed × cleared` where `cleared = 1` on a win, else the kill ratio; 0 if nothing placed |
| Speed bonus | 10 | win only. Par 120 s, max 180 s: `t <= 120` → `5 + 5 × (120 - t) / 120`; else `5 × max(0, 180 - t) / 60` |
| Economy | 15 | nothing placed → 0. `cost <= budget` → `(10 + 5 × (budget - cost) / budget) × cleared`. Over budget → `-50 × (cost - budget) / budget` (negative) |

`final = clamp(0, 100, sum)` rounded to 2 decimals. `budget =
calculate_test_case_budget(test_case_budget, total_enemies)`. `cost` =
the report's `totalDefenceCost`/`totalCost` if > 0 (trusted as sent), else
`max(sum of listed defence costs, type-stat costs, expected placement costs)`,
else `placed × 100`.

Output: `{"valid": true, "score", "breakdown": {"outcome_score", "kill_score", "survival_score", "speed_bonus", "cost_score"}, "stats": {...}}`.
`stats` keys: `team_id`, `session_id`, `outcome`, `victory_inferred`,
`duration_seconds`, `enemies_killed`, `total_enemies`, `bounty_earned`,
`unlocked_budget`, `defences_placed`, `defences_survived`,
`defences_destroyed`, `total_defence_cost`, `test_case_budget`, `savings`,
`overspent`, `cost_status` (`UNDER_BUDGET`/`OVER_BUDGET`), `damage_per_cost`,
`total_defence_damage`, `total_enemy_damage`, `defence_type_stats`,
`enemy_type_stats`.

## 16. Legacy pipeline: submissions, runs, simulator

Still mounted and covered by tests, used by the local `make start` stack
(`integration/`), not exposed in the event deployment.

```
POST /submissions (agent_file, team_id)
  └─ file saved to UPLOAD_DIR/team{id}_<hex>.py
  └─ 1 Submission + 1 Run (pending) per EnemyConfig (50 seeded)
  └─ background: orchestrator.run_submission
       for each run (≤ MAX_CONCURRENT_RUNS at once):
         SDK POST /run {agentFileContent: base64, enemies}
           ValidationError     → run invalid (schema violation)
           any other error     → run failed
           sdkStatus.valid=false or no placement → run invalid
           duplicate cell      → run invalid
           valid + ENABLE_HEADLESS_SIMULATOR=false (default) → run pending
                                  (waits for an interactive Unity game via /api/team)
           valid + headless on → run simulating → simulator POST /start {runId, teamId, enemies, defencePlacementData}
         finalize_submission after each run
simulator ─▶ POST /runs/{id}/result {result} (X-Simulator-Secret)
  └─ must be simulating (409 otherwise)
  └─ budget: EnemyConfig.budget, else max(250, min(850, 150 + 30 × enemies))
     (calculate_test_case_budget's max() makes the starting-coin budget win anyway)
  └─ evaluate → invalid (score 0) or completed (score)
  └─ finalize_submission → WebSocket result
```

`scoring.finalize_submission` (idempotent): `total_score` = mean over runs that
are `completed` (their score) or `invalid` (0); once every run is terminal, any
`failed` run → submission `failed` with no score (admin retries), else
`completed`.

The simulator adapter at `SIMULATOR_URL` is `integration/service.py` (port
8002 locally): it queues jobs for native/WebGL Unity workers
(`/unity/jobs/next`, `/frame`, `/result`) or a portable Python battle, and posts
`{"result": {"engine", "totalEnemies", "killed", "escaped", "defencesPlaced", "defencesAlive", "durationSeconds", "won", ...}}`
back to `/runs/{id}/result`. It also has its own `/evaluate` (development
formula `80 × kill + 20 × survival`), which the backend does not call.

## 17. Deployment, tests, quirks, how-to

### 17.1 Event deployment

- Root `docker-compose.yml`: `db` (Postgres 16, `max_connections=200`),
  `backend` (image `deploy/Dockerfile`, workdir `/app/autopilot-backend`,
  `uvicorn app.main:app --port 8010 --workers 1 --timeout-keep-alive 30 --no-access-log`,
  volume `uploads:/data/uploads`, health check `/health`), `gateway`
  (`integration.gateway:app`, port 8004), `web` (Caddy with the game, downloads
  and routes), `tunnel` (profile `tunnel`, Cloudflare quick tunnel), `seed`
  (profile `tools`).
- `deploy/Caddyfile` public routes: `/backend/api/*`, `/backend/health`,
  `/backend/register`, `/backend/admin`, `/backend/admin/*` (prefix stripped →
  `backend:8010`); `/register` and `/admin` redirect; `/llm/v1/*` → gateway
  `/v1/*`; `/download/*` file browser; everything else = game files (`build7`).
- `deploy/server-update.sh` (run by `deploy/push-to-server.sh` and the
  `deploy.yml` workflow on every push to `test_branch`): build, `up -d`, wait
  for `/health`, run `seed` (`tools/seed_levels.py --team TEAM --test-cases ...`),
  print the tunnel URL.
- `tools/seed_levels.py` uses the **legacy** routes (`POST /teams`,
  `POST /api/team-data/{name}`) to create teams `TEAM` and `TC01`..`TC10` with
  those levels. These teams are why `/api/test-cases` can add a `teamID` per case.

### 17.2 Tests (`autopilot-backend/tests/`, run with `make test`, `PYTHONPATH=autopilot-backend`)

| File | Covers |
|---|---|
| `test_competition.py` | password hashing and cache, token expiry/forgery, LLM token = gateway token, form/answer validation, level normalisation, shipped test cases load as TC01-10 + H* |
| `test_reinforcements.py` | referee rules (occupied, bounds, id, duplicates, budget, partial batch, unlocked ceiling, cumulative spend), bounty/budget maths, session placements |
| `test_match_tracker.py` | report completion from snapshots, dead enemies, opening call ignored, forget, cleared-ratio scoring, `db_id` int32 guard |
| `test_backend_sdk_sync.py` | `/api/reinforcements` → real SDK `serve` `/reinforcements` → referee reasons and budget; `/api/state`, `/api/screenshot` |
| `test_empty_start.py` | default level and upsert accept an empty board |

### 17.3 Quirks and known issues found in the code

Worth knowing before changing nearby code (not fixed):

- `push_results`: when no run matches and `teamID` is empty, `team` is never
  assigned and `elif team:` raises `NameError` (HTTP 500).
- `push_results` legacy branch reads `db.get(TeamData, team.id)`, i.e. by
  `team_data.id`, not `team_data.team_id`; it can read another team's wave size.
- `POST /enemy_configs` accepts `budget` but does not save it.
- `runs.py`'s fallback budget (250-850) is always below `STARTING_COINS`, so
  `calculate_test_case_budget` ignores it.
- `submit_agent` has a duplicated unreachable `return`.
- `/api/results` counts `results_log` by loading every id (`len(...all())`),
  which grows with the table.
- `start_session` does not check `team.approved`.
- Results without `teamID` (e.g. Unity results relayed by the adapter) are
  invalid unless `engine == "portable"`.
- CORS is `*` with credentials.
- `init.sql` does not create the competition tables or the account columns;
  `create_all` plus the `ALTER TABLE` lines in `lifespan` do.
- Cross-component (details in [SDK_GUIDE.md §19.1](SDK_GUIDE.md#191-found-in-the-code-not-fixed)):
  a snapshot the SDK cannot parse (e.g. fractional unit positions, which this
  backend accepts and rounds) reaches `/api/reinforcements` without
  candidates, so the referee falls back to `SDK_URL` (not running in the event)
  and skips; and the SDK gives up on the referee after 1.5 s, possibly after
  the backend already recorded approved placements.

### 17.4 How to make common changes

| Change | Where |
|---|---|
| New participant/game route | `routers/competition.py` or `routers/game_api.py`; if it must be public, keep it under `/api/` (Caddy only forwards `/backend/api/*` and the pages) |
| New admin route | the `admin` router in `routers/competition.py` (inherits `verify_admin`); call it from `static/admin.html` via `api(path)` |
| New column | `models.py`, plus an `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `main.lifespan` (`create_all` never adds columns) |
| New table | `models.py` only (`create_all` creates it) |
| Scoring weights / formula | `evaluator_service.calculate_score` (and the module docstring) |
| Unit costs, bounty, starting coins | `evaluator_service.DEFENCE_COST_MAP`, `BOUNTY_PER_KILL`, `AUTOPILOT_STARTING_COINS` |
| Referee rules / grid / unit ids | `reinforcement_service.validate_reinforcements`, `GRID_*`, `VALID_DEFENCE_IDS`; also `schemas.GridPosition` limits |
| Pause interval cap | `competition.MAX_INTERVAL_S` and the legacy copy in `game_api.get_team_data` |
| Default test cases | `test_cases/*.json` (seeded only into an empty table) or `/admin/api/test-cases` |
| Registration questions | `competition.DEFAULT_FORM` or `PUT /admin/api/registration-form` |
| Token lifetime | `accounts.TOKEN_TTL_S` |
| Anything the SDK sends or reads | update [SDK_GUIDE.md](SDK_GUIDE.md) too |

## 18. Changelog

Add one line per change: date, what changed, sections touched.

| Date | Change | Sections |
|---|---|---|
| 2026-10-16 | First version, written from the code. | all |
