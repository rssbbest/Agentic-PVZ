# Autopilot: organiser and volunteer guide

Everything the event team needs during the hackathon: where things are, what
each one is for, how to test them, and what to do when something goes wrong.
Participants have their own guide: [README_PARTICIPANT.md](README_PARTICIPANT.md).

## Links

| What | Link | Who uses it |
|---|---|---|
| **Admin dashboard** | https://13-200-183-134.sslip.io/admin | organisers, volunteers (needs `ADMIN_SECRET`) |
| Game (event domain, via Cloudflare) | https://autopilot.gdgnsut.com/ | participants, the big screen |
| Game | https://13-200-183-134.sslip.io/ | participants (Chrome or Firefox) |
| Downloads: SDK, starter `agent.py`, guide | https://autopilot.gdgnsut.com/download/ | participants |
| One-line install (macOS, Linux) | `curl -fsSL https://autopilot.gdgnsut.com/install.sh \| sh` | participants |
| One-line install (Windows PowerShell) | `irm https://autopilot.gdgnsut.com/install.ps1 \| iex` | participants |
| Participant guide (formatted in a browser) | https://autopilot.gdgnsut.com/guide | send this link to teams (`?raw` or curl: the Markdown) |
| Player guide | https://autopilot.gdgnsut.com/guide/event | detailed game rules and unit stats |
| Backend health | https://13-200-183-134.sslip.io/backend/health | anyone: `{"status":"ok"}` means up |
| Backend API base | https://13-200-183-134.sslip.io/backend | the SDK; scripts |
| LLM gateway | https://13-200-183-134.sslip.io/llm/v1 | the SDK relays agents' LLM calls here |
| Leaderboard (live, public) | https://13-200-183-134.sslip.io/leaderboard/ | put it on the big screen |
| Leaderboard (final, private) | https://13-200-183-134.sslip.io/leaderboard/?board=private | sealed until you publish it in the dashboard |
| Code (private) | https://github.com/nextgen-user/autopilot-unified (branch `test_branch`) | developers |

`ADMIN_SECRET` is in the server's `~/autopilot/.env`. Share it only with the
people who run the dashboard; never post it in a group chat.

## How it fits together

- A **team** logs in with a 4-digit **team ID** and a **password**. Team IDs are
  logins only; they say nothing about the level.
- **Test cases** are the levels: public TC01–TC10 and **hidden** ones (the
  final board). Each sets its enemies, spawn order, timing and starting
  `coins`. Both kinds are edited in the dashboard.
- The participant runs the **SDK** on their laptop with their `agent.py`. The SDK
  picks the test case, runs the agent at every pause, and talks to the backend.
  The game in the browser only draws the match.
- **PLAY** (one public test case) is practice: saved, never ranked.
- **PUBLISH PUBLIC** / **PUBLISH PRIVATE** play every active public / hidden
  test case once with a frozen copy of `agent.py`. The run's score is the
  **sum** over its cases (an unplayed case counts 0) and goes on the public /
  private leaderboard. The backend keeps the score, the `agent.py` and the run
  log of every run. PUBLISH PRIVATE works only while **PUBLISH PRIVATE open** is
  ticked in the dashboard (Leaderboard section; off by default).
- **A game's score:** a win scores out of 100 (win 25, kills 25, defences
  standing 25, speed 10, coins left 15); a loss scores only its kills (up to 25).

## The admin dashboard

Sign in with `ADMIN_SECRET` (paste it, press Enter). Tabs:

| Tab | What you do there |
|---|---|
| **Overview** | counts: teams, waiting for approval, banned, games played, evals; latest eval runs |
| **Teams** | search; **Import from Unstop**; add one team; approve, **ban/unban**; click a team for its details, games, eval runs, notes, and to **set a new password** |
| **Eval runs** | every eval: team, score, per hidden case; open one to read its `agent.py` and download its run log |
| **Test cases** | public and hidden levels: add, edit (enemies, rows, settings), switch on/off, reorder |
| **Registration form** | the public `/register` form; keep **Registration open** off (teams come from Unstop) |

