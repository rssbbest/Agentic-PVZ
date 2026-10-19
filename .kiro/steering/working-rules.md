# Working rules for this codebase

Follow these every time, in every session.

## Permissions

- Do not run git commands that commit or push. Never commit or push.
- Do not edit, create or delete any file unless the user asks for it in that request.
- Do not open anything under `unity-files/` (the Unity project: `Assets/`,
  `ProjectSettings/`, `UserSettings/`) without asking the user first and getting
  a yes. The developer docs below should be enough for the Unity side.

## Documentation to use

| Folder | Content | Use |
|---|---|---|
| `docs/new_docs_for_kiro/` | `BACKEND_GUIDE.md`, `SDK_GUIDE.md` (written by Kiro from the code) | Read the relevant guide before working on the backend or the SDK, to find the right files |
| `docs/docs_by_developers/` | `backend API guide.md` (what the Unity client sends and expects on every endpoint), `playerGuide.md` (grid, unit ids, costs, stats) | The source of truth for the Unity build's behaviour |

## Keeping the guides current

Whenever a change to the codebase is made (by request):

1. Update the matching sections of `docs/new_docs_for_kiro/BACKEND_GUIDE.md`
   and/or `docs/new_docs_for_kiro/SDK_GUIDE.md` in the same task.
2. Add a row to that guide's Changelog table (date, what changed, sections touched).
3. If the change crosses the SDK/backend boundary (a field the SDK sends or
   reads, a route, a timeout), update both guides.

## Facts to keep in mind

- Live flow: Unity game (browser) → SDK on `127.0.0.1:5000` → backend over HTTPS.
  The game never calls the backend directly; the backend never calls the game.
- The SDK source exists twice: `autopilot-sdk/` (dev and tests) and
  `autopilot-sdk-binary/` (the shipped binaries are built from it). An SDK
  source change must be made in both.
