# build7: empty URL boxes send requests to the page, not the SDK

**Status:** open. Needs a Unity change (the Unity source in `autopilot-game-engine/Assets`
is older than build6, so it cannot be fixed in this repo).

## What happens

On the start screen, participants enter the team ID and leave the three URL boxes
(team data, reinforcements, results push) empty, so the game uses its built-in
SDK address `http://127.0.0.1:5000/api/...`. That is what build6 does and what
`docs/PARTICIPANTS.md` and `docs/QUICKSTART.md` tell players to do.

build7 instead sends the request for every **empty** box to an empty URL, i.e. a
`POST` to the page itself (`http://localhost:8000/`, or the hosted page's root):

| Boxes filled | Requests seen (Chromium, build7) | Result |
|---|---|---|
| none | `POST http://localhost:8000/` | game page answers 405; the level never loads |
| team data only | `POST http://127.0.0.1:5000/api/team`, then every pause `POST http://localhost:8000/` | level loads; every reinforcement pause fails |
| all three | `.../api/team`, `.../api/reinforcements`, `.../api/results` on :5000 | works |

build6 with the same browser and empty boxes calls `127.0.0.1:5000` for all three.

## Fix (Unity)

Where the start screen reads the three boxes, treat an empty (or whitespace-only)
box as "use the default": `http://127.0.0.1:5000/api/team`,
`/api/reinforcements`, `/api/results`. Then rebuild build7 and replace the
`game-build7` release asset.

## Workaround until then

Type the three URLs into the boxes:

    http://127.0.0.1:5000/api/team
    http://127.0.0.1:5000/api/reinforcements
    http://127.0.0.1:5000/api/results

## Also in build7 (working)

- The coin counter (`Coins: N`) reads `coins` from the `/api/team` reply and from
  every reinforcement reply. The backend and the SDK now send it: the budget left,
  in whole units.
- The game logs each reinforcement snapshot as `result json are -`, which reads like
  the final result. Renaming the log line would avoid confusion when debugging.
