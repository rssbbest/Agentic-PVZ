# AutoPilot Backend API Guide

This is a complete reference for building the backend that the Unity client (AutoPilot) talks to. It covers every endpoint the simulator calls, what each one must receive and return, and how optional/newer fields behave if you leave them out.

All requests are plain HTTP, unauthenticated, same-origin assumption only for CORS (the client runs as a WebGL build, so **CORS must be enabled** on every endpoint for the game's origin, including preflight `OPTIONS` for `POST`). Default base URL used by the client is `http://127.0.0.1:5000`, but every URL is a field in the Unity inspector, so paths below are the defaults, not hardcoded.

---

## Endpoint overview

| # | Endpoint | Method | Called when | Request body | Response body |
|---|----------|--------|-------------|---------------|----------------|
| 1 | `/api/team` | POST | Player submits Team ID on the menu screen | `{"teamID": string}` | Level data: coins, defences, enemies, optional settings |
| 2 | `/api/results` | POST | Simulation ends (win or loss) | Full battle report JSON | Anything (only logged) |
| 3 | `/api/reinforcements` | POST | Once at run start, then every N seconds (if enabled) | Live board snapshot JSON | Place-or-skip decision + coin balance |
| 4 | `/api/screenshot` | POST | On-demand, mid-game (optional feature) | `multipart/form-data`: JPG + teamID + sessionID | Anything (only logged) |
| 5 | `/api/state` | POST | On-demand, mid-game (optional feature) | Same schema as snapshot sent to `/api/reinforcements` | Anything (only logged) |

Endpoints 1 and 2 are the original, required core loop. Endpoint 3 should also be implemented: the client always sends one `/api/reinforcements` request when the run starts, even if `gameSettings.reinforcementsEnabled` is false (the interval pings after that do follow the setting). If that request fails, the game shows "No response from agent, resuming" and carries on. Endpoints 4 and 5 are optional — screenshot/state capture are never triggered unless some other script explicitly calls them.

---

## 1. `POST /api/team` — fetch level data

**Method:** `POST`
**Content-Type:** `application/json`

### Request (sent by simulator)

```json
{ "teamID": "TEAM123" }
```

### Response (sent by backend) — success, any 2xx status

```json
{
  "sessionID": "abc123",
  "coins": 500,
  "defencePlacementData": [
    { "defenceId": 0, "defenceGridPosition": { "x": 0, "y": 0 } }
  ],
  "enemies": [
    { "enemyId": 0, "count": 5 },
    { "enemyId": 1, "count": 6, "allowedRows": [0, 1, 2] }
  ],
  "spawnSettings": {
    "interArrivalTime": 6,
    "timeDecreaseRate": 0.25,
    "minInterArrivalTime": 0,
    "spawnSequence": [0, 0, 0, 1, 0, 0, 1, 1, 1, 1, 1]
  },
  "gameSettings": {
    "reinforcementsEnabled": true,
    "reinforcementIntervalSeconds": 10,
    "reinforcementHoldSeconds": 1,
    "sendScreenshotToo": false
  }
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `sessionID` | string | optional | Blank/missing → client falls back to `"Not Implemented Yet"` in reports |
| `coins` | int | optional | Starting coin balance, shown on screen as `Coins: 500` (counts up from 0 when the run starts). Missing → 0 |
| `defencePlacementData[].defenceId` | int | yes (if array present) | Index into the client's defence prefab list (confirm current count/order with the Unity project — originally 0–3, more types may have been added since) |
| `defencePlacementData[].defenceGridPosition.x` | int | yes | Grid column, `0`–`10` (11 columns) |
| `defencePlacementData[].defenceGridPosition.y` | int | yes | Grid row, `0`–`5` (6 rows) |
| `enemies[].enemyId` | int | yes | Index into the client's enemy prefab list |
| `enemies[].count` | int | yes | How many of this enemy to spawn in the wave |
| `enemies[].allowedRows` | int[] | optional | Rows this enemy type may spawn in. Missing/empty → any row. Out-of-range values are dropped with a warning; if none remain valid, falls back to any row |
| `spawnSettings` | object | optional | Any field left out keeps the client's Inspector default. Entire object missing/null → all Inspector defaults |
| `spawnSettings.interArrivalTime` | float (seconds) | optional | Wait between enemy spawns, before decay |
| `spawnSettings.timeDecreaseRate` | float (seconds) | optional | Subtracted from the wait after each spawn |
| `spawnSettings.minInterArrivalTime` | float (seconds) | optional | Floor on the wait, so spawns don't get arbitrarily fast |
| `spawnSettings.spawnSequence` | int[] | optional | `enemyId`s in the order they should spawn. See [Spawn sequence](#spawn-sequence-spawnsettingsspawnsequence) below. Missing/empty → random order |
| `gameSettings` | object | optional | Same overlay behavior as `spawnSettings` — missing fields, missing object, or explicit `null` all keep Inspector defaults |
| `gameSettings.reinforcementsEnabled` | bool | optional | Turns the interval reinforcement pings on/off for this team's run (the single ping at run start is always sent) |
| `gameSettings.reinforcementIntervalSeconds` | float | optional | Seconds (in-game time) between reinforcement pings |
| `gameSettings.reinforcementHoldSeconds` | float | optional | Real seconds the game stays paused on the decision text (place, skip, or no response) before resuming |
| `gameSettings.sendScreenshotToo` | bool | optional | If true, each reinforcement ping also uploads a clean screenshot to `/api/screenshot` |

### Spawn sequence (`spawnSettings.spawnSequence`)

- Each entry is an `enemyId` from `enemies[]`. Spawn N takes entry N and spawns one queued enemy of that type, in one of that type's `allowedRows`.
- Use each id at most as many times as that type's `count`, and keep the length at or below the total number of enemies (the sum of all `count`s).
- Shorter than the total: the sequence is followed for the first N spawns, then the remaining enemies spawn in random order.
- Missing, `null` or `[]`: fully random order. Unlike the other `spawnSettings` fields, there is no Inspector default.
- The client does not validate entries. An id with no enemy of that type left in the queue spawns a random enemy for that slot, and the sequence carries on from the next entry.

Example: with `enemyId 0 × 5` and `enemyId 1 × 6` (11 enemies), `[0,0,0,1,0,0,1,1,1,1,1]` spawns three 0s, one 1, two 0s, then five 1s. `[1,1,0]` spawns two 1s and one 0, then the other 8 in random order.

### Response — error (any non-2xx)

```json
{ "error": "Team 'TEAM123' not found" }
```

The `error` string is shown to the user on screen verbatim, so keep it human-readable.

**Notes for implementers:**
- `defenceId` / `enemyId` are positional indices into Inspector-ordered prefab lists on the client. These are not stable, named identifiers — confirm the current mapping with whoever maintains the Unity project before hardcoding values.
- Settings objects (`spawnSettings`, `gameSettings`) use a **partial-overlay** pattern: send only the fields you want to override, everything else falls back to the client's own defaults. This means an old backend that doesn't know about `gameSettings` yet is fully backward compatible — just omit the key entirely.

---

## 2. `POST /api/results` — receive the end-of-run report

**Method:** `POST`
**Content-Type:** `application/json`

### Request (sent by simulator) — after the run ends, win or loss

```json
{
  "teamID": "TEAM123",
  "sessionID": "abc123",
  "success": false,
  "durationSeconds": 81.4,
  "enemiesKilled": 3,
  "defencesDestroyed": 2,
  "totalDefenceDamage": 4200.0,
  "totalEnemyDamage": 1650.0,
  "defences": [
    {
      "id": "defenceSoilder_0",
      "type": "defenceSoilder",
      "position": { "x": 0, "y": 1 },
      "damageDealt": 900.0,
      "destroyed": true,
      "destroyedAtTime": 42.1
    }
  ],
  "enemies": [
    {
      "id": "EnemClown_0",
      "type": "EnemClown",
      "damageDealt": 300.0,
      "killed": true,
      "killedAtTime": 37.9
    }
  ],
  "defenceTypeStats": [
    { "type": "defenceSoilder", "unitCount": 1, "totalDamageDealt": 900.0, "unitsDestroyed": 1 }
  ],
  "enemyTypeStats": [
    { "type": "EnemClown", "unitCount": 1, "totalDamageDealt": 300.0, "unitsKilled": 1 }
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `teamID` | string | As submitted at the start |
| `sessionID` | string | From `/api/team`, or `"Not Implemented Yet"` if none was sent |
| `success` | bool | `true` when every enemy in the wave was killed, `false` when an enemy reached the base |
| `durationSeconds` | float | Measured from the start of this run only |
| `defences[]` / `enemies[]` | array | One entry per individual unit (not per type) |
| `id` | string | Unique per unit, e.g. `defenceSoilder_0`, `defenceSoilder_1` |
| `type` | string | The prefab type, derived from `id` |
| `defenceTypeStats[]` / `enemyTypeStats[]` | array | One row per unit type, with aggregated totals |

### Response (sent by backend)

Any 2xx status. Body is not parsed, only logged — return whatever you like (e.g. `{"ok": true}` or empty).

**Behavior to support:** the client retries up to 3 times, 2 seconds apart, only on connection failure (not on 4xx/5xx — those are treated as final). Make sure your endpoint responds within ~10 seconds or the client will time out and move on without the result being recorded.

---

## 3. `POST /api/reinforcements` — mid-run reinforcement decision

**Method:** `POST`
**Content-Type:** `application/json`

Called once when the run starts (as soon as the enemy wave is queued), then every `gameSettings.reinforcementIntervalSeconds` of in-game time if `gameSettings.reinforcementsEnabled` is true. The first call happens even when reinforcements are disabled. The game freezes, sends the current board state, waits for your decision, shows it on screen, then resumes.

### Request (sent by simulator) — current board snapshot

```json
{
  "teamID": "TEAM1",
  "sessionID": "abc123",
  "elapsedSeconds": 31.8,
  "counts": {
    "activeDefenceCount": 4,
    "activeEnemyCount": 3,
    "enemiesQueued": 5,
    "totalEnemiesInWave": 12
  },
  "defences": [
    {
      "id": "defenceSoilder_0",
      "type": "defenceSoilder",
      "health": 85.5,
      "maxHealth": 150.0,
      "position": { "x": 1, "y": 2 }
    }
  ],
  "enemies": [
    {
      "id": "EnemClown_3",
      "type": "EnemClown",
      "health": 40.0,
      "maxHealth": 100.0,
      "position": { "x": 2, "y": 2 },
      "isAttacking": true,
      "isDead": false
    }
  ]
}
```

This is the exact same schema used by `/api/state` (see endpoint 5). It does not include the coin balance.

### Response (sent by backend) — place defences

```json
{
  "placeDefences": true,
  "coins": 300,
  "defencePlacementData": [
    { "defenceId": 0, "defenceGridPosition": { "x": 3, "y": 2 } }
  ]
}
```

### Response (sent by backend) — skip this round

```json
{ "placeDefences": false, "coins": 500 }
```

An empty 2xx body is also treated as "skip" (and leaves the coin balance unchanged).

| Field | Type | Required | Notes |
|---|---|---|---|
| `placeDefences` | bool | yes | `true` = place the cards below, `false`/missing = skip |
| `coins` | int | send on every response | The team's balance **after** this decision (a total, not a change). Missing → balance unchanged |
| `defencePlacementData[]` | array | only if placing | Same `Placement_Card` shape as `/api/team`: `{"defenceId": int, "defenceGridPosition": {"x": int, "y": int}}` |

**Rules the client expects you to enforce (not validated client-side):**
- Every `defenceId` must be a valid prefab index.
- Every `defenceGridPosition` must be inside the grid (`x: 0–10`, `y: 0–5`) and on a cell that isn't already occupied.
- There is no per-card validation on the client, so a bad id or occupied cell is undefined/your responsibility.

**Coins:**
- The client only displays `coins`. It doesn't check costs, deduct anything, or block placements, so the backend decides what the agent can afford.
- The snapshot doesn't carry the balance back, so track it server-side (for example per `sessionID`), starting from the `coins` sent by `/api/team`.
- The new balance is shown as `Coins: <n>` once the game has resumed, counting from the old value (green flash when it goes up, red when it goes down).
- If the request fails (timeout, connection error, or any non-2xx), the balance stays at its last value.

**What the player sees:** "Waiting for agent response..." while the request is in flight, then one of:
- "Agent chose to place defences" (`placeDefences: true`)
- "Agent chose to skip" (`placeDefences` false or missing, or an empty/unreadable body)
- "No response from agent, resuming" (the request failed)

The decision text stays on screen for `gameSettings.reinforcementHoldSeconds` before the game resumes.

**Timing:** the game is fully frozen (`timeScale = 0`) while waiting for your response. The client retries on connection failure (same retry policy as `/api/results`), with up to ~34 seconds total worst case before it gives up and resumes with no placement. Keep this endpoint fast — every second here is a second the simulation is visibly frozen.

**CORS:** this endpoint must also answer `OPTIONS` preflight requests for the game's page origin, same as the others.

---

## 4. `POST /api/screenshot` — mid-game screenshot upload (optional)

**Method:** `POST`
**Content-Type:** `multipart/form-data` (boundary generated by the client)

Only fires if something in the game explicitly triggers a capture (e.g. `sendScreenshotToo` during a reinforcement ping, or a future debug/manual trigger). Not part of the core required loop.

### Request (sent by simulator) — multipart form fields

| Part name | Kind | Content-Type | Value |
|---|---|---|---|
| `screenshot` | file | `image/jpeg` | JPG bytes of the current screen (board, UI, effects) |
| `teamID` | text | `text/plain` | Team ID string |
| `sessionID` | text | `text/plain` | Session ID string (never blank — falls back to a placeholder) |

Suggested filename sent along with the file part: `screenshot_<teamID>_<yyyyMMdd_HHmmss>.jpg` — informational only, feel free to generate your own storage name server-side.

### Response (sent by backend)

- Success: any 2xx, body not parsed.
- Failure: respond with `{"error": "..."}` so the client can log something readable (it won't be shown to the player — this upload is best-effort and silent to the user).

---

## 5. `POST /api/state` — mid-game structured snapshot (optional)

**Method:** `POST`
**Content-Type:** `application/json`

Same trigger conditions as `/api/screenshot` (not part of the core loop — only fires if something calls it). Same JSON body shape as the `/api/reinforcements` request (section 3), but this endpoint has no decision to make — it's a one-way push.

### Request (sent by simulator)

Identical schema to the `/api/reinforcements` request body shown above.

### Response (sent by backend)

Any 2xx, body not parsed, only logged.

---

## Summary: what the simulator sends vs. expects back

| Endpoint | Simulator sends | Simulator expects back |
|---|---|---|
| `/api/team` | Team ID | Starting coins + defence/enemy layout + optional spawn/game settings (incl. spawn sequence) |
| `/api/results` | Full battle report | Just a 2xx (fire-and-forget) |
| `/api/reinforcements` | Live board snapshot | Place-or-skip decision + coin balance + optional cards |
| `/api/screenshot` | JPG + team/session IDs | Just a 2xx (fire-and-forget) |
| `/api/state` | Live board snapshot (same shape as #3) | Just a 2xx (fire-and-forget) |

## Build order recommendation

If building this from scratch, implement in this order:
1. `/api/team` and `/api/results` — required for the game to run at all.
2. `/api/reinforcements` — one request always arrives at run start, so at minimum answer `{"placeDefences": false, "coins": <balance>}`. Real place/skip logic is only needed if `gameSettings.reinforcementsEnabled` is true.
3. `/api/screenshot` and `/api/state` — only needed if you're building something that consumes mid-game visuals/state (e.g. an LLM-driven evaluator watching the match live).
