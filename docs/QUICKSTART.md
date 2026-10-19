# Quick start, live demo and tests

`./quickstart.sh` runs the whole live-reinforcement stack on one Linux machine:

```text
Browser game (:8000) ─▶ :5000 tap ─▶ SDK `autopilot play` (:5100) ─▶ :8011 tap ─▶ Backend (:8010) ─▶ Postgres (:5433, Docker)
                                         └ runs agent.py ─▶ :8005 tap ─▶ LLM gateway (:8004) ─▶ Bedrock / Gemini / any OpenAI-compatible API
Live monitor (:8020) reads every tap and every service log.
```

| Piece | What starts it |
|---|---|
| Postgres | Docker container `autopilot-quickstart-db` (volume `autopilot-quickstart-pgdata`) |
| Backend | `autopilot-backend` with uvicorn; loads the test cases (public TC01–TC10 and the hidden ones) at start |
| SDK | `autopilot-sdk-binary/bin/autopilot-linux-x86_64 play` (`SDK=source` runs it from source) |
| Agent | `autopilot-sdk/examples/llm_tool_agent.py` with an LLM key, else `reinforcement_agent.py` (`AGENT=path` overrides) |
| LLM gateway | `integration/gateway.py`, only when `AUTOPILOT_LLM_API_KEY` is set |
| Game | the prebuilt Unity WebGL `build7`, downloaded once from the `game-build7` release |
| Monitor | `tools/live-monitor/monitor.py` |

Logs: `.runtime/quickstart/logs/`. All exchanges are also appended to
`.runtime/quickstart/exchanges.jsonl`.

## Setup

```bash
git clone -b test_branch https://github.com/nextgen-user/autopilot-unified.git
cd autopilot-unified
cp .env.example .env    # put the Bedrock API key in AUTOPILOT_LLM_API_KEY (optional)
./quickstart.sh
```

Requirements: Linux x86_64, Docker, curl, unzip, Python 3.10+ (or `uv`), and
`gh auth login` or `GITHUB_TOKEN` for the private game download
(`GAME_ZIP=/path/build7.zip` skips the download). Without an LLM key everything
still runs with the heuristic example agent.

## Showing it

1. Open http://localhost:8020 (monitor) and http://localhost:8000 (game) side by side.
2. In the game type a 4-digit ID from the table quickstart printed, e.g. `0008`
   for TC05, fill the three URL boxes with `http://127.0.0.1:5000/api/team`, `http://127.0.0.1:5000/api/reinforcements` and `http://127.0.0.1:5000/api/results`
   (build7 does not fall back to them when a box is empty: [issue](issues/build7-empty-url-boxes.md)) and press play. The box takes digits
   only; the backend reads them as the team's numeric ID.
3. When the level loads, the agent is called once on the empty board
   (`state["phase"] == "start"`, the coming wave in `state["upcomingEnemies"]`)
   and its approved placements are the opening defences.
   Every ~10 s of game time the game pauses. In the monitor that pause appears
   as one card: the board the game sent, the LLM's tool calls and one-line
   reason, the agent's proposals (dashed) and what the backend referee
   approved (green). The game then places the approved defences.
4. At the end the game posts its result; the card shows WON/LOST and the
   backend's score. The right side lists every HTTP exchange (click one for
   the full request and response) and the live logs of all services.
5. Edit the agent file while the game runs; the next pause uses the new code.

## Playing as a participant

`./quickstart.sh organiser` starts only what organisers host: the database,
backend, levels, LLM gateway and game page. Nothing listens on :5000, so run
the SDK yourself, as a participant does:

```bash
./quickstart.sh organiser
autopilot-sdk-binary/bin/autopilot-linux-x86_64 verify --agent my_agent.py
autopilot-sdk-binary/bin/autopilot-linux-x86_64 play --agent my_agent.py
```

Open http://localhost:8000, type a 4-digit ID and press play. The SDK
terminal shows each pause and prints the result at the end. The live monitor
is not started in this mode. Stop with Ctrl+C, then `./quickstart.sh down`.

## LLM tool calling in agent.py

Participants write normal Python functions and let the model call them:

```python
from autopilot_sdk.llm import LLMClient
from autopilot_sdk.tools import run_tool_loop, tool

def get_reinforcements(state):
    @tool
    def free_cells(row: int) -> list[int]:
        """Columns in a row with no living defence.

        Args:
            row: Board row, 0-5.
        """
        ...

    run = run_tool_loop(LLMClient(),
                        messages, [free_cells],
                        deadline_s=state["timeBudgetSeconds"] - 0.6, max_rounds=2)
    return parse(run.content)
```

