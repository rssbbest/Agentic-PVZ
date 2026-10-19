# Local run: game, SDK binary, backend and database

How to run the live reinforcement game end to end on one Linux machine:
Postgres in Docker, the backend, the SDK binary running a participant agent, and
the Unity WebGL game (`autopilot-game-engine/build7`).

```text
Browser (game, :8000) ──▶ SDK binary "autopilot play" (:5000) ──▶ Backend (:8010) ──▶ Postgres (:5433, Docker)
                              └ runs agent.py locally
```

| Component | How it's started | Port |
| --- | --- | --- |
| Postgres | Docker container `autopilot-e2e-db` | 5433 |
| Backend | `uvicorn` from `autopilot-backend/` | 8010 |
| SDK | `autopilot-sdk/dist/autopilot-linux-x86_64 play` | 5000 |
| Game | `autopilot-game-engine/build7/server.py` | 8000 |

The game sends every request to `127.0.0.1:5000`. `autopilot play` listens
there, runs `get_reinforcements(state)` from the agent file, and forwards the
candidate placements, plus all other game calls, to the backend. The backend
referee approves or drops each placement. The agent source is never uploaded.

All commands run from the repository root unless they start with `cd`.

## A. One-time setup

**A1. Python environment.** On a fresh clone:

```bash
make setup
```

This creates `.venv` with the backend and SDK dependencies.

**A2. Build the SDK binary.** Output: `autopilot-sdk/dist/autopilot-linux-x86_64`.
Rebuild after any SDK code change:

```bash
uv pip install --python .venv/bin/python pyinstaller     # once
.venv/bin/python autopilot-sdk/build_binary.py
```

PyInstaller builds only for the current OS; Windows and macOS binaries must be
built on those systems.

**A3. Create the database container,** with a named volume so data survives
restarts:

```bash
sudo systemctl start docker
docker run -d --name autopilot-e2e-db \
  -e POSTGRES_PASSWORD=password -e POSTGRES_DB=zombie_defense \
  -v autopilot-e2e-pgdata:/var/lib/postgresql/data \
  -p 127.0.0.1:5433:5432 postgres:16-alpine
```

If Docker reports the name is already in use, the container exists; go to
section B.

**A4. Create a game team.** Once per database, with the backend running (B2).
The game's team box takes exactly four characters, so the seeded `TEAM1` cannot
be typed. Create `TEAM` and copy `TEAM1`'s level into it:

```bash
curl -s -XPOST localhost:8010/teams -H 'content-type: application/json' -d '{"name":"TEAM"}'; echo
curl -s -XPOST localhost:8010/api/team -H 'content-type: application/json' -d '{"teamID":"TEAM1"}' \
 | python3 -c 'import sys,json;d=json.load(sys.stdin);print(json.dumps({k:d[k] for k in ("defencePlacementData","enemies","spawnSettings","gameSettings")}))' \
 | curl -s -XPOST localhost:8010/api/team-data/TEAM -H 'content-type: application/json' -H 'X-Admin-Secret: dev-admin' -d @-; echo
```

`Team name already exists` means it is already done. Team names match
case-insensitively.

## B. Every run

Use three terminals, started in this order.

**B1. Database:**

```bash
sudo systemctl start docker          # only if Docker isn't running
docker start autopilot-e2e-db
```

**B2. Backend (terminal 1):**

```bash
cd autopilot-backend
DATABASE_URL=postgresql+asyncpg://postgres:password@127.0.0.1:5433/zombie_defense \
UPLOAD_DIR=$PWD/uploads ADMIN_SECRET=dev-admin SIMULATOR_SECRET=dev-sim \
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Wait for `Application startup complete`. On start the backend creates any
missing tables and seeds the 50 enemy configs and the `TEAM1`/`team1` teams.
Check with `curl localhost:8010/health` (`{"status":"ok"}`). API docs:
http://localhost:8010/docs.

**B3. SDK binary with an agent (terminal 2):**

```bash
autopilot-sdk/dist/autopilot-linux-x86_64 play \
  --agent autopilot-sdk/examples/reinforcement_agent.py \
  --backend http://127.0.0.1:8010
```

Replace `--agent` with your own file. Check with `curl localhost:5000/health`.
The command exits with an error if the agent fails the static scan or has no
`get_reinforcements`.

**B4. Game (terminal 3):**

```bash
python3 autopilot-game-engine/build7/server.py
```

It opens http://localhost:8000 in your browser.

**B5. Play.** Type `TEAM` in the team ID boxes, fill the three URL fields with
`http://127.0.0.1:5000/api/team`, `http://127.0.0.1:5000/api/reinforcements` and `http://127.0.0.1:5000/api/results`, where the SDK listens, and submit. build6 used these when the fields
were empty; build7 does not ([issue](issues/build7-empty-url-boxes.md)).

