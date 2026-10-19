"""Minimal LLM agent: each call asks the organiser gateway for placements.

    export AUTOPILOT_GATEWAY_URL=https://<organiser-host>/llm/v1
    export AUTOPILOT_GATEWAY_KEY=<team token>
    ./autopilot-macos-arm64 play --agent agent.py --backend https://<organiser-host>/backend

Every LLM reply (or failure) is printed in the `play` terminal as [agent] lines.
"""
import json
import os
import sys

from autopilot_sdk.llm import LLMClient

PROMPT = (
    "You play a lane-defence game. Grid x 0-10, y 0-5. Enemies walk from x=10 "
    "toward x=0 along their row y; if one reaches x=0 you lose. Defences only "
    "fight enemies in their own row, so put them in the rows that have enemies, "
    "near the base (x 0-4). Defence IDs: 0 penguin, 1 soldier, 2 archer, 3 shield; "
    "shields deal no damage, so mostly use soldiers (1) and archers (2). "
    "phase 'start': no enemies yet; use upcomingEnemies[].allowedRows to cover "
    "the rows enemies will use. phase 'pause': there ARE enemies on the board; "
    "you MUST place 1 or 2 defences in the rows of the enemies with the lowest x. "
    "Each defence costs coins (unitCosts, by defenceId); the placements together "
    "must cost no more than coins. Only use cells listed in freeCells. Reply with raw JSON only: "
    '{"placements": [{"defenceId": 1, "x": 2, "y": 3}], "reason": "<short>"}'
)


def _log(message: str) -> None:
    print(f"[agent] {message}", file=sys.stderr, flush=True)


# Used when the SDK does not send state["unitCosts"].
DEFAULT_COSTS = {0: 250, 1: 200, 2: 300, 3: 150}


def get_reinforcements(state: dict) -> list[dict]:
    defences = [d for d in state.get("defences", []) if d.get("health", 0) > 0]
    enemies = [e for e in state.get("enemies", []) if not e.get("isDead") and e.get("health", 0) > 0]
    occupied = {(d["position"]["x"], d["position"]["y"]) for d in defences}
    costs = {**DEFAULT_COSTS, **{int(k): v for k, v in (state.get("unitCosts") or {}).items()}}
    coins = (state.get("budget") or {}).get("remaining")
    coins = float("inf") if coins is None else float(coins)
    if coins < min(costs.values()):
        _log(f"phase={state.get('phase')} {coins:g} coins: nothing is affordable, skipping the LLM call")
        return []
    board = {
        "phase": state.get("phase"),
        "elapsedSeconds": state.get("elapsedSeconds"),
        "enemies": [{"type": e["type"], "health": e["health"], **e["position"]} for e in enemies],
        "coins": None if coins == float("inf") else coins,
        "unitCosts": costs,
    }
    if state.get("phase") == "start":
        board["upcomingEnemies"] = state.get("upcomingEnemies", [])
    rows = {e["position"]["y"] for e in enemies} or set(range(6))
    board["freeCells"] = [[x, y] for y in sorted(rows) for x in range(5) if (x, y) not in occupied]
    time_left = float(state.get("timeBudgetSeconds") or 3.5) - 0.5
    if time_left < 0.8:  # too short for a call through the hosted gateway
        _log(f"phase={state.get('phase')} {time_left + 0.5:g} s pause: too short for the LLM, using the fallback")
        return _fallback(enemies, occupied, coins, costs)
    try:
        reply = LLMClient(
            api_key=os.environ.get("AUTOPILOT_GATEWAY_KEY", ""),
            base_url=os.environ.get("AUTOPILOT_GATEWAY_URL", "http://127.0.0.1:8004/v1"),
        ).chat.completions.create(
            messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps(board)}],
            timeout=time_left,
        )
        answer = json.loads(reply["choices"][0]["message"]["content"])
    except Exception as exc:  # noqa: BLE001 - report it, then use the fallback
        _log(f"LLM failed: {type(exc).__name__}: {str(exc)[:160]}")
        return _fallback(enemies, occupied, coins, costs)

    placements = []
    for p in answer.get("placements", [])[:3]:
        x, y, kind = int(p["x"]), int(p["y"]), int(p["defenceId"])
        if 0 <= x <= 10 and 0 <= y <= 5 and kind in (0, 1, 2, 3) and (x, y) not in occupied \
                and costs[kind] <= coins:
            occupied.add((x, y))
            coins -= costs[kind]
            placements.append({"defenceId": kind, "defenceGridPosition": {"x": x, "y": y}})
    _log(f"phase={state.get('phase')} LLM -> {placements} ({str(answer.get('reason', ''))[:80]})")
    return placements


def _fallback(enemies, occupied, coins=float("inf"), costs=DEFAULT_COSTS):
    """One soldier in the row of the closest enemy (a shield if a soldier is too dear)."""
    kind = next((k for k in (1, 3) if costs[k] <= coins), None)
    if not enemies or kind is None:
        return []
    row = min(enemies, key=lambda e: e["position"]["x"])["position"]["y"]
    for x in range(11):
        if (x, row) not in occupied:
            return [{"defenceId": kind, "defenceGridPosition": {"x": x, "y": row}}]
    return []
