# Competition flow: SDK modes, login, test cases, eval

How a participant's game is chosen, played, scored and recorded. For the game
engine (login screen), the evaluator/leaderboard, and organisers.

## The flow

```
participant                SDK (their laptop, 127.0.0.1:5000)             backend (hosted)
───────────                ──────────────────────────────────             ────────────────
starts `autopilot`  ──▶    menu: PLAY (test case) · PLAY ALL · EVAL · VERIFY
opens the game page
logs in (game)      ──▶    POST /api/login ─────────────────────────────▶ checks password, approved, not banned
                           keeps token + LLM token  ◀──────────────────── {token, teamID, teamName, llmToken}
presses play        ──▶    POST /api/team + testCase | evalId,evalSlot ─▶ that test case's level, new sessionID
                           runs agent.py at the opening and each pause
                           POST /api/reinforcements (candidates) ──────▶ referee: budget, free cells
game over           ──▶    POST /api/results ───────────────────────────▶ score → game_sessions row
                           (eval, after the last hidden case)
                           POST /api/eval/{id}/finish (run log) ────────▶ eval score = mean over hidden cases
```

**The SDK, not the game, decides what is played.** The game sends the same
`POST /api/team` as today; the SDK adds the test case (play) or the eval run and
slot (eval). The game only needs the team (from the login) and the play button.

## SDK modes

| Mode | What it plays | agent.py | Recorded |
|---|---|---|---|
| `play` + test case | one public test case, every press of play | live: edits apply at the next pause | each game in `game_sessions` (mode `play`) |
| `play --all` | each public test case once, in order; prints the average | live | same |
| `eval` | every **active hidden** test case once (in order) | frozen copy taken when the eval starts; uploaded | `eval_runs` + one `game_sessions` row per hidden case (mode `eval`) + the run log file |
| `verify` | nothing; static checks + a dry run on sample states | – | – |

`autopilot` with no arguments opens the interactive console; Ctrl+C in a run
returns to its menu, so the binary is never restarted. The same modes exist as
commands: `autopilot play --test-case TC03`, `play --all`, `eval [--team 0042]`.

Every run writes `autopilot-runs/<time>_<mode>_<case>/` on the participant's
machine: `session.json`, `events.jsonl` (each login, level, pause with snapshot,
agent answer and referee decision, LLM call, result), `console.log`, and each
`agent-<sha>.py` that played. An eval uploads `events.jsonl` (gzipped).

## Endpoints

All under the backend (hosted: `https://<site>/backend/...`). The game reaches
them through the SDK at `http://127.0.0.1:5000/...`.

### For the game (via the SDK)

| Call | Body | Reply |
|---|---|---|
| `POST /api/login` | `{"teamID": "0042", "password": "..."}` | 200 `{"token", "teamID", "teamName", "expiresIn", "llmToken"}`; 401 wrong ID/password; 403 banned or not approved yet (`detail` says which) |
| `POST /api/team` | `{"teamID": "0042"}` (as today) | the level, as today; 401/403/404/409 `{"error": "..."}` with a reason to show |
| `POST /api/reinforcements` | unchanged | unchanged |
| `POST /api/results` | unchanged | unchanged, plus `testCase` / `evalSlot` |

The login token is valid for 24 h. After a login the SDK sends it as
`Authorization: Bearer <token>` with the later calls, and the backend takes the
team from it (not from the `teamID` in the body).

### For participants (registration page and SDK)

| Call | Notes |
|---|---|
| `GET /register` | the registration page |
| `GET /api/registration/form` | title, description, open, questions |
| `POST /api/register` | `{"teamName", "password" (8+ chars), "answers": {questionId: value}}` → `{"teamID", "teamName", "approved"}` |
| `GET /api/test-cases` | public active test cases `[{"code": "TC01", "name", "enemies"}]` |
| `GET /api/eval/info` | `{"hiddenTestCases": 5}` |
| `POST /api/eval/start` | login; `{"agentSource", "sdkVersion"}` → `{"evalId", "slots"}`; an older running eval of the team becomes `abandoned` |
| `GET /api/eval/{id}` | login; progress per slot |
| `POST /api/eval/{id}/finish` | login; `{"log": base64(gzip(events.jsonl)), "summary"}` → `{"score", "status", "slots"}` |
| `GET /api/me` | login; the team's own stats |