- `@tool` builds the JSON schema from type hints and the docstring.
- Tools run inside the agent's sandboxed process; the network guard still applies.
- The last round forces an answer (`tool_choice: "none"`), so a loop always ends.
- `state["timeBudgetSeconds"]` is the agent's real limit: 3.5 s, cut to the
  level's `reinforcementHoldSeconds` minus 0.6 s when the hold is shorter.
- Keys never reach agents: the gateway holds them, rotates several keys and
  passes tool calls through unchanged.

Measured from India: Bedrock `qwen.qwen3-next-80b-a3b-instruct` in `ap-south-1`
answers one tool round plus the final answer in 1.4-1.7 s. The same model in
`us-east-1` took 2.1-2.5 s per call, too slow for the pause. Levels with a 2 s
hold leave 1.4 s, so the example agent uses its heuristic there.

## Hosting for participants (Cloudflare tunnel or Tailscale Funnel)

The game server on :8000 is the single public entry point: it serves the game
and `/download/`, and forwards `/backend/api/*`, `/backend/health` and
`/llm/v1/*` (admin and submission routes are not forwarded). Publish it with

```bash
./quickstart.sh tunnel         # Cloudflare quick tunnel, no account: https://<random>.trycloudflare.com
./quickstart.sh tunnel off
```

The tunnel keeps running across `down`/`up` and `tools/release.sh`, so its URL
stays the same until it is stopped. Measured: the 30 MB compressed game
downloads in about 3 s through Cloudflare against about 75 s through Funnel.

### Tailscale Funnel

```bash
./quickstart.sh organiser      # or `up` to also play here
./quickstart.sh funnel         # publish; prints the lines to send participants
./quickstart.sh funnel off     # stop publishing
```

`funnel` publishes three routes on `https://<machine>.<tailnet>.ts.net:8443`
(`FUNNEL_PORT` to change): the game at `/`, the backend's game API at
`/backend/api/*` (admin and submission routes are not published) and the LLM
gateway at `/llm/v1`. The gateway accepts each team's own token
(`./quickstart.sh tokens` prints them; they are signed with `GATEWAY_TEAM_SECRET`,
generated once into `.runtime/quickstart/secrets.env`) and the organiser
tokens in `AUTOPILOT_GATEWAY_TOKENS`, allows 60 calls per minute per token
(`AUTOPILOT_GATEWAY_RATE_PER_MIN`), always uses the organiser's model
(`AUTOPILOT_LLM_ALLOWED_MODELS` to allow others) and caps output at 1024
tokens. Admin and simulator secrets are generated too, never the old defaults.

The game is served gzip-compressed (about 41 MB instead of 208 MB). Measured
through Funnel from the same city: the page loads in about 2 minutes the first
time (~400 KB/s), a new HTTPS connection costs ~1.1 s and a reused one ~0.35 s.
The SDK keeps its backend connection open and relays the agent's LLM calls
over it, so a two-round LLM tool agent answers a pause in 2.8-3.2 s, inside
the 5 s hold. The machine must stay on and awake while participants play.

## Releasing a change

`tools/release.sh` syncs `autopilot-sdk` into `autopilot-sdk-binary`, runs the
SDK and gateway tests, builds the Linux binary, builds the Windows binary in
the Windows VM (tests included), restarts the stack, republishes it through
Funnel and smoke-tests the new binary as a participant against the public URL.
`--no-windows`, `--no-deploy`, `--organiser`, `--commit` and `--push` adjust it.
macOS cannot be cross-built: build on a Mac with `python build_binary.py`.

## Tests

| Command | What it checks |
|---|---|
| `./quickstart.sh smoke` | game-shaped `/api/team` and `/api/reinforcements` through SDK, agent and referee |
| `./quickstart.sh status` | every service answers |
| `.venv/bin/python -m pytest -q autopilot-sdk/tests integration/tests` | SDK (tool loop, BOM, Windows pipe, hold budget, proxy) and gateway |
| `PYTHONPATH=autopilot-backend .venv/bin/python -m pytest -q autopilot-backend/tests` | backend (2 known failures in `test_backend_sdk_sync.py`: endpoints not implemented) |
| `./quickstart.sh testcases` | reload TC01-TC10 after editing the JSON |

## Windows participants

`autopilot-sdk-binary/bin/autopilot-windows-x86_64.exe play --agent agent.py --backend http://<organiser-backend>`
runs the agent locally and listens on 127.0.0.1:5000 for the game, exactly as on Linux.
