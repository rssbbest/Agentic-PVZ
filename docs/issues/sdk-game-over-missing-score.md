# SDK: GAME OVER summary never shows the backend's score

**Status:** fixed in source (binaries not rebuilt yet) · **Area:** `autopilot-sdk` (`local_proxy.py`), needs a binary rebuild · **Found:** 2026-10-15 on the hosted test server, macOS binary, teams 0003 and 0004

## What happens

At the end of a game the `autopilot play` terminal prints the GAME OVER block
(result, duration, kills, damage) but no score:

```
============= GAME OVER - team 0004 (session d5a3af80f178) =============
Result:    LOST
Duration:  30.0 s
Enemies:   2/4 killed   (damage dealt by enemies: 1107.4)
Defences:  3/3 destroyed   (damage dealt by defences: 850)
...
========================================================================
```

## Why

- The backend scores the game in `POST /api/results` and returns it:
  `autopilot-backend/app/routers/game_api.py` sets `response["score"]` and
  `response["evaluator_output"]` (around line 539).
- The SDK's `/api/results` route (`autopilot-sdk/src/autopilot_sdk/local_proxy.py`,
  around line 253) calls `self._print_results(body)` with the *game's* payload,
  then `self._forward("POST", body)`. The backend's reply is passed to the game
  but never read, so `format_results` has no score to print.

## Expected

After forwarding, the SDK prints the backend's `score` (and, briefly, the
evaluator's cost/budget stats from `evaluator_output.stats`, e.g.
`total_defence_cost`, `test_case_budget`, `enemies_killed/total_enemies`)
under the GAME OVER block. If the backend returns no score, say so
(e.g. "Score: not scored (no run for this session)") rather than printing nothing.

## Also check

- Whether the backend returns a `score` at all for live sessions that have no
  `run` (the hosted teams 0003-0013). If `score` is `None` there, participants
  never get a score; decide whether live play should be scored.
- Whether the game UI shows the score from the `/api/results` reply.

## Done when

- The GAME OVER block in `autopilot play` shows the score from the backend.
- A test in `autopilot-sdk/tests/test_local_proxy.py` covers it.
- The SDK is synced into `autopilot-sdk-binary` and the binaries are rebuilt
  (Linux/Windows via `tools/release.sh`; macOS must be rebuilt on a Mac).

## Fix (2026-10-15)

- `local_proxy.py`: `/api/results` is forwarded first; the backend's reply is
  then printed under GAME OVER: `Score`, `Cost: <spent> of <budget> budget
  (under/over budget)` and `Scored: <killed>/<total> enemies killed,
  <survived>/<placed> defences survived`. Unscored games print
  `Score: not scored (<reason>)`, and an unreachable backend prints
  `Score: unknown (...)`, both in red.
- Backend `POST /api/results` now always returns `evaluator_output`, so the
  reason (`{"valid": false, "error": ...}`) reaches the SDK when a game is not
  scored. Live sessions (teams 0003-0013, no `run`) are scored; verified on
  the hosted server: `Score: 21.05`, `Cost: 700 of 1120 budget`.
- Tests: `test_game_over_shows_the_backends_score_and_cost`,
  `test_game_over_says_why_a_game_was_not_scored`,
  `test_game_over_without_a_backend_reply` in `autopilot-sdk/tests/test_local_proxy.py`.
- Synced into `autopilot-sdk-binary/src`. **Binaries still to rebuild**:
  `tools/release.sh --push` for Linux/Windows, `python build_binary.py` on a Mac.
- Not checked: whether the game UI shows the score (needs the Unity side).