### Organisers: `/admin` (dashboard) and `/admin/api/*` (header `X-Admin-Secret`)

| Call | Notes |
|---|---|
| `GET /admin/api/overview` | counts |
| `GET /admin/api/teams` | every team with status and stats (games, evals, best/last eval score) |
| `POST /admin/api/teams` · `PATCH /admin/api/teams/{id}` | create; edit name, answers, notes, password, `approved`, `banned` |
| `GET /admin/api/teams/{id}` | one team: answers, every game, every eval run |
| `POST /admin/api/teams/import` | `{teams: [{name, password?, answers}], approved}`: many teams at once (the dashboard's *Import from Unstop* reads an Excel/CSV export into this). Missing passwords are generated; names already taken are skipped. Returns `{created: [{teamID, name, password, answers}], skipped}`: the only place generated passwords appear |
| `GET /admin/api/evals` · `GET /admin/api/evals/{id}` | eval runs; one run with its `agentSource`, per-slot scores and games |
| `GET /admin/api/evals/{id}/log` | the uploaded run log (`.jsonl.gz`) |
| `GET /admin/api/sessions/{sessionID}` | one game: full result, evaluator output, approved placements |
| `GET/POST /admin/api/test-cases` · `PUT/DELETE /admin/api/test-cases/{code}` | public and hidden test cases; `{"code", "name", "hidden", "active", "position", "level": {"enemies", "spawnSettings", "gameSettings", "defencePlacementData"}}` |
| `GET/PUT /admin/api/registration-form` | `{"title", "description", "open", "autoApprove", "questions": [{"id", "label", "type": text/textarea/email/number/url/select/checkbox, "required", "options"}]}` |

## Data (Postgres)

| Table | Row |
|---|---|
| `teams` | + `password_hash` (PBKDF2), `approved`, `banned`, `profile` (`{"answers", "notes"}`) |
| `test_cases` | `code`, `name`, `hidden`, `active`, `position`, `level` (JSON) |
| `game_sessions` | one game started by the SDK: `session_id`, `team_id`, `test_case`, `mode`, `eval_id`, `eval_slot`, `agent_sha256`, `score`, `success`, `pauses` (snapshots the backend received), `placements` (referee-approved), `result`, `evaluator_output`, `started_at`, `finished_at` |
| `eval_runs` | `team_id`, `status` (`running`/`finished`/`incomplete`/`abandoned`), `case_codes`, `agent_source`, `agent_sha256`, `sdk_version`, `score`, `log_path`, `summary`, `started_at`, `finished_at` |
| `competition_settings` | `registration_form` |

The score of one game is `evaluator_service.evaluate` as before. An eval's score
is the mean over its hidden test cases; one not played counts 0. Every eval run is
kept, including abandoned ones, so the leaderboard can choose its rule (for
example "the latest finished eval counts", which stops a team from re-running
until it gets lucky).

Settings: `REQUIRE_LOGIN=true` makes `play` need a login too (eval always does).
`AUTOPILOT_GATEWAY_TEAM_SECRET` (the gateway's secret) puts each team's LLM token
in the login reply.

## Integrity

The agent runs on the participant's machine, so a determined participant can
tamper with what the game reports. What the backend keeps to check winners:
the exact `agent.py` of each eval (and its hash on every game), every pause it
received (`pauses`), every placement its referee approved, the full result, and
the participant's own run log. For the final ranking, re-run the top teams'
eval `agent_source` on the organisers' machine.