### Adding the teams (once, before the event)

1. Export the registrations from Unstop (Excel or CSV).
2. **Teams → Import from Unstop**, choose the file, check the column guesses
   (team name, member name, email, phone, college), then **Create N teams**.
3. A **logins CSV** downloads at once: team ID, team name, password, emails.
   **The passwords are only ever shown here**; keep the file safe and send each
   team its own line only.
4. New registrations later: import the updated file again. Teams already there
   (same name) are skipped, so only the new ones are created.

### During the event

| Situation | Do this |
|---|---|
| A team lost its password | Teams → the team → **New password** → Save; tell them the new one |
| A team cheats or misbehaves | Teams → **Ban** (they can't log in or play); **Unban** undoes it |
| Check a team really played | Teams → the team: every game with score, pauses, agent hash; Eval runs → its `agent.py` and run log |
| Change a hidden test case | Test cases → edit or add; the next eval uses it (evals already run keep their score) |
| "Did the server go down?" | open `/backend/health`; then see *Server* below |

## Evaluation and monitoring during the event

**Scores**

- **Only complete runs count**, on both boards: every test case of the board
  played to the end. Each test case of a run starts once; a team that reloads or
  quits mid-game must publish again.
- **Live board** = each team's best PUBLISH PUBLIC run (sum over the public cases).
- **Official score = the private board**: each team's PUBLISH PRIVATE run (sum
  over the hidden cases), the **best** or the **latest** one per team (dashboard
  setting, default best). A private run is locked to its `agent.py`: a team
  plays it once, and an interrupted run resumes with the same file. To let a
  team play again (a genuine crash), **Void run** in its run dialog; **Close run**
  / *Close running private runs* end runs at the end of the event (an incomplete
  run does not count). It stays sealed until you tick **publish** in the dashboard.
- **Hidden cases: same agent, same score.** Every game of a hidden test case
  spawns identically (seeded per test case); public cases spawn in random rows,
  as in build10. The game (build12) runs at a fixed step with no fast-forward,
  and the LLM gateway answers at temperature 0 (and caches answers).
- **Checked results:** a game's report must come with the team's login and be
  possible: no more game time than real time, kills within the wave, a win
  only after the whole wave spawned and died, the pauses the backend saw, and
  only units the referee approved. A report that fails scores 0, and the
  backend log says why (`Result refused for session ...`).
- PLAY games are practice. They show in each team's page but are never ranked.
- **Eval runs** tab: every published run (board public or private) with its score
  per case, the exact `agent.py` that ran (frozen at its start) and its run log.
- The leaderboard reads the same data (`/backend/api/leaderboard/public`,
  `/backend/api/leaderboard/private`).

**Releasing an SDK fix during the event**

Push to `test_branch`: the deploy builds the Windows, Linux and macOS programs and the
Python package, and writes `download/sdk-manifest.json` (each program's sha256,
the package's fingerprint). From then on the backend refuses games and runs from
any other SDK build (HTTP 426), and every SDK updates itself on that reply or at
its next start: it downloads the new program (or pip-installs the package),
checks the sha256, restarts, and carries on logged in with the same PLAY or
PUBLISH run (a published run resumes where it stopped). `autopilot update`
updates on demand. The macOS programs are committed (`autopilot-sdk-binary/bin`):
rebuild them with *Build SDK binaries* (macOS) when the SDK changes; Macs install
with Python by default and update through the package anyway. SDKs installed
before the automatic updates get a message to run the install line again.

**Choosing winners**

The game runs on the participants' laptops, so the checks above stop made-up
results but not a team that rewrites the game itself. Re-run the finalists:

1. Leaderboard (private tab) or Teams tab → **Best private** heading, or Export CSV.
2. For each of the top teams: Eval runs tab → its private run → copy its
   `agent.py`. On an organiser laptop, save it as `agent.py`, log in with an
   organiser team and press **3 PUBLISH PRIVATE** (void that organiser team's
   earlier private run first). The score must match the team's (same agent, same score; a small
   difference can come from an LLM call that timed out). A big gap means the
   team's run was not genuine.
