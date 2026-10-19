# Event readiness: bugs found in live play (2026-10-15)

Found by playing the hosted stack end to end (Cloudflare URL, compiled Linux SDK,
build7 in Chromium on a real GPU), all ten test cases (teams 0004-0013), plus
probe games. Fixed items are on `test_branch`; open items need a decision or a
Unity change.

## Open: needs a decision

| Issue | Evidence | Options |
|---|---|---|
| **Cloudflare quick tunnel URL is not stable.** | It changes whenever `cloudflared` restarts (reboot, crash). | A named Cloudflare tunnel on a real domain for the event. |
| **Backend match state is in memory.** | Budget ledgers and the snapshot ledger live in the backend process. | Restarting the backend mid-match loses that match's spending/snapshot record; avoid restarts during play or persist them. |
| **3000 coins: hard but winnable.** | Strong heuristic at 3000: 5 of 10 test cases won (85.9-90.4), losses 23.8-49.2 (1000 coins: all lost). | `AUTOPILOT_STARTING_COINS` on the backend changes it without a rebuild. |

## Open: Unity (build7), needs Parv

| Issue | Evidence |
|---|---|
| Empty URL boxes send requests to the page instead of `127.0.0.1:5000` (regression from build6). | `docs/issues/build7-empty-url-boxes.md`. Worked around in `build7/index.html`; the Unity default should still be fixed. |
| End-of-match report lists only units that fought (dealt or took damage). | Report listed 1 of 4 defences in one game, 0 of 4 in another. The backend now completes it from the pause snapshots. |
| A pause always lasted the full `reinforcementHoldSeconds`, even when the reply came in 0.2 s; the game also waits past the hold for a late reply. | Worked around: holds are 0.2 s and the SDK gives agents their full 3.5 s, so a pause lasts as long as the agent takes (pauses ~6.5 s apart instead of ~10.4 s). |
| Game runs at ~45% of real time. | 60 fps in the browser, yet 4.2 game-seconds take ~9 real seconds, also with no pause in between; the taunt slow-motion (`initTimeScaleBeforeTaunt`) looks responsible. If intended, players should know a "3 s" pause interval is ~7 real seconds. |
| The reinforcement snapshot is logged as `result json are -`. | Reads like the final result when debugging. |
| Type names are misspelt / inconsistent: `defenceSoilder`, `EnemClown` next to `EnemyMonk`. | Agents that match on `type` must use these exact spellings. |
| `The referenced script on this Behaviour (Game Object 'Canvas') is missing!` | Logged by build7 at every level load. |
| Unity source in the repo is older than build6/build7. | `autopilot-game-engine/Assets` last changed Oct 11; builds can only be inspected compiled. Commit the build7 source. |

## Fixed on test_branch

- Unwinnable levels at 1000 coins: 3000 starting coins (see above).
- One shared LLM token for every team (60 calls/min in total): each team has its own token (`./quickstart.sh tokens`), limited per team.
- Fixed wait at every pause and LLM agents starved on short holds: 0.2 s holds; the SDK no longer cuts the agent's time to the hold.
- Test cases were teams whose ID had to be typed in the game: the SDK now picks the test case (`play --test-case`, `play --all`, hidden ones in `eval`); the game page no longer types IDs by itself. See docs/COMPETITION.md.

- Game report missing units: backend completes it from every pause snapshot and the approved placements; scoring and the SDK's GAME OVER use the full counts.
- Agents had no cost information: `state["budget"]` and `state["unitCosts"]`; refused placements print a reason.
- Fast defeats scored ~42: survival and economy scale with the share of the wave killed.
- build7 coin counter showed nothing: backend and SDK send `coins`.
- Starting coins 1000; every kill pays 50 up to the end of the wave (the old cap stopped kills paying out).
- Example agents and `test-agent/agent.py` overspent; now only propose what they can pay for.
- Example agent prompt had wrong unit health (soldier 200, archer 200, shield 600); the game has soldier 150, archer 100, shield 700, penguin 200.
- `test-agent/agent.py` passed a 0 s timeout on short holds (`ValueError: Attempted to set connect timeout to 0.0`); it now skips the LLM when under 0.8 s, and `BharatCodeClient.chat` raises a clear `requests.Timeout("no time left ...")` instead.
