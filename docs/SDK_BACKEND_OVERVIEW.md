# Autopilot: how the SDK, the backend and the game fit together

A high-level overview for anyone working on one part (game engine, evaluator,
leaderboard). The exact endpoints and tables are in [COMPETITION.md](COMPETITION.md).

## The pieces

```
 participant's laptop                                          organisers' server (https://<site>)
┌──────────────────────────────────────────┐                 ┌─────────────────────────────────────────┐
│ Browser: the Unity WebGL game            │                 │ Caddy (HTTPS front door)                │
│   loaded from https://<site>/            │                 │   /            the game files           │
│   talks ONLY to http://127.0.0.1:5000    │                 │   /download/   SDK binaries, examples   │
│                │                          │                 │   /backend/*   → backend (FastAPI)       │
│                ▼                          │   HTTPS         │   /llm/v1/*    → LLM gateway             │
│ SDK binary `autopilot` (port 5000)  ─────┼────────────────▶│                                         │
│   • local proxy for the game             │                 │ Backend (FastAPI, one process)          │
│   • runs agent.py at every pause         │                 │   teams, logins, test cases, referee,   │
│   • picks the test case / eval slot      │                 │   scoring, game sessions, eval runs,    │
│   • writes run logs                      │                 │   /register page, /admin dashboard      │
│                ▲                          │                 │            │                            │
│ agent.py (participant's code)            │                 │            ▼                            │
│   get_reinforcements(state) → placements │                 │ Postgres                                │
│   may call the LLM ───── (through SDK) ──┼────────────────▶│ LLM gateway → Amazon Bedrock (Qwen)     │
└──────────────────────────────────────────┘                 └─────────────────────────────────────────┘
```

- **Game (Unity WebGL, build7):** draws and simulates the match. It never runs
  participant code and never talks to the server's API directly. Every API call
  goes to `http://127.0.0.1:5000`, the SDK on the same laptop.
- **SDK (`autopilot` binary, Python packed with PyInstaller):** sits between the
  game and the server. It answers the game's pause calls by running the
  participant's `agent.py` locally, then forwards everything to the backend.
  The participant's code stays on their laptop; only an eval uploads a copy.
- **Backend (FastAPI + Postgres):** the source of truth. It owns team accounts,
  test cases (public and hidden), and the referee that checks every placement
  against budget and free cells. It scores each game and stores the games, the
  eval runs and their logs.
- **LLM gateway:** lets agents use an LLM without any provider key. Each team has
  its own token, limited to 60 calls a minute.

## One game, step by step

1. **The participant starts the SDK** with no arguments. An interactive console
   asks for `agent.py` and shows a menu: PLAY (one public test case), PLAY ALL,
   EVAL (the hidden test cases), VERIFY. They pick, for example, PLAY TC03. The
   SDK says READY and starts listening on `127.0.0.1:5000`.
2. **They open the game page** and log in: the game sends
   `POST /api/login {teamID, password}`. The SDK forwards it to the backend. On
   success it keeps the login token (and the team's LLM token for the agent).
3. **Play is pressed:** the game sends `POST /api/team {teamID}`. The SDK adds what
   to play (`testCase: "TC03"`, or `evalId` + `evalSlot` in an eval) and the
   agent's hash, then forwards it with the token. The backend answers with the
   level (enemies, spawn and game settings, starting budget) and a new
   `sessionID`.
4. **The opening and the pauses:** about every 3 s of game time the game freezes
   and sends `POST /api/reinforcements` with a snapshot of the board (units,
   health, positions, counts).
   - The SDK turns it into the agent's `state`: the board plus the budget, unit
     costs, a 7 s time limit and `phase`, which is `"start"` on the first call
     (plus the whole wave) and `"pause"` after that.
   - It runs `get_reinforcements(state)` in a sandboxed worker process.
   - It sends the proposed placements to the backend's referee, which approves
     those that fit the budget and free cells and says why it refused the others.
   - The game gets the approved placements, places them and resumes.
5. **Game over:** the game sends `POST /api/results`. The backend completes the
   report from the snapshots it saw, scores it (out of 100) and stores it. The
   SDK prints the summary and score.
6. **Next game:** pressing play again repeats step 3. The SDK decides what comes
   next: the same test case (PLAY), the next one (PLAY ALL) or the next hidden
   slot (EVAL). The participant presses Ctrl+C in the SDK to return to its menu
   and pick something else, without restarting anything.

## Eval (the official score)

- EVAL plays every **active hidden test case** once.
- The SDK freezes a copy of `agent.py` when the eval starts, sends that source to
  the backend (`/api/eval/start`), and plays every hidden case with it.
- After the last case it uploads its run log (`/api/eval/{id}/finish`).
- The eval score is the mean over the hidden cases; a case not played counts 0.
- Organisers add or change hidden test cases in `/admin` during the event.

## What the backend stores

- **`teams`:** name, password (hashed), approved and banned flags, and the
  registration answers.
- **`test_cases`:** the level of every public and hidden test case.
- **`game_sessions`:** one row per game the SDK starts: team, test case, mode,
  eval slot, the agent's hash, and how many pauses the backend saw. It also keeps
  the referee-approved placements, the full result and the score.
- **`eval_runs`:** team, the hidden cases played, the agent.py source, the score,
  the uploaded log and the status.

## The participant side

- **Run logs:** every run writes `autopilot-runs/<time>_<mode>_<case>/` with
  `session.json`, `events.jsonl` (every exchange), `console.log` and each
  `agent.py` version that played.
- **Commands without the menu:** `verify`, `play --test-case TC03`,
  `play --all`, `eval`.

## What the game engine must do

- Show a **login screen** (team ID + password) and send `POST /api/login`.
- Send `POST /api/team {teamID}` when play is pressed, and **show the `error`
  text** if the reply is not 200.
- Keep `/api/reinforcements` and `/api/results` as they are.
- After game over, **go back to the play screen with the login kept**.
- Use `http://127.0.0.1:5000` as the base for every call.

That is all. The game does not choose test cases, run agents, keep scores or
handle tokens.

## Where the code is (branch `deploy-docker`)

| Part | Path |
|---|---|
| SDK proxy (game ⇄ agent ⇄ backend) | `autopilot-sdk/src/autopilot_sdk/local_proxy.py` |
| Interactive console | `autopilot-sdk/src/autopilot_sdk/interactive.py` |
| Agent sandbox | `autopilot-sdk/src/autopilot_sdk/runtime/` |
| Run logs, verify | `autopilot-sdk/src/autopilot_sdk/runlog.py`, `checks.py` |
| Game API (level, pauses, results) | `autopilot-backend/app/routers/game_api.py` |
| Login, registration, eval, admin API | `autopilot-backend/app/routers/competition.py` |
| Referee, scoring | `autopilot-backend/app/services/reinforcement_service.py`, `evaluator_service.py` |
| Game page wrapper | `autopilot-game-engine/build7/index.html` |
| Hosting | `docker-compose.yml`, `deploy/`, `DEPLOY.md` |

**To try it:** open https://13-200-183-134.sslip.io (test team
0014, password `hunter22pass`). Download the SDK from `/download/`, run it with
no arguments, and choose 1 PLAY.