3. For each top team, also open its private run (Eval runs tab):
   - read its `agent.py` (no hardcoded answers per level, nothing outside the rules);
   - download the run log and check that every hidden case was really played
     (pauses, the agent's answers, the referee's decisions, the result).
4. Ask the winners for their `autopilot-runs/` folder; it should match the log
   the backend kept.

**Before the event starts**

- Delete the test teams (Teams → the team → delete); their games and runs go with them.
- Delete or ignore runs from before the scoring change (an old private run was
  scored as an average, the board now sums).
- Keep **PUBLISH PRIVATE open** unticked until the final phase.
- Check every hidden test case: enemies, `coins`, `spawnSequence`, intervals.

**What to watch, every half hour or so**

| Check | Where | Healthy |
|---|---|---|
| Server up | https://13-200-183-134.sslip.io/backend/health | `{"status":"ok"}` |
| Activity | dashboard Overview | games and published runs going up |
| Errors in the backend | `docker compose logs --since 30m backend \| grep -ciE "error\|traceback"` | 0, or a few |
| LLM refusals | `docker compose logs --since 30m gateway \| grep "refused 429"` | few; each line names the team over 60 calls a minute |
| LLM provider errors | `docker compose logs --since 30m gateway \| grep "upstream error"` | none (if many: Bedrock quota or outage) |
| CPU and memory | `docker stats --no-stream` | backend well under 100% CPU; memory not near the 3.8 GB total |
| CPU credits | AWS console → EC2 → the instance → Monitoring → **CPU credit balance** | not heading to 0 (or the instance is in Unlimited mode) |

If the game feels slow for everyone: check CPU credits first, then `docker stats`.
If one team says it is slow: it is their agent (each pause allows 15 s) or their LLM calls.

## Testing everything (about 20 minutes, before the event)

1. **Dashboard:** sign in. Import a small test sheet with 2 made-up teams; keep the logins CSV.
2. **Participant flow:** download the SDK for your laptop and `agent.py` from
   `/download/`, run the SDK with no arguments, then:
   - **4 VERIFY**: all lines ✓.
   - **1 PLAY** → `TC01`, open the game, log in with a test team, press ▶.
     The SDK shows every pause (agent's proposal, referee's decision, LLM calls)
     and the score at the end. In the dashboard, that team shows 1 game.
3. **Publish:** tick PUBLISH PRIVATE open, then **2 PUBLISH PUBLIC** and **3 PUBLISH PRIVATE**, press ▶ for each case. Eval runs shows the total,
   the `agent.py` and the log.
4. **Ban:** ban the test team; its next game is refused. Unban it.
5. **Hidden test case:** change one; start a new eval; it plays the changed level.
6. **Clean up:** delete the test teams (Teams → the team → its page; their games go with them).

Load test (no LLM calls, costs nothing; run it **before** importing the real
teams, since its throwaway teams use up team IDs):

```bash
python deploy/loadtest.py https://13-200-183-134.sslip.io/backend <ADMIN_SECRET> --teams 300
```

Last result (16 Oct, direct to the server): 300 participants, 12,720 requests,
0 errors, pause replies 37 ms typical / 125 ms p99; 300 logins at the same
instant all succeeded.

## Server (for whoever holds the server key)

EC2 `ec2-13-200-183-134.ap-south-1.compute.amazonaws.com`, user `ubuntu`, the
stack in `~/autopilot` (Docker Compose: `web` = Caddy with HTTPS, `backend`,
`gateway`, `db`).

```bash
ssh -i <key.pem> ubuntu@ec2-13-200-183-134.ap-south-1.compute.amazonaws.com
cd ~/autopilot
docker compose ps                         # all should be Up / healthy
docker compose logs -f --tail 100 backend # live backend log
docker compose restart backend            # last resort: matches in progress lose their coin ledger
```

