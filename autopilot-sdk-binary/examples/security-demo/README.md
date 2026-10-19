# Security demo: what agent code can and cannot do

Each file is a separate participant `agent.py` for the live game: a
module-level `get_reinforcements(state)`. `showcase.py` runs every file
through the same path the game uses and checks the outcome.

```bash
.venv/bin/python autopilot-sdk/examples/security-demo/showcase.py
```

For each file the showcase:

1. runs `autopilot play`'s startup check (static scan and contract),
2. starts a real SDK proxy for it and sends one reinforcement pause, as the
   game does,
3. reads what the sandboxed worker and the referee did with it.

It needs no backend, database or API key. A stand-in referee with the
backend's rules and a fake LLM gateway run on loopback. Pass
`--backend http://127.0.0.1:8010` to use the real backend referee, and
`--agent <file>` to run a single example. The last run's outcomes are in
`results.json`.

## The examples

| Layer | File | What it tries | Outcome |
|---|---|---|---|
| control | `good_agent.py` | Legal soldiers in the enemies' rows | approved 2/2 |
| control | `gateway_llm.py` | LLM call through the organiser's gateway | approved 1/1 |
| static | `forbidden_import.py` | `import anthropic` | rejected at startup |
| static | `direct_provider.py` | Provider URL in the source | rejected at startup |
| static | `missing_function.py` | Old `Agent` class, no `get_reinforcements` | rejected at startup |
| sandbox | `runtime_provider.py` | Provider hostname built at runtime | blocked |
| sandbox | `local_llm.py` | Model served on this machine (Ollama port) | blocked |
| sandbox | `unlisted_host.py` | Self-hosted relay on no denylist | blocked |
| sandbox | `raw_ip_socket.py` | Raw IP through the C-level `_socket` module | blocked |
| sandbox | `obfuscated_import.py` | `importlib.import_module("anth" + "ropic")` | blocked |
| sandbox | `guard_tamper.py` | Uninstalls the socket patches, then calls a provider | blocked |
| sandbox | `subprocess_curl.py` | Hands the call to `curl` | blocked |
| sandbox | `native_ctypes.py` | C library socket calls through `ctypes` | blocked |
| sandbox | `swallowed_violation.py` | Catches the blocked error and returns placements | blocked |
| limits | `timeout_agent.py` | Never returns | worker stopped at the time limit |
| limits | `exception_agent.py` | Raises | pause skipped |
| limits | `bad_output.py` | Returns text, not placements | pause skipped |
| referee | `out_of_bounds.py` | One placement at x=99, one legal | approved 1/2 |
| referee | `occupied_cell.py` | Places on a living defence | approved 0/1 |
| referee | `duplicate_cells.py` | Two defences in one cell | approved 1/2 |

After all of them the showcase runs `good_agent.py` again, to show the SDK
still works.

## The layers

- **Static.** `autopilot verify` and `autopilot play` reject banned provider
  imports, provider URLs and files without `get_reinforcements`. This is a
  quick pre-flight check; anything built at runtime gets past it.
- **Sandbox.** Every call runs in a fresh worker process with a CPython
  audit hook installed before the agent is loaded. Unlike patching
  `socket`, an audit hook cannot be removed and also sees C-level calls.
  Inside the worker:
  - the network reaches only loopback and the LLM gateway
    (`AUTOPILOT_GATEWAY_URL`); a raw IP is allowed only if the gateway's
    hostname resolved to it; local LLM ports are blocked;
  - starting programs or processes (`subprocess`, `os.system`, `exec`,
    `fork`), native calls through `ctypes` and walking the interpreter's
    objects (`gc`, `sys._current_frames`) are blocked;
  - provider SDKs cannot be imported, however the import is written.

  A violation ends the call at once and places nothing, even if the agent
  catches the error.
- **Limits.** The call is stopped at its time limit (3.5 s, less when the
  level's pause is shorter). Errors and malformed output skip the pause; the
  game resumes either way.
- **Referee.** The backend approves or drops each placement: on the board,
  a known defence, not on a living defence, not twice in one batch. It
  never moves a placement.

## What this does not cover

- **It is not an OS sandbox.** Code that runs inside the worker can still
  read files and environment variables the participant's account can read.
  No provider keys are there: they stay in the organiser's gateway.
- **Renamed copies of a provider SDK** get past the import check, but their
  calls still go to a provider host, which the network allowlist blocks.
- **The SDK runs on the participant's machine.** A participant who changes
  the SDK, or replaces it with their own program on port 5000, is not bound
  by any of these checks. Only the backend referee and the scoring run on
  organiser machines. The sandbox keeps honest agents inside the rules and
  makes accidental or casual misuse visible; it does not stop a determined
  participant on their own computer.
