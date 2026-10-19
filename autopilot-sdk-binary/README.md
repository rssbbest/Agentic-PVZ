# Autopilot SDK standalone runner

This folder contains the SDK runner, participant agent examples, backend
integration, and standalone binary build workflow. The current Unity feature
uses a module-level `get_reinforcements(state)` hook. The older submission
pipeline remains available through `Agent.place_defences(enemies)` and
`POST /run`.

## Standalone executable

The Linux x86_64 binary built from this source is included at
`bin/autopilot-linux-x86_64`. It bundles Python and SDK dependencies. Native
builds for Linux x86_64, Windows x86_64, macOS x86_64, and macOS arm64 run in
[GitHub Actions](.github/workflows/build-binaries.yml); tagging a release
`v*` attaches those platform builds to the release.

```sh
./bin/autopilot-linux-x86_64 version
./bin/autopilot-linux-x86_64 serve --host 0.0.0.0 --port 8001
```

To build locally for the current OS and CPU architecture, install Python 3.10+
and run:

```sh
python -m pip install ".[build]"
python build_binary.py
```

Output is written to `dist/`. PyInstaller must build on the target OS; a Linux
executable does not run on Windows or macOS. See [testing](testing.md) for
binary checks and manual no-Unity integration steps.

## Live reinforcement agents

Write a module-level function in the participant's `agent.py`:

```python
def get_reinforcements(state: dict) -> list[dict]:
    # Return candidate placements, or [] to skip this reinforcement pause.
    return [{"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 3}}]
```

The current board is 11 columns by 6 rows: x is 0–10, y is 0–5. Defence IDs
are 0–3. Check occupied living-defence cells before proposing placements. The
backend referee drops invalid candidates and never nudges them. The SDK limits
the hook to 3.5 seconds by default; backend HTTP timeout is 4.5 seconds.

The request snapshot contains `teamID`, `sessionID`, `elapsedSeconds`,
`counts`, `defences`, `enemies`, and `candidatePlacements`. Full fields,
example strategy, and policy are in [agent development](docs/agent-development.md).

## Run with the backend

The backend calls `POST /reinforcements` on this service. That request contains
no source code, so the SDK loads the team's latest uploaded source from a
shared directory. By default the backend saves submissions as
`team{teamID}_{uuid}.py`; matching is case-insensitive and normalizes a leading
`team` in the requested ID. Custom team names can use `{teamID}.py` or its
normalized underscore form. Set `AUTOPILOT_AGENT_DIR` to the directory
containing those files. The unified Docker Compose setup mounts backend
`uploads/` into the SDK at `/agents` read-only and sets the backend SDK URL to
`http://sdk:8001`.

```sh
autopilot serve --host 0.0.0.0 --port 8001 --agent-dir ./uploads
```

See [backend integration](docs/backend-integration.md) for request and
response formats, Docker deployment, timeout behavior, and agent file lookup.
Unity calls the backend's `/api/team`, `/api/results`, and
`/api/reinforcements`; Unity does not call this SDK service directly.

## Legacy submission CLI and runner

The previous evaluator integration remains usable:

```sh
export AUTOPILOT_BACKEND_URL=http://localhost:8000
autopilot verify --agent agent.py
autopilot register --name "Team name"
autopilot submit --agent agent.py --team-id 1
autopilot status 3 --runs
autopilot leaderboard
```

For `POST /run`, the backend sends base64 agent source and an enemy list. That
legacy path runs `Agent.place_defences(enemies)` in a guarded subprocess and
returns `sdkStatus` with placement data. Live Unity reinforcements instead use
`get_reinforcements(state)` as described above.

## Development

```sh
python -m pip install -e '.[dev]'
pytest -q
```

See [`testing.md`](testing.md) for unit, HTTP, binary, and backend API checks
that do not require a live Unity game.