- **Freeze deploys for the event** (every push to `test_branch` deploys):
  `gh variable set DEPLOY_PAUSED --body true`; after: `--body false`.
- **Don't stop the instance the night before**: a t2 loses its saved CPU credits.
  Rebooting is fine.
- Never delete or overwrite `~/autopilot/.env` (the secrets); copies of earlier
  versions are next to it as `.env.bak-*`.

## Backend endpoints

Base: `https://13-200-183-134.sslip.io/backend`. Full contract: [docs/COMPETITION.md](docs/COMPETITION.md).

**Used by the SDK and the game**

| Endpoint | Purpose |
|---|---|
| `POST /api/login` | team ID + password → login token and the team's LLM token |
| `POST /api/team` | start a game: the level and a session ID (the SDK names the test case) |
| `POST /api/reinforcements` · `POST /api/state` | each pause: the agent's placements → the referee's decision |
| `POST /api/results` | game over → score |
| `GET /api/test-cases` | the public test cases |
| `GET /api/eval/info` · `POST /api/eval/start` (`board`: public/private) · `GET /api/eval/{id}` · `POST /api/eval/{id}/finish` | published runs (PUBLISH PUBLIC / PRIVATE) |
| `GET /api/me` | the logged-in team's own record |
| `GET /health` | up or not |

**Admin** (header `X-Admin-Secret: <ADMIN_SECRET>`; the dashboard uses these)

| Endpoint | Purpose |
|---|---|
| `GET /admin/api/overview` | the counts on the Overview tab |
| `GET`/`POST /admin/api/teams` · `POST /admin/api/teams/import` | list teams with stats; add one; bulk import |
| `GET`/`PATCH`/`DELETE /admin/api/teams/{id}` | one team; edit (name, details, password, approved, banned); delete |
| `GET /admin/api/sessions/{id}` | one game in full |
| `GET /admin/api/evals` · `/evals/{id}` · `/evals/{id}/log` | eval runs, their `agent.py`, their run log |
| `GET`/`POST`/`PUT`/`DELETE /admin/api/test-cases[/{code}]` | public and hidden test cases |
| `GET`/`PUT /admin/api/registration-form` | the public form |

Example, the scoreboard data for the evaluator or leaderboard:

```bash
curl -s -H "X-Admin-Secret: $ADMIN_SECRET" https://13-200-183-134.sslip.io/backend/admin/api/teams \
  | python3 -c 'import json,sys; [print(t["teamID"], t["name"], t["bestEval"], t["lastEval"]) for t in json.load(sys.stdin)]'
```

## When participants ask

| They say | Answer |
|---|---|
| "The game does nothing after ▶" | Is the SDK showing **READY**? In Chrome, did they click **Allow** (local network access)? |
| "Login refused" | Check team ID and password; reset the password in the dashboard if needed |
| "VS Code underlines `autopilot_sdk`" | Step 1.3 of their guide: install the SDK with Python in a venv and select that interpreter |
| "LLM call failed: HTTP 429" | More than 60 LLM calls a minute for their team; make fewer |
| "No placements this pause" | Their agent crashed or was too slow; the reason is printed in their SDK window |
| "Port 5000 is in use" | Another copy of the SDK is open; close it |
| Mac: "cannot be opened" / "not verified" | System Settings → Privacy & Security → **Open Anyway**, then double-click again (once; the program is not signed by Apple) |
| Linux: double-click does nothing | Run it from a terminal: `./autopilot-linux-x86_64` in the unzipped folder |
| "The program won't run" (old Linux, ARM, odd setup) | The one-line install falls back to Python by itself; or the guide's step 1.3 (venv + pip) |
| "My agent.py has an error" | The SDK shows the line and the reason in a red box and keeps running; fix the file and press Enter (or choose again) |
