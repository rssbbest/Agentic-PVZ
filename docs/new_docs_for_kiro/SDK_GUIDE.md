# SDK guide (`autopilot-sdk/`, `autopilot-sdk-binary/`)

The complete reference for the participant SDK: what it does, every command,
every HTTP route it serves, what it sends to the backend, how it runs the
participant's `agent.py`, the sandbox around it, its logs and how it is built.
Use it to find the right file before changing anything.

> **Keep this file current.** Every change to the SDK (commands, proxy routes,
> the agent `state`, sandbox rules, timeouts, env vars, build) must update the
> matching section here and add a line to [Changelog](#20-changelog). The
> backend side is in [BACKEND_GUIDE.md](BACKEND_GUIDE.md).

---

## Table of contents

1. [What the SDK is](#1-what-the-sdk-is)
2. [Who the SDK talks to](#2-who-the-sdk-talks-to)
3. [Two copies of the source](#3-two-copies-of-the-source)
4. [File map](#4-file-map)
5. [Configuration (config, env vars, state file)](#5-configuration)
6. [Command reference](#6-command-reference)
7. [Interactive console](#7-interactive-console)
8. [The local proxy (`play` / `eval`)](#8-the-local-proxy-play--eval)
9. [One game, step by step (SDK side)](#9-one-game-step-by-step-sdk-side)
10. [Modes: play, play --all, eval](#10-modes-play-play---all-eval)
11. [Agent contract (`get_reinforcements`)](#11-agent-contract-get_reinforcements)
12. [How the agent is executed](#12-how-the-agent-is-executed)
13. [Security layers](#13-security-layers)
14. [LLM access: gateway, relay, tokens, tools](#14-llm-access-gateway-relay-tokens-tools)
15. [Run logs](#15-run-logs)
16. [`verify`](#16-verify)
17. [`autopilot serve` and the legacy CLI](#17-autopilot-serve-and-the-legacy-cli)
18. [Build, distribution, tests](#18-build-distribution-tests)
19. [Quirks, known issues, how-to](#19-quirks-known-issues-how-to)
20. [Changelog](#20-changelog)

---

## 1. What the SDK is

A Python package, `autopilot_sdk` (Python 3.10+, deps: `pydantic>=2`,
`requests>=2.31`, `rich>=13.7`), with the console script `autopilot`
(`autopilot_sdk.cli:main`). Participants get it as a one-file PyInstaller
binary (`autopilot-<os>-<arch>`). It has five roles:

| Role | Entry | Who uses it |
|---|---|---|
| **Local game proxy** (`play`, `eval`): listens on `127.0.0.1:5000` for the game, runs `agent.py` at every pause, forwards everything to the backend | `local_proxy.py` | participants (the main role) |
| **Interactive console** (`autopilot` with no arguments): menu around the proxy | `interactive.py` | participants |
| **`verify`**: static checks + dry run of `agent.py` | `checks.py` | participants |
| **`serve`**: HTTP service the backend can call (`/run`, `/reinforcements`) | `server.py` | organisers, legacy pipeline only |
| **Legacy CLI** (`register`, `submit`, `status`, `leaderboard`, `run --offline-demo`) | `cli.py`, `client/` | legacy pipeline only |

Version: `autopilot_sdk.__version__ = "0.2.0"` (also `SdkConfig.sdk_version`);
`pyproject.toml` still says `0.1.0`.

## 2. Who the SDK talks to

In the live competition flow the SDK is the only thing on the participant's
laptop that talks to the organisers' server. The game never calls the backend
directly, and the SDK never calls the game: the game (Unity WebGL in the
browser) sends HTTP requests to the SDK, and the SDK answers them.

```
 Browser: Unity WebGL game (build7, loaded from https://<site>/)
        │  every API call → http://127.0.0.1:5000/api/...
        ▼
 SDK local proxy  (autopilot play / eval, ThreadingHTTPServer)
        │  ├─ runs agent.py in a sandboxed subprocess at the opening and each pause
        │  ├─ HTTPS → https://<site>/backend/api/...   (backend)
        │  └─ relays the agent's LLM calls → https://<site>/llm/v1/...  (gateway)
        ▲
 agent.py worker process  ── LLM calls → http://127.0.0.1:5000/llm/v1 (relay)
```

| Peer | Direction | What |
|---|---|---|
| Game | game → SDK | `/api/login`, `/api/team`, `/api/reinforcements`, `/api/results`, other `/api/*`, `OPTIONS` preflights |
| Backend | SDK → backend | forwards of the game's calls; its own `/api/test-cases`, `/api/eval/info`, `/api/eval/start`, `/api/eval/{id}/finish`, `/api/state`, `/health` |
| LLM gateway | SDK → gateway | relays `POST /llm/v1/*` from the agent (only when the gateway is remote) |
| Agent worker | SDK ↔ worker | `multiprocessing` pipe: the state in, placements out |

Legacy only: the backend calls `autopilot serve` (`POST /run`,
`POST /reinforcements`), and the legacy CLI calls `/teams`, `/submissions`,
`/runs`, `/leaderboard`, which the event deployment does not expose publicly.

## 3. Two copies of the source

| Folder | Used by | Notes |
|---|---|---|
| `autopilot-sdk/` | local dev: root `requirements.txt` installs it editable (`-e ./autopilot-sdk[dev]`); `make test`; `integration/` (imports `autopilot_sdk`); `examples/` are copied to `/download/` by `deploy/Dockerfile.web` | canonical for development |
| `autopilot-sdk-binary/` | **the shipped binaries**: `.github/workflows/build-binaries.yml` builds from this folder; `bin/` holds prebuilt binaries copied to `/download/`; the standalone backend compose builds its `sdk` service from here | mirror |

As of this guide, every file under `src/`, `examples/` (except one) and
`tests/` (except one) is byte-identical in both. Differences:

- `build_binary.py`: the binary copy adds `--collect-submodules rich`.
- `pyproject.toml`: the binary copy pins `pyinstaller>=6.10,<7`.
- `README.md` differs.
- Only in `autopilot-sdk-binary/`: `.github/workflows/build-binaries.yml`
  (a copy; the active workflow is the root one), `examples/security-demo/invalid_contract.py`,
  `tests/smoke_binary.py`, `testing.md`, `bin/` (prebuilt binaries).
- Only in `autopilot-sdk/`: `build/pyinstaller.spec`, `src/autopilot_sdk.egg-info/`.

**Any SDK source change must be made in both folders**, otherwise the binary
participants download behaves differently from what the tests ran.

## 4. File map

Paths are relative to `autopilot-sdk/` (identical in `autopilot-sdk-binary/`).

```
pyproject.toml           package metadata, `autopilot` script, extras dev/build
build_binary.py          PyInstaller one-file build for the current OS
Dockerfile               image that runs `autopilot serve` on :8001
README.md, docs/         agent-development.md, backend-integration.md (partly outdated, see §19)
sdk-technical-design.md  original design doc (turn-based contract; background only)
examples/
  reinforcement_agent.py budget-aware rule agent
  llm_agent.py           LLM with tools + rule fallback (the "step 3" agent in README_PARTICIPANT.md)
  llm_tool_agent.py      LLM tool-calling agent with lane-blocking fallback
  llm_check_agent.py     one plain LLM call per pause, prints [llm-check] lines
  security-demo/         hostile agents + showcase.py that runs them through the proxy
tests/                   pytest (see §18)
src/autopilot_sdk/
  __init__.py            __version__, exports Agent
  __main__.py            binary entry: multiprocessing.freeze_support(), AUTOPILOT_DEBUG stack dumps
  cli.py                 argparse commands; no args → interactive console
  config.py              SdkConfig defaults + env overrides, MAX_AGENT_TIME_S = 10
  interactive.py         rich-based menu console, ~/.autopilot/state.json
  local_proxy.py         the game proxy: routes, RunContext, eval start/finish, summaries, LLM relay
  console.py             timestamped coloured terminal lines, sinks (run log), log handler
  runlog.py              autopilot-runs/<run>/ folder: session.json, events.jsonl, console.log, agent copies
  checks.py              `verify`: static checks + dry run on sample opening/pause
  tools.py               @tool decorator and run_tool_loop for LLM tool calling
  server.py              `autopilot serve`: /health, /run, /reinforcements
  client/backend_client.py  legacy REST client with retries
  contracts/
    schemas.py           Pydantic models: placement + reinforcement wire shapes; old turn-based models
    agent_base.py        legacy Agent base class (decide / place_defences)
    bharatcode_client.py BharatCodeClient: OpenAI-style chat client for the gateway
  runtime/
    reinforcements.py    run_reinforcements(): scan, temp file, spawn worker, parse; find_agent_file()
    worker.py            child-process entry points (reinforcement_worker, placement_worker, agent_worker, ...)
    orchestrator.py      _spawn_and_join / _receive (spawn context, timeout, kill); TurnOrchestrator (legacy)
    placement.py         run_placement() for /run (legacy)
    loader.py            load_reinforcement_function(), load_agent_class()
  network_guard/
    guard.py             install_agent_sandbox() (audit hook), install_network_guard() (legacy socket patch)
    domains.py           ALLOWED_HOSTS, BLOCKED_HOST_SUFFIXES, LOCAL_LLM_PORTS
  verifier/
    static_scan.py       scan_source()/scan_file(): regex + AST pass
    denylist.py          banned imports, banned URL substrings, obfuscation heuristics
  telemetry/logger.py    JSONL events to ~/.autopilot/logs/<match_id>.jsonl (legacy/LLM call audit)
```

## 5. Configuration

### 5.1 `SdkConfig` (`config.py`, frozen dataclass; `load_config()` applies env overrides)

| Field | Default | Override | Used for |
|---|---|---|---|
| `backend_base_url` | `http://localhost:8010` | `AUTOPILOT_BACKEND_URL`, `--backend` (play/eval) | backend base URL |
| `bharatcode_base_url` | `https://api.bharatcode.dev/v1` | – | placeholder; its host is allowed by the sandbox |
| `default_time_limit_s` | 45.0 | `serve --timeout` | legacy `/run` and offline demo limit |
| `sdk_version` | `0.2.0` | – | |
| `event_backend_url` | `https://equity-projector-halo-directors.trycloudflare.com/backend` | – | default backend offered by the interactive console |
| `telemetry_batch_every_turns` | 1 | – | unused |
| `max_backend_retries` | 5 | – | legacy `BackendClient` |
| `service_host` / `service_port` | `0.0.0.0` / 8001 | `serve --host/--port` | `autopilot serve` |
| `reinforcement_timeout_s` | 10.0 | `AUTOPILOT_REINFORCEMENT_TIMEOUT_SECONDS` (capped at 10), `--reinforcement-timeout` | agent time limit (`play`: clamped 0.05-10 s; `serve`: clamped 0.05-4 s) |
| `agent_dir` | `./agents` | `AUTOPILOT_AGENT_DIR`, else `UPLOAD_DIR`, `serve --agent-dir` | where `serve` looks for team agent files |

### 5.2 Environment variables

Set by the participant or organiser:

| Variable | Read in | Meaning |
|---|---|---|
| `AUTOPILOT_BACKEND_URL` | `config.py`, `interactive.py` | backend URL (hosted: `https://<site>/backend`); in the console it overrides the saved one |
| `AUTOPILOT_GATEWAY_URL` | `local_proxy.gateway_upstream`, `BharatCodeClient`, `worker.py` | LLM gateway (hosted: `https://<site>/llm/v1`); default for a hosted backend is `<site>/llm/v1` |
| `AUTOPILOT_GATEWAY_KEY` | `BharatCodeClient`, `apply_login` | team LLM token; filled from the login reply when not set |
| `AUTOPILOT_REINFORCEMENT_TIMEOUT_SECONDS` | `config.py` | agent time limit (≤ 10) |
| `AUTOPILOT_AGENT_DIR` / `UPLOAD_DIR` | `config.py` | `serve` agent directory |
| `AUTOPILOT_GAME_URL` | `local_proxy.game_url` | game page shown to the participant (default `<site>/`) |
| `AUTOPILOT_LOG_DIR` | `local_proxy.new_run` | run log root (default `./autopilot-runs`) |
| `AUTOPILOT_DEBUG` | `__main__.py` | `kill -USR1 <pid>` dumps every thread's stack (non-Windows) |
| `NO_COLOR` | `console.py` | disable colours |

Set internally by the SDK (inherited by agent worker processes):

| Variable | Set by | Meaning |
|---|---|---|
| `AUTOPILOT_GATEWAY_UPSTREAM` | `gateway_upstream()` | cached real gateway URL (cleared when the console changes backend) |
| `AUTOPILOT_GATEWAY_URL` | `_relay_llm_through()` | rewritten to `http://127.0.0.1:<port>/llm/v1` when the gateway is remote |
| `AUTOPILOT_SDK_RELAY_PORT` | `_relay_llm_through()` | the relay port the sandbox allows on loopback |
| `AUTOPILOT_GATEWAY_KEY` + `AUTOPILOT_GATEWAY_KEY_FROM_LOGIN=1` | `apply_login()` | token from the login reply; a later login may replace a login-provided token, never a user-set one |

### 5.3 Files on the participant's machine

| Path | Written by | Content |
|---|---|---|
| `~/.autopilot/state.json` | interactive console | `{"backend", "agent", "team", "testCase"}` remembered between starts |
| `./autopilot-runs/<stamp>_<mode>_<label>/` | `runlog.RunLog` | run log folder (§15) |
| `~/.autopilot/logs/<match_id>.jsonl` | `telemetry/logger.py` | `bharatcode_call` events and legacy violations (`local.jsonl` by default) |

## 6. Command reference

`autopilot` with no arguments opens the interactive console (§7). Otherwise
(`cli.build_parser`):

| Command | Flags | Does | Backend calls | Exit code |
|---|---|---|---|---|
| `version` | – | prints `autopilot 0.2.0` | – | 0 |
| `verify` | `--agent` (req), `--no-dry-run` | static checks + dry run (§16) | none (the agent may call the LLM) | 0 pass, 1 fail |
| `play` | game options + `--test-case CODE`, `--all` | local proxy in play mode (§8, §10) | see §8 | 0, or 1 on agent/suite/login/port errors |
| `eval` | game options | local proxy in eval mode | see §8 | 0 if the eval result was accepted, else 1 |
| `serve` | `--host`, `--port`, `--timeout`, `--reinforcement-timeout`, `--agent-dir` | legacy SDK service (§17.1) | none (it is called) | – |
| `register` | `--name` | legacy: create a team | `POST /teams` | 0/1 |
| `submit` | `--agent`, `--team-id`, `--no-wait`, `--wait-timeout 1800` | legacy: scan, load `Agent` class, upload, poll | `POST /submissions`, `GET /submissions/{id}` | 0 completed, 1 otherwise, 2 local check failed |
| `status` | `submission_id`, `--runs` | legacy: show a submission | `GET /submissions/{id}`, `GET /runs/{id}` | 0/1 |
| `leaderboard` | `--limit 50` | legacy | `GET /leaderboard` | 0/1 |
| `run` | `--agent`, `--offline-demo`, `--team-key` | legacy: one offline `Agent.decide` turn | none | 0, 2 check failed, 3 without `--offline-demo` |

Game options (`play` and `eval`): `--agent` (required), `--backend`
(default `AUTOPILOT_BACKEND_URL` or `http://localhost:8010`), `--host 127.0.0.1`,
`--port 5000`, `--reinforcement-timeout` (≤ 10), `--team` + `--password`
(log in from the terminal; asked for when omitted), `--verbose` (logs every HTTP
request through the `autopilot.http` logger).

Notes:
- `play` with neither `--test-case` nor `--all` adds no `testCase`, so the
  backend serves the team's **legacy level** (`team_data`); this is how the
  README's "team ID → level" table works.
- `cli._utf8_output()` reconfigures redirected stdout/stderr to UTF-8 so ✓/✗
  do not crash on Windows pipes.
- Legacy commands use `load_config().backend_base_url`, not `--backend`.

## 7. Interactive console

`interactive.main()` (rich UI):

1. Banner; `AUTOPILOT_BACKEND_URL` overrides the saved backend.
2. No backend saved → `choose_backend` (default: saved, env, then
   `event_backend_url`; adds `https://` if missing).
3. No valid agent saved → `choose_agent` (accepts a folder → `folder/agent.py`;
   runs `check_agent_file`).
4. `probe(backend)`: `GET /health`, `GET /api/test-cases`, `GET /api/eval/info`
   (8 s each) → status panel (backend online, public/hidden counts, agent,
   team, LLM gateway).
5. Menu loop:

| Key | Action |
|---|---|
| `1` PLAY | pick a public test case (table of code/name/enemies), then `run_mode("play")` |
| `2` PLAY ALL | `fetch_test_cases` → suite, `run_mode("play", all)` |
| `3` EVAL | needs hidden cases > 0; offers a login; confirms; `run_mode("eval")` |
| `4` VERIFY | `verify_agent` with a spinner |
| `A` | choose another agent.py |
| `L` | log in (`POST /api/login`), keeps the token for the next run |
| `B` | change backend (re-probes; clears the cached gateway) |
| `Q` | quit |

`run_mode` builds a `RunContext` (`new_run`), prints the READY panel (game URL,
run log folder) and calls `serve_run("127.0.0.1", 5000, ...)` (port fixed).
Ctrl+C stops the proxy and returns to the menu; a login made on the game page
carries over to the next run. Port 5000 busy → error message, back to menu.

## 8. The local proxy (`play` / `eval`)

`local_proxy.py`. `make_proxy()` builds a `ThreadingHTTPServer` with a
handler subclass whose class attributes hold the run's state; `serve_run()`
runs it in a thread until Ctrl+C or the end of an eval.

### 8.1 Routes served on `127.0.0.1:5000`

| Method / path | Handling | Backend call (timeout) |
|---|---|---|
| `OPTIONS *` | 204 with CORS headers | – |
| `GET /health` | `{"status": "ok"}` locally | – |
| `GET /suite` | `play --all` progress (`suite_status`) | – |
| `GET /api/*` | forwarded unchanged + Bearer | same path (10 s) |
| `POST /api/login` | `_handle_login`: forward without auth; on 200 `apply_login` (keep token, set LLM key) | `POST /api/login` (10 s) |
| `POST /api/team` | `_handle_team`: add `agentSha256` and `testCase` or `evalId`+`evalSlot`; start the eval on the first game; forward; remember the level | `POST /api/eval/start` (20 s, eval only), `POST /api/team` (10 s) |
| `POST /api/reinforcements` | `_handle_reinforcements`: run the agent, forward candidates to the referee, or answer a skip locally | `POST /api/reinforcements` (**1.5 s**) or background `POST /api/state` (5 s) |
| `POST /api/results` | forward; before the game gets the reply, print the summary and record suite/eval scores; the last eval slot triggers `finish_eval` | `POST /api/results` (10 s), then `POST /api/eval/{id}/finish` (60 s) |
| `POST /api/*` (other, e.g. `/api/screenshot`) | forwarded unchanged + Bearer | same path (10 s) |
| `POST /llm/v1/*` | LLM relay (only if the gateway is remote); 403 if the request has an `Origin` header (browsers) | gateway (30 s) |
| anything else | 404 `{"detail": "Not found"}` | – |

Common behaviour:
- Request bodies over 2 MiB → 400.
- Every reply carries `Access-Control-Allow-Origin: *`, methods
  `GET, POST, OPTIONS`, the requested headers, and
  `Access-Control-Allow-Private-Network: true` when Chrome asks (needed for a
  public HTTPS page to call `127.0.0.1`).
- After a login, forwards carry `Authorization: Bearer <token>`
  (`_auth_headers`), except `/api/login` and the background `/api/state`.
- Backend unreachable on a forward → 502 `{"detail": "Backend unreachable: ..."}`.
- All backend calls share one `requests.Session` with pool size 16 (keeps TLS
  connections warm).

### 8.2 Per-run state

`RunContext` (one per `play`/`eval` run): `mode`, `test_case`, `suite`
(`{"cases", "scores": {code: score}}`), `auth` (`{"token", "teamID", "teamName"}`),
`eval` (`{"source", "sha", "id", "slots", "scores": {slot: score}, "finishing", "result"}`),
`log` (RunLog), `finished` (Event), `lock`.

Handler class attributes (per proxy instance):

| Attribute | Key | Content |
|---|---|---|
| `ledgers` | sessionID and lower-cased teamID | `{"total": matchBudget, "opening": budget, "perKill": bountyPerKill, "costs": unitCosts, "spent": starting defences cost}`; `spent` is replaced by the referee's `budget.spent` after each decision |
| `levels` | sessionID and lower-cased teamID | `{"upcomingEnemies", "spawnSettings", "gameSettings"}` for the opening call |
| `pauses` | sessionID | number of reinforcement calls seen (1 = opening) |
| `sessions` | sessionID | the test case code played |

### 8.3 `POST /api/team` (`_handle_team`)

1. Parse the body (non-JSON → forwarded as is).
2. `request["agentSha256"] = sha256(current agent source)` (eval: the frozen copy).
3. **Eval**: without a login token → 401 `{"error": "Log in with your team ID and password first"}`.
   If the eval has no id yet → `start_eval` (`POST /api/eval/start {agentSource, sdkVersion}`; error → 409 `{"error"}`).
   Next slot = lowest slot without a recorded score; none left or finished → 409.
   Adds `evalId`, `evalSlot`.
4. **Play**: with a finished suite, scores are cleared (the next press starts
   again); code = next unplayed suite case, else `run.test_case`; adds `testCase`
   when there is a code.
5. Forward. On 200: log `level`, print "Game started" (enemies, budget, pause
   interval), `_remember_level` (ledger + wave), map `sessionID → testCase`.
   On error: print the backend's `error`/`detail`; the game receives the
   backend's status and body.

### 8.4 `POST /api/reinforcements` (`_handle_reinforcements`)

1. Validate the body as `ReinforcementSnapshot`. If that fails (including a
   fractional unit position, see §19), the raw body is forwarded to the
   backend unchanged and the agent is **not** run.
2. `n = pauses[sessionID] += 1`; `opening = (n == 1 and a level is remembered)`.
3. Budget from the ledger (same formula as the referee):
   `killed = max(total - queued - active, number of isDead)`,
   `unlocked = min(total, opening + perKill × killed)`,
   `remaining = max(0, unlocked - spent)`.
4. Build the agent `state` (§11), call `run_reinforcements(source, state, limit)`
   (§12). Any exception → `[]`.
5. `wire_candidates(raw)`: keep only items that validate as the SDK's
   `DefencePlacement` (x 0-10, y 0-5, defenceId 0-3); others are dropped with a
   warning, because one off-grid candidate would make the backend 422 the whole
   request.
6. **No candidates** → reply `{"placeDefences": false, "defencePlacementData": [], "coins": remaining}`
   to the game at once, and send the raw body to `POST /api/state` in a
   background thread (so the backend's match record sees every unit).
7. **Candidates** → `payload["candidatePlacements"] = candidates` (the game's
   raw snapshot plus the candidates, not the agent `state`), `POST /api/reinforcements`
   with Bearer, 1.5 s timeout. Error/timeout → skip reply. Success → the
   backend's decision is sent to the game verbatim, the ledger takes the
   referee's `spent`, and the terminal shows `referee approved k/n: ✓ ... ✗ ...: reason`
   and the budget line.
8. Every pause is logged as a `pause` event (§15).

### 8.5 `POST /api/results`

Forwarded with `before_reply=_print_results`. Before the game sees the reply:
- logs `result` (game payload + backend reply);
- prints `format_results`: WON/LOST, duration, enemies killed/total and
  defences destroyed/total (preferring the backend's completed counts from
  `evaluator_output.stats`), type stats, `Score`, cost vs budget and the
  breakdown (or "not scored (reason)");
- appends a game row to `session.json`;
- `play --all`: records the score under the case code (from the reply's
  `testCase`, else the session map); after the last case prints the table and
  average;
- `eval`: records the score under the reply's `evalSlot`; after the last slot
  starts `finish_eval` in a thread.

`finish_eval`: `POST /api/eval/{id}/finish {"log": base64(gzip(events.jsonl)), "summary": {"sdkVersion", "games"}}`
(Bearer, 60 s), prints the EVAL RESULT table and score, logs `eval_finish`,
sets `run.finished` → `serve_run` returns `"finished"`.

### 8.6 Lifecycle (`play()`, `new_run()`, `serve_run()`)

- `play()`: `--all` → `fetch_test_cases` (`GET /api/test-cases`, error if
  empty); `check_agent_file`; `new_run`; prints the header (agent, backend,
  LLM gateway, game API address, run log, game URL).
- `new_run()`: creates the RunLog, saves the agent copy, freezes the source for
  eval, adds the run log as a console sink.
- `serve_run()`: sets up the LLM relay env (§14), builds the proxy, serves in a
  daemon thread, installs a Ctrl+C handler that only sets a flag (safe with the
  one-file launcher's duplicated Ctrl+C and on Windows), polls every 0.3 s,
  then shuts down and closes the run log.

## 9. One game, step by step (SDK side)

```
game                         SDK (127.0.0.1:5000)                         backend
 │ POST /api/login ────────▶ forward ───────────────────────────────────▶ token, llmToken
 │                           keep token; AUTOPILOT_GATEWAY_KEY = llmToken
 │ POST /api/team {teamID} ▶ + agentSha256 + testCase | evalId,evalSlot ─▶ level + sessionID "c…"
 │                           (eval: first game → /api/eval/start)
 │                           remember ledger + wave
 │ POST /api/reinforcements▶ n=1 → phase "start" (+ upcomingEnemies)
 │   (level start)           run agent (≤ 10 s) → candidates
 │                           ├─ none → skip reply; /api/state (bg) ─────▶ match_tracker
 │                           └─ some → + candidatePlacements ───────────▶ referee → decision
 │ ◀──────── decision / skip
 │ POST /api/reinforcements▶ n≥2 → phase "pause" … same as above
 │   (every ~3 s game time)
 │ POST /api/results ──────▶ forward ───────────────────────────────────▶ complete, score, store
 │ ◀──────── reply           print summary + score; suite/eval bookkeeping
 │                           (eval, last slot) /api/eval/{id}/finish ───▶ eval score
```

The game stays frozen at a pause until the reply arrives (build7; the level's
`reinforcementHoldSeconds` is only a minimum), so the agent always gets its
full time limit.

## 10. Modes: play, play --all, eval

| | `play --test-case CODE` | `play --all` | `eval` | `play` (no case) |
|---|---|---|---|---|
| Added to `/api/team` | `testCase=CODE` | `testCase=<next unplayed>` | `evalId`, `evalSlot=<next>` | nothing |
| Backend level | that public case | each public case in order | each active hidden case | team's legacy `team_data` level |
| agent.py | re-read at every pause (edits apply live) | live | frozen copy taken at `new_run` | live |
| Login | optional (unless backend `REQUIRE_LOGIN`) | optional | required | optional |
| After the game | "press play to run CODE again" | per-case score, average after the last | per-slot score; after the last slot the eval finishes and the proxy stops | summary |
| Backend record | `game_sessions` mode `play` | same | `eval_runs` + `game_sessions` mode `eval` + uploaded log | `results_log` only |

## 11. Agent contract (`get_reinforcements`)

`agent.py` must define a module-level `get_reinforcements(state: dict) -> list[dict]`
(checked by AST at startup and at load in the worker; the function's
`__module__` must be the agent module, so an imported function does not count).

### 11.1 The `state` the agent receives

From the game's snapshot (`ReinforcementSnapshot.model_dump(mode="json")`, extra
fields the game sends are kept):

| Key | Type | Notes |
|---|---|---|
| `teamID` | str | as the game sent it |
| `sessionID` | str \| None | backend session (`c…` for test cases) |
| `elapsedSeconds` | float | game time |
| `counts` | `{activeDefenceCount, activeEnemyCount, enemiesQueued, totalEnemiesInWave}` | ints ≥ 0 |
| `defences` | `[{id, type, health, maxHealth, position: {x, y}}]` | living and dead defences as reported |
| `enemies` | `[{id, type, health, maxHealth, position: {x, y}, isAttacking, isDead}]` | |
| `candidatePlacements` | list | whatever the game sent (normally empty) |

Added by the SDK:

| Key | When | Content |
|---|---|---|
| `budget` | level known | `{"remaining", "spent", "unlocked", "total"}` computed like the referee (§8.4) |
| `unitCosts` | level known | `{"0": 250, "1": 200, "2": 300, "3": 150}` from the level reply |
| `timeBudgetSeconds` | always | the agent's time limit (10 s by default) |
| `phase` | always | `"start"` for the first call of a session (if the level is known), else `"pause"` |
| `upcomingEnemies`, `spawnSettings`, `gameSettings` | `phase == "start"` | the level's wave (`enemyId`, `count`, `allowedRows`) and settings |

Game facts the agent needs: board x 0-10 (enemies walk from x=10 toward x=0)
and y 0-5; defence ids 0 penguin, 1 soldier, 2 archer, 3 shield; enemy ids
0 Clown, 1 Monk, 2 Wizard, 3 Pirate (`local_proxy.DEFENCE_NAMES`/`ENEMY_NAMES`).
The board starts empty: the opening call is where the first defences go.

### 11.2 What the agent returns

`[{"defenceId": 0-3, "defenceGridPosition": {"x": 0-10, "y": 0-5}}, ...]`, most
urgent first (the referee approves in order until the money runs out).
`None` counts as `[]`. A non-list, or a list with a non-dict item, is an error
(→ skip). Items outside the SDK schema are dropped locally (§8.4 step 5).
Occupancy, duplicates and budget are left to the backend referee, which never
moves a placement.

### 11.3 Legacy contract (`contracts/agent_base.Agent`)

A subclass of `autopilot_sdk.Agent` with `place_defences(enemies)` (used by
`/run`, `submit`) or `decide(state: TurnState)` (offline demo). Exactly one
concrete subclass defined in the file; at least one of the two methods
overridden. Not used by `play`/`eval`.

## 12. How the agent is executed

`runtime/reinforcements.run_reinforcements(agent_source, state, timeout_s)`:

1. Decode as UTF-8 (BOM allowed); not UTF-8 → `[]`.
2. `scan_source` (§13.1); violations → `[]`.
3. Write the source to a temp file `agent_*.py`.
4. `orchestrator._spawn_and_join(reinforcement_worker, (path, json(state)), timeout_s)`:
   a fresh **`spawn`** process (a new interpreter every call: no memory between
   pauses), daemon, `join(timeout)`; still alive → `terminate`, then `kill`.
5. Inside `worker.reinforcement_worker`: `install_agent_sandbox` with the
   gateway hosts (§13.2) **before** loading the agent; `load_reinforcement_function`;
   call it; validate the return type; send `("ok", json)` (`allow_nan=False`).
   Exceptions → `("error", traceback)`; load failure → `("load_error", msg)`;
   sandbox violation → `("blocked", msg)` and `os._exit(0)`.
6. Parent: timed out, no process, no message (0.25 s), non-`ok` status,
   non-JSON or non-list → log a "No placements this pause: <reason>" warning
   and return `[]`; else the dict items.
7. The temp file is deleted.

Every failure fails open to `[]`, so the game always resumes.

Because each call is a new process, an agent cannot keep Python state between
pauses (module globals are fresh). Start-up cost is paid at every call.

## 13. Security layers

### 13.1 Static scan (`verifier/static_scan.py`, `denylist.py`)

Runs at `play`/`eval` startup (`check_agent_file`), in `verify`, and before
every call.

| Check | Result |
|---|---|
| Banned URL substrings anywhere in the text (case-insensitive): `api.openai.com`, `openai.azure.com`, `oaiusercontent.com`, `api.anthropic.com`, `claude.ai`, `generativelanguage.googleapis.com`, `aiplatform.googleapis.com`, `api.cohere.ai/.com`, `api.mistral.ai`, `api.together.xyz/.ai`, `api.groq.com`, `api.perplexity.ai`, `api.deepseek.com`, `openrouter.ai`, `api.replicate.com`, `bedrock-runtime`, `.amazonaws.com`, `api.x.ai`, `api.ai21.com`, `api-inference.huggingface.co` | violation |
| Imports (AST) of `anthropic`, `cohere`, `mistralai`, `replicate`, `groq`, `ai21`, `together`, `huggingface_hub`, `google.generativeai`, `vertexai`, `litellm`, `langchain_openai`, `langchain_anthropic`, `langchain_community`, `ollama` | violation |
| Imports of `openai`, `boto3` | warning only |
| `base64.b64decode`, `exec(`, `eval(`, rot13 `codecs.decode` | warning only |
| Syntax error | violation |

### 13.2 Runtime sandbox (`network_guard/guard.install_agent_sandbox`)

A CPython audit hook (cannot be removed, sees C-level `_socket` calls),
installed in the worker before the agent is imported:

- **Network**: allowed = loopback (except `LOCAL_LLM_PORTS` 11434, 1234, 5000,
  5001, 7860, 8000, 8080, 8888, with the relay port exempt), `ALLOWED_HOSTS`
  (`api.bharatcode.dev`, `api.autopilot.gdg.dev`), and the hostnames of
  `AUTOPILOT_GATEWAY_URL` and `bharatcode_base_url`. A raw IP is allowed only if
  an allowed hostname resolved to it. Provider hosts (`BLOCKED_HOST_SUFFIXES`)
  and everything else are blocked. Unparseable addresses fail closed.
- **Processes**: `subprocess.Popen`, `os.system`, `os.exec*`, `os.posix_spawn`,
  `os.spawn*`, `os.fork*`, `os.startfile` blocked.
- **Native code**: `ctypes.dlsym`, `ctypes.call_function` blocked (`ctypes` is
  pre-imported so Windows' own lookups do not trip it).
- **Introspection**: `gc.get_objects/referrers/referents`, `sys._current_frames` blocked.
- **Imports**: banned provider SDKs (the static list minus `openai`/`boto3`)
  blocked by `import` event, by module code path on `exec`, and if already in
  `sys.modules`.
- A violation calls `on_violation` first (reports "blocked" and exits the
  process), so catching the exception does not help.

Not covered: it is not an OS sandbox (files and env vars of the user are
readable); a participant can modify or replace the SDK on their own machine.
Only the backend referee and scoring are trusted. See
`examples/security-demo/README.md` and `showcase.py` for a demonstration of each
layer.

### 13.3 Legacy socket guard (`install_network_guard`)

Used by the `Agent`-class workers (`placement_worker`, `agent_worker`,
`match_*_worker`): patches `socket.create_connection` and `socket.getaddrinfo`;
denylist model (blocks provider hosts and local-LLM ports, allows the rest).

## 14. LLM access: gateway, relay, tokens, tools

- **Gateway URL**: `gateway_upstream(backend)` = `AUTOPILOT_GATEWAY_URL`, or
  `<site>/llm/v1` when the backend URL ends in `/backend`; cached in
  `AUTOPILOT_GATEWAY_UPSTREAM`.
- **Relay** (`_relay_llm_through`): if the gateway is remote (not localhost /
  loopback), agents get `AUTOPILOT_GATEWAY_URL=http://127.0.0.1:<port>/llm/v1`
  and `AUTOPILOT_SDK_RELAY_PORT=<port>`. The proxy forwards `POST /llm/v1/*` to
  the real gateway over its warm session (a fresh agent process would pay a new
  TLS handshake each pause), logs an `llm` event and prints
  `🧠 LLM answered / asked to run <tools> (s)` or the failure with a hint
  (401/403 token, 413 too large, 429 rate limit). A local gateway is used
  directly, without the relay.
- **Token**: `AUTOPILOT_GATEWAY_KEY`, set by the participant or filled from the
  login reply's `llmToken` (`<teamCode>.<hmac>`; 60 calls/min per team at the
  gateway).
- **`BharatCodeClient`** (`contracts/bharatcode_client.py`): `chat(messages, timeout_s=None, **kwargs)`
  posts `{messages, **kwargs}` to `<base_url>/chat/completions` with
  `Authorization: Bearer <key>` (omitted if no key); `timeout_s <= 0` raises
  `requests.Timeout` without a call; HTTP errors raise after printing a red
  line (unless relayed, where the relay reports). Logs `bharatcode_call` to
  telemetry.
- **Tools** (`tools.py`): `@tool` builds an OpenAI function schema from type
  hints and the docstring (`Args:` section); `run_tool_loop(client, messages, tools, max_rounds=4, deadline_s=None, final_prompt=..., **chat_kwargs)`
  runs tool calls inside the agent process until the model answers; the last
  round sends no tools plus a "final answer" prompt; results are capped at
  20 000 chars; returns `ToolRun(content, messages, calls, rounds, stopped)`
  with `stopped` in `answered | max_rounds | deadline`.

## 15. Run logs

`runlog.RunLog` writes `autopilot-runs/<YYYY-MM-DD_HH-MM-SS>_<mode>_<label>/`
(label = test case, `all`, or empty; `-2`, `-3` suffix on collision):

| File | Content |
|---|---|
| `session.json` | `mode`, `label`, `started`, `testCase`, `suite`, `agent`, `agentSha256`, `backend`, `sdkVersion`, `team`, `teamName`, `games` (`{sessionID, testCase, evalSlot, won, score, durationSeconds}`), and later `evalId`, `hiddenTestCases`, `average`, `evalScore`, `finished` |
| `events.jsonl` | one JSON per line, `{"t", "kind", ...}` |
| `console.log` | terminal output without ANSI colours (console sink) |
| `agent-<sha12>.py` | every agent version that played |

Event kinds: `session_start`, `agent_version {sha256, bytes}`,
`login {team, status}`, `level {status, request, reply}`,
`pause {session, n, phase, snapshot, budget, agentSeconds, agentReturned, candidates, decision, error?}`,
`llm {status, seconds, request_bytes, reply}` (relay only), `result {result, reply}`,
`eval_start {evalId, slots, agentSha256}`, `eval_finish {reply, error}`,
`session_end`. An eval uploads `events.jsonl` gzipped.

## 16. `verify`

`checks.verify_agent(path, dry_run=True, time_limit=10)`:

1. `agent.py found`.
2. `Safety scan` (static scan; warnings shown as detail).
3. `get_reinforcements(state) defined` (`check_agent_file`).
4. If all passed and dry run on: `run_reinforcements` on two sample states from
   `sample_states()` (an opening with `phase="start"`, `upcomingEnemies`, budget
   3000/3500; a pause with three enemies and two defences), each checked with
   `wire_candidates`; passes if it finished within the limit + 0.5 s. The
   detail shows placements, cost vs remaining, time, and a hint when nothing
   was proposed.

## 17. `autopilot serve` and the legacy CLI

### 17.1 `autopilot serve` (`server.py`)

The service the backend calls at `SDK_URL` (legacy pipeline and the backend's
fallback when a reinforcement request has no candidates). Not used in the
event deployment. `ThreadingHTTPServer` on `0.0.0.0:8001` by default.

| Route | Body | Reply |
|---|---|---|
| `GET /health` | – | `{"status": "ok"}` |
| `POST /run` | `{"agentFileContent": base64, "enemies": [{enemyId, count, allowedRows?}]}` | 200 `RunResponse` (`exclude_none`): `{"sdkStatus": {"valid", "violations": [{type, message}]}, "defencePlacementData"?}`; 400 bad body/base64; 500 SDK crash |
| `POST /reinforcements` | `{"teamID", "snapshot": ReinforcementSnapshot}` | 200 `{"defencePlacementData": [...]}` (`[]` if no agent file or any failure); 400 if body invalid or `teamID != snapshot.teamID` |

Bodies must be 1 byte to 2 MiB. `/run` = `runtime/placement.run_placement`:
scan → `placement_worker` (`Agent.place_defences`) with `--timeout` (45 s) →
validate `list[DefencePlacement]` → no duplicate cells → non-empty. Violation
types: `invalid_agent`, `forbidden_api_usage`, `timeout`, `agent_crashed`,
`agent_exception`, `invalid_agent_output`, `duplicate_placement`.
`/reinforcements` uses `find_agent_file(agent_dir, teamID)`: newest of
`{teamID}.py`, its slug, `team<id>.py`, or `team<id>_*.py` /
`<slug>_*.py`, case-insensitive, inside `agent_dir`; limit
`reinforcement_timeout_s` clamped to 4 s (backend waits 4.5 s).

### 17.2 Legacy CLI and client

`client/backend_client.BackendClient(base_url, timeout_s=30, max_retries=5)`:
connection errors, timeouts and 5xx are retried with backoff 1, 2, 4, 8 s; 4xx
raise `BackendError("<status>: <detail>")`. Methods: `health`, `create_team`,
`submit` (multipart `agent_file` + `team_id`), `get_submission`, `get_run`,
`enemy_configs`, `leaderboard`, `wait_for_submission` (poll 2 s). `run
--offline-demo` uses `runtime/orchestrator.TurnOrchestrator` with the old
`TurnState`/`TurnDecision` models and pickled `self` state between turns.

## 18. Build, distribution, tests

- **Build** (`build_binary.py`, run on each target OS): PyInstaller
  `--onefile --name autopilot-<linux|windows|macos>-<x86_64|arm64>[.exe]`,
  `--collect-submodules autopilot_sdk`, `--collect-all pydantic requests`
  (+ `--collect-submodules rich` in the binary copy), entry
  `src/autopilot_sdk/__main__.py`. Output in `dist/`.
- **CI** (root `.github/workflows/build-binaries.yml`, working dir
  `autopilot-sdk-binary`): Linux and Windows always, macOS arm64/Intel on demand;
  Python 3.11; `pip install ".[build]"`; build; `tests/smoke_binary.py` (version,
  verify dry run, a full `play --test-case TC01` game against a stand-in
  backend on port 5000, the console quits on `q`); upload artifacts.
  `deploy.yml` runs it on every push to `test_branch` and ships the new
  binaries to `/download/`.
- **Docker** (`Dockerfile`): `pip install .`, user `sdk`, `autopilot serve` on 8001.
- **Downloads**: `deploy/Dockerfile.web` copies `autopilot-sdk-binary/bin/*`,
  `autopilot-sdk/examples/*.py`, `README_PARTICIPANT.md`, `docs/PARTICIPANTS.md`
  to `/download/`.
- **Tests** (`autopilot-sdk/tests`, `make test` or `python -m pytest -q`):

| File | Covers |
|---|---|
| `test_local_proxy.py` | proxy end to end against a stand-in backend: local agent run + referee, skips with `/api/state`, backend down, pass-through, CORS, startup rejection, summaries and scores, opening call with the wave, full time limit, LLM relay and its guard port, budget and refusal reasons, coin counter, `--all`, single test case, login carry-over, eval (frozen agent, slots, log upload), eval without login |
| `test_agent_sandbox.py` | sandbox bypass attempts, caught violations, provider import however written, loopback gateway allowed, relay port is the only local-LLM port allowed |
| `test_reinforcements.py` | `run_reinforcements` contract, skip/errors/timeouts, `find_agent_file`, `serve /reinforcements`, UTF-8 BOM |
| `test_tools.py` | tool schemas, loop, tool errors, max rounds/deadline, sandboxed tool agent without a gateway, client headers and zero timeout |
| `test_placement_service.py` | `/run` and `run_placement` violations, service contract |
| `test_offline_core.py` | static scan, legacy socket guard, loader, `TurnOrchestrator` memory/timeout/exception/lifecycle |
| `test_guard_live.py` | legacy socket guard against real client libraries |
| `test_cli_output.py` | `verify` output through a cp1252 pipe |

## 19. Quirks, known issues, how-to

### 19.1 Found in the code (not fixed)

- **Fractional positions skip the agent.** The SDK's `UnitPosition` takes
  ints only (checked: `6.0` passes, `6.4` fails validation), while the backend
  rounds floats and says units between cells are reported at fractional
  positions. On such a snapshot `_handle_reinforcements` forwards the raw body
  to the backend without running the agent, so that pause gets no placements,
  and the pause counter is not advanced (a later call can become the "opening").
  Whether build7 sends fractional positions was not verified here (its source
  is not in `Assets/`).
- **1.5 s referee timeout.** If the backend answers `/api/reinforcements` after
  1.5 s, the SDK tells the game to skip, but the backend may already have
  approved and recorded the placements (they then count in the score as placed
  defences).
- `/api/state` is sent without the Bearer header (the backend does not need it).
- The SDK schema caps `defenceId` at 3, so candidates with other ids are dropped
  locally ("Dropping malformed candidate") and the referee's
  `unknown defenceId` reason does not appear in `play`.
- `LOCAL_LLM_PORTS` includes 5000 and 8000 (the SDK's own port and the local
  backend); the relay port is exempt.
- `ALLOWED_HOSTS` and `bharatcode_base_url` are placeholders.
- `event_backend_url` hard-codes the current trycloudflare URL; it changes if
  the tunnel restarts.
- The interactive console always binds `127.0.0.1:5000`.
- Docs that no longer match the code: `autopilot-sdk/README.md` says the SDK
  calls the agent when `/api/team` returns (the code treats the game's first
  `/api/reinforcements` as the opening); several docs say the agent limit is
  3.5 s (it is 10 s for `play`, ≤ 4 s for `serve`); `docs/backend-integration.md`
  describes the older Unity → backend → SDK flow.
- Version string mismatch (`0.2.0` in code, `0.1.0` in `pyproject.toml`).

### 19.2 How to make common changes

| Change | Where |
|---|---|
| New field for the agent | `local_proxy._handle_reinforcements` / `_agent_state`; also `checks.sample_states` and `docs/PARTICIPANTS.md` |
| Agent time limit | `config.MAX_AGENT_TIME_S`, `SdkConfig.reinforcement_timeout_s`; `serve` cap in `server.make_server` |
| Referee/forward timeouts | `local_proxy.REINFORCEMENT_FORWARD_TIMEOUT_S`, `STATE_REPORT_TIMEOUT_S`, `LLM_RELAY_TIMEOUT_S`, `backend_timeout_s` |
| Something the SDK adds to `/api/team` | `local_proxy._handle_team` (and the backend's `ApiTeamRequest`) |
| Grid size / unit ids | `contracts/schemas.GRID_MAX_X/Y`, `MAX_DEFENCE_ID` (and the backend referee) |
| Sandbox rules | `network_guard/guard.install_agent_sandbox`, `domains.py`, `verifier/denylist.py` |
| Console menu | `interactive.menu_panel` and the loop in `interactive.main` |
| Run log content | `runlog.py` and the `_log(...)` calls in `local_proxy.py` |
| Game summary text | `local_proxy.format_results`, `_score_lines` |
| Ship it | apply the same change in `autopilot-sdk-binary/`; CI rebuilds the binaries |
| Anything the backend receives or returns | update [BACKEND_GUIDE.md](BACKEND_GUIDE.md) too |

## 20. Changelog

Add one line per change: date, what changed, sections touched.

| Date | Change | Sections |
|---|---|---|
| 2026-10-16 | First version, written from the code. | all |