## C. Expected behaviour

| Where | Expected |
| --- | --- |
| Game | The level loads; about every 10 s of game time it pauses for ~5 s and new defences appear |
| Terminal 2 (SDK) | `POST /api/team`, then `Reinforcement: N candidate(s), M approved` at each pause |
| Terminal 1 (backend) | `POST /api/team`, `POST /api/reinforcements`, and `POST /api/results` at the end |

Stored game results:

```bash
docker exec autopilot-e2e-db psql -U postgres -d zombie_defense \
  -c "select id, data from results_log order by id desc limit 5;"
```

Test the SDK and backend without the game by sending a snapshot the way the
game does:

```bash
curl -s -XPOST localhost:5000/api/reinforcements -H 'content-type: application/json' -d '{"teamID":"TEAM","sessionID":"t","elapsedSeconds":20,"counts":{"activeDefenceCount":0,"activeEnemyCount":1,"enemiesQueued":0,"totalEnemiesInWave":1},"defences":[],"enemies":[{"id":"E","type":"EnemClown","health":45,"maxHealth":100,"position":{"x":3,"y":2},"isAttacking":false,"isDead":false}],"candidatePlacements":[]}'
```

Expected: `{"placeDefences": true, ...}`.

## D. Writing an agent

An agent is a Python file with a module-level `get_reinforcements`:

```python
# my_agent.py
def get_reinforcements(state: dict) -> list[dict]:
    enemies = [e for e in state["enemies"] if not e["isDead"] and e["health"] > 0]
    if not enemies:
        return []
    occupied = {(d["position"]["x"], d["position"]["y"])
                for d in state["defences"] if d["health"] > 0}
    target = min(enemies, key=lambda e: e["position"]["x"])
    for x in range(11):
        if (x, target["position"]["y"]) not in occupied:
            return [{"defenceId": 1, "defenceGridPosition": {"x": x, "y": target["position"]["y"]}}]
    return []
```

- `state` contains `elapsedSeconds`, `counts`, `defences[]` and `enemies[]`.
  Each unit has `id`, `type`, `health`, `maxHealth` and `position {x, y}`;
  enemies also have `isAttacking` and `isDead`.
- It is also called once when the level loads, before any enemy, with
  `state["phase"] == "start"`, no enemies and the coming wave in
  `state["upcomingEnemies"]` (`enemyId`, `count`, `allowedRows`). What it
  returns then is the opening board; at the pauses `phase` is `"pause"`.
- Grid: x is 0–10, y is 0–5. Enemies come from the right.
- Defence IDs: 0 penguin, 1 soldier, 2 archer, 3 shield.
- Return `[]` to skip. The backend drops off-grid, occupied and duplicate cells
  without moving them.
- Limit: 3.5 s per call. Errors, timeouts and malformed output place nothing
  and the game resumes.
- LLM provider SDKs and URLs are not allowed. Agent code can reach only
  loopback and the LLM gateway, and cannot start programs or processes;
  `autopilot-sdk/examples/security-demo/showcase.py` shows each rule.
- Check with `autopilot-sdk/dist/autopilot-linux-x86_64 verify --agent my_agent.py`,
  then pass the file to B3.
- The file is re-read at every pause, so edits apply without a restart.

## E. Stopping

- Ctrl+C in terminals 1–3.
- `docker stop autopilot-e2e-db`. Data, including the `TEAM` team, stays in
  the `autopilot-e2e-pgdata` volume.
- To wipe everything:
  `docker rm -f autopilot-e2e-db && docker volume rm autopilot-e2e-pgdata`,
  then repeat A3 and A4.

## F. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `failed to connect to the docker API` | Docker daemon is off | `sudo systemctl start docker` |
| Backend: connection refused on 5433 | Database not started | B1, then wait a few seconds |
| `curl -s` prints nothing, or a JSON parse error | That service isn't running | Check `/health` on 8010 and 5000 |
| `address already in use` | Port taken | `ss -ltnp \| grep -E ':(5000\|8000\|8010)'` and stop the old process |
| Game: "Could not reach the server" | SDK (B3) not running | Start B3 |
| SDK: "Backend unreachable" | Backend (B2) not running, or wrong `--backend` | Start B2 or fix the URL |
| Game: "Team 'X' not found" | Team not in this database | Run A4 with that name |
| Game runs but nothing is placed | Agent skipped, errored or failed the scan | Read the warnings in terminal 2; run `verify` |

Do not run the backend on port 8000: `server.py` uses it.
