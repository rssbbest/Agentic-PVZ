# Autopilot unified


Hosting for the event (Docker Compose on AWS EC2, HTTPS, load test): see [DEPLOY.md](DEPLOY.md).

## Competition: accounts, test cases, eval

How games are chosen, played and recorded, with every endpoint and table:
[docs/COMPETITION.md](docs/COMPETITION.md).

| What | Where |
|---|---|
| Team registration (participants) | `https://<site>/register` |
| Admin dashboard: approve/ban teams, edit answers and passwords, view each team's games, eval runs, agent.py and run logs; add or edit public and hidden test cases; edit the registration form | `https://<site>/admin` (sign in with `ADMIN_SECRET`) |
| SDK | `autopilot` alone opens the interactive console: PLAY a test case, PLAY ALL, EVAL (hidden test cases), VERIFY |

Hidden test cases ship as examples in
`autopilot-backend/test_cases/hidden_test_cases.json` (loaded into an empty
database); replace them from the dashboard before the event. The SDK binaries for
Linux, Windows and macOS (arm64 and Intel) are built and smoke-tested by
`.github/workflows/build-binaries.yml`.

## Final test: start, secrets, run

**Host (this PC, `~/soc-work/autopilot-unified-test-branch`):**

Hosted on the event server (EC2 `ec2-13-200-183-134.ap-south-1`, t2.medium) with
Docker Compose in `~/autopilot`, served directly on ports 80/443 (Caddy, with a
Let's Encrypt certificate it renews itself). **Every push to `test_branch` deploys automatically**
(`.github/workflows/deploy.yml`): it builds and smoke-tests the Windows and Linux
SDK binaries, copies the code, game build and binaries to the server, rebuilds,
restarts and reloads the public test cases. The server's `.env` is never
touched. Freeze deploys with `gh variable set DEPLOY_PAUSED --body true`; deploy
by hand with `deploy/push-to-server.sh ubuntu@<host> <key>`. Details: [DEPLOY.md](DEPLOY.md).

Current public URL: **https://13-200-183-134.sslip.io**
(registration `/register`, admin dashboard `/admin`). `13-200-183-134.sslip.io` is
a free name that always resolves to the server's IP 13.200.183.134; to use an own
domain, point its A record at that IP and run
`bash ~/autopilot/deploy/serve-direct.sh <domain>` on the server.

**Teams and test cases are separate.** A team ID (4 digits) is only a login:
teams are added in `/admin` (Teams → *Import from Unstop* creates them in bulk
with generated passwords). The SDK picks what to play: a public test case
(TC01–TC10), or the hidden ones in an eval. Test cases load when the backend
starts and are edited in `/admin` → Test cases.

**Organisers and volunteers:** [TEAM_SDK.md](TEAM_SDK.md) (links, dashboard, monitoring, choosing winners).

**Participant (any laptop):** download the binary from `/download/`, run it with
no arguments, log in on the game page. Nothing to export: the LLM gateway is the
server's `/llm/v1` and each team's LLM token comes with its login. Full guide:
[README_PARTICIPANT.md](README_PARTICIPANT.md).

| What | URL |
|---|---|
| Game (open in Chrome or Firefox) | https://13-200-183-134.sslip.io/ |
| Backend, for `--backend` | https://13-200-183-134.sslip.io/backend |
| Admin dashboard (`ADMIN_SECRET` from the server's `.env`) | https://13-200-183-134.sslip.io/admin |
| LLM gateway (the SDK relays agents' calls here) | https://13-200-183-134.sslip.io/llm/v1 |
| SDK binaries, editor package, example agents, guide | https://autopilot.gdgnsut.com/download/ |

Without the menu, for scripts: `./autopilot-macos-arm64 play --agent agent.py --team 0042 --all`
(asks for the password). Organisers can still set `AUTOPILOT_GATEWAY_KEY` by hand
(the shared organiser token is `EVENT_GATEWAY_TOKEN` in the server's `.env`).

## Quick start (about two minutes)

Needs Linux (x86_64), Docker, and `gh auth login` (the game build is a release asset
of this private repository). `uv` makes setup faster but is optional.

```bash
git clone -b test_branch https://github.com/nextgen-user/autopilot-unified.git
cd autopilot-unified
cp .env.example .env          # optional: put an LLM key in AUTOPILOT_LLM_API_KEY
./quickstart.sh               # database, backend, SDK, game, live monitor
```

Open **http://localhost:8000** (game) and **http://localhost:8020** (live monitor).
Type a 4-digit ID from the table quickstart prints (0003 = default level,
0004-0013 = test cases TC01-TC10), fill the three URL boxes with
`http://127.0.0.1:5000/api/team`, `.../api/reinforcements` and `.../api/results`
(build7 needs them: [issue](docs/issues/build7-empty-url-boxes.md)) and press play.
`./quickstart.sh smoke` checks the stack without a browser; `./quickstart.sh down` stops it.
See [docs/QUICKSTART.md](docs/QUICKSTART.md) for the demo walkthrough, LLM tool calling and tests.

---

One repository for the participant SDK, backend, Unity game, browser control page,
simulator/evaluator adapter, and local organiser LLM gateway. A participant
writes `agent.py`; the SDK validates its placements; Unity plays assigned waves;
the backend records scores and a leaderboard.

```text
Browser / participant CLI
          │ upload agent.py
          ▼
Backend + Postgres ──► SDK worker ──► validated placements
          │                               │
          └───────────────┬───────────────┘
                          ▼
                Simulator job queue ◄──► Unity native / WebGL
                          │                    │
                          │              frames + result
                          ▼
                    Evaluator → backend → scores / replay / leaderboard

Agent → LLMClient (autopilot_sdk.llm) → organiser gateway → OpenAI-compatible LLM
                                         (provider key stays in gateway env)
```

The gateway serves the standard chat completions API and forwards it to the
configured provider (Bedrock Qwen at the event). Scores use the development formula
`80 × killed/totalEnemies + 20 × defencesAlive/defencesPlaced`.

## Requirements

- **Linux** for the supplied launcher (uses `/proc` and native Linux players).
- Python **3.10–3.12**; 3.11 tested. `uv` is supported and selects Python 3.11.
- Docker running, with permission to use it; `make` and Git.
- For real Unity gameplay: activated **Unity 6000.1.17f1**, with Linux Build
  Support and/or WebGL Build Support. Unity and its licence are installed
  separately through Unity Hub. A desktop/display is needed for the native game.
- Optional OpenAI-compatible LLM endpoint for the LLM example. Baseline agents need no API key.

## Fresh clone and configuration

```bash
git clone https://github.com/nextgen-user/autopilot-unified.git
cd autopilot-unified
make setup
```

`make setup` creates `.venv`, installs the Python dependencies and copies
`.env.example` to `.env` if it does not already exist. With no `uv`, it uses
`python3`; choose another supported interpreter with
`AUTOPILOT_PYTHON=python3.11 make setup`.

Edit **`.env`**:

```dotenv
AUTOPILOT_LLM_BASE_URL=https://api.openai.com/v1
AUTOPILOT_LLM_API_KEY=your_key_here
AUTOPILOT_LLM_MODEL=gpt-4o-mini
UNITY_EDITOR_PATH=/path/to/6000.1.17f1/Editor/Unity
```

The launcher reads this file directly; do not source it into your shell.
Leave the provider key blank for baseline play. Leave `POSTGRES_PASSWORD`,
`SIMULATOR_SECRET` and `ADMIN_SECRET` blank to generate private local values in
`.runtime/config.json`. `.env` and `.runtime` are ignored by Git. The committed
`.env.example` contains no keys. Provider credentials are passed only to the
**gateway process**, not the SDK's environment; this is not filesystem isolation.

## End to end with real Unity

```bash
make build           # native Linux player; may take several minutes on first import
make build-webgl     # browser player; requires WebGL module and additional build time
make start           # starts database, backend, SDK, gateway, adapter and native player
make status
```

Open **http://localhost:8002**:

1. Click **Open Unity game** and keep the browser game tab open.
2. Choose a wave. Click **Load formation**, or select a defence and click grid
   cells. Right-click removes a defence.
3. Click **Play formation in Unity**. The real game uses those placements at 2x
   speed. Counters and results appear on the control page. Practice does not
   enter the leaderboard.
4. Click **Replay battle** after completion.
5. Click **Run test agent · 50 waves**, **Run LLM agent · 50 waves**, or upload
   your own `agent.py`. The progress grid shows all 50 backend enemy configs;
   click a run to inspect or replay it.

The native player handles scored jobs automatically at 20x speed. Native and
WebGL players can both consume scored jobs. The browser must stay active for its
worker to progress; use the native player for unattended local runs.

If only the WebGL module is available, build WebGL, start the stack and open the
browser game. The launcher prints a missing-native-player instruction but leaves
services running for the browser worker. Do not submit scored jobs before a
worker is connected.

## End to end without Unity

```bash
make portable
make smoke
```

Open http://localhost:8002 and use **Start portable practice** or the test-agent
submission button. This mode runs a **simplified Python combat simulation** with
balance values read from Unity prefabs. Its gameplay/scores are not equivalent
to real Unity. To switch modes, wait for submissions to finish, then run
`make stop` followed by `make start` or `make portable`.

## Participant commands

```bash
source .venv/bin/activate
export AUTOPILOT_BACKEND_URL=http://127.0.0.1:8000

autopilot verify --agent integration/examples/test_agent.py
autopilot register --name "My team"
autopilot submit --agent integration/examples/test_agent.py --team-id TEAM_ID --wait-timeout 900
autopilot status SUBMISSION_ID --runs
autopilot leaderboard
```

Replace the numeric IDs with those printed by the commands. For the LLM
example submit root `agent.py` instead. See [participant instructions](docs/PARTICIPANTS.md)
for the agent contract, IDs, coordinates and what verification checks.

## Tests

```bash
make test                  # SDK + integration + backend automated tests
make smoke                 # fresh baseline submission; validates all 50 completed runs
make smoke-llm             # fresh baseline + LLM submissions; needs a working endpoint/key/model
make unity-smoke           # SDK -> real Unity practice -> counters/evaluator/replay
```

Run smoke commands against an already started stack, with a connected player
if in Unity mode. Reports go to ignored `artifacts/live-test-report.json`.
Infrastructure/provider failures fail the smoke check; no fake fallback score is
used. Full submissions in Unity mode can take several minutes.

Optional checks:

```bash
.venv/bin/python -m integration.live_test --negative --skip-llm
# Above deliberately stops this stack's SDK, verifies failures, restarts and retries.
.venv/bin/python -m integration.unity_smoke --llm
.venv/bin/python autopilot-sdk/examples/security-demo/showcase.py
.venv/bin/python -m playwright install chromium
.venv/bin/python -m integration.browser_test --headless
# Browser check requires the corrected WebGL build, running Unity-mode services,
# and browser OS dependencies (playwright install-deps chromium if needed).
```

[Validation notes](docs/VALIDATION.md) distinguish previous Unity evidence from
checks performed on this unified checkout.

## Services, logs and stopping

| Component | Address |
| --- | --- |
| Control page, simulator, evaluator | http://localhost:8002 |
| Corrected WebGL game | http://localhost:8002/engine/?autopilot=1 |
| Backend API docs | http://localhost:8000/docs |
| SDK | http://localhost:8001/health |
| LLM gateway | http://localhost:8004/health |
| Postgres | 127.0.0.1:54339 by default |

Services bind to loopback. Service ports 8000, 8001, 8002 and 8004 must be free;
stop older stacks before starting this one. Database settings are configurable
in `.env`; this repo uses its own `autopilot-unified-db` container and
`autopilot-unified-pgdata` volume. Logs, uploaded agents, replay files, gateway
cache and process metadata live in `.runtime/`.

```bash
make stop                  # stop managed services and native player; preserve DB/replays
# Optional: stop the default database too
docker stop autopilot-unified-db
```

Close any browser game tabs manually. Do not change the database password after
creating its persistent volume without updating PostgreSQL itself. Run
`make stop` and restart to apply other `.env` changes, after pending work finishes.

## Source map and handoff

| Directory/file | Purpose |
| --- | --- |
| `autopilot-sdk/` | Participant CLI, Python agent contract, validation and runner service |
| `autopilot-backend/` | Submission scheduling, Postgres persistence, evaluator calls and leaderboard |
| `autopilot-game-engine/` | Unity source, combat corrections, HTTP bridge and build helpers |
| `integration/` | Launcher, settings, adapter, gateway, portable combat and browser frontend |
| `agent.py` | LLM formation example; no provider key embedded |
| `.env.example` | Documented local configuration template |

Read [changes versus cloned code](docs/CHANGES_FROM_UPSTREAM.md),
[known bugs and limitations](docs/KNOWN_BUGS.md), and
[API contracts](docs/ARCHITECTURE.md). Original source revisions are recorded in
[upstream-revisions.json](docs/upstream-revisions.json). Existing component
READMEs are retained as upstream background; use this root README for unified
setup and the current limitations.
