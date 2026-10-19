"""Reinforcement agent for checking the LLM path end to end.

At every reinforcement pause it makes one plain LLM call through the organiser
gateway and prints the outcome to the `autopilot play` terminal:

    [llm-check] t=20s OK 1.31s model=qwen.qwen3-next-80b-a3b-instruct cached=no -> soldier at (2,3): <reason>
    [llm-check] t=30s FAILED 0.02s ConnectionError: ... -> fallback soldier at (1,2)

The model's placement is used when it is a legal JSON answer; otherwise a
one-soldier heuristic answers so the pause is not wasted. The backend referee
approves or drops the placement either way.

    autopilot verify --agent llm_check_agent.py
    AUTOPILOT_GATEWAY_URL=http://127.0.0.1:8004/v1 autopilot play --agent llm_check_agent.py
"""
import json
import os
import sys
import time

from autopilot_sdk.llm import LLMClient

GATEWAY_URL = os.environ.get("AUTOPILOT_GATEWAY_URL", "http://127.0.0.1:8004/v1")
GATEWAY_KEY = os.environ.get("AUTOPILOT_GATEWAY_KEY", "")  # team token for a hosted gateway
# Unset: the gateway's configured model is used.
MODEL = os.environ.get("AUTOPILOT_LLM_MODEL") or None
# Kept back from state["timeBudgetSeconds"] for the fallback and the reply.
RESERVE_S = 0.5
WIDTH, HEIGHT = 11, 6
DEFENCES = {0: "penguin", 1: "soldier", 2: "archer", 3: "shield"}

PROMPT = (
    "Lane-defence game, board x 0-10 by y 0-5. Enemies walk from x=10 toward x=0 "
    "along their row. Defence IDs: 0 penguin, 1 soldier, 2 archer, 3 shield. "
    "Pick ONE defence for a free cell to stop the most urgent enemy. Reply with raw "
    'JSON only, no code fences: {"defenceId": 1, "x": 2, "y": 3, "reason": "<short>"}'
)


def _log(message: str) -> None:
    print(f"[llm-check] {message}", file=sys.stderr, flush=True)


def _alive(units):
    return [u for u in units if u.get("health", 0) > 0 and not u.get("isDead", False)]


def _xy(unit):
    pos = unit.get("position") or {}
    return pos.get("x"), pos.get("y")


def _fallback(enemies, occupied):
    """One soldier near the base in the row of the closest enemy."""
    row = _xy(min(enemies, key=lambda e: _xy(e)[0] or 0))[1]
    for x in (2, 1, 3, 0, 4, 5, 6, 7, 8, 9, 10):
        if (x, row) not in occupied:
            return {"defenceId": 1, "defenceGridPosition": {"x": x, "y": row}}
    return None


def _describe(placement):
    pos = placement["defenceGridPosition"]
    return f"{DEFENCES[placement['defenceId']]} at ({pos['x']},{pos['y']})"


def get_reinforcements(state: dict) -> list[dict]:
    enemies = _alive(state.get("enemies", []))
    if not enemies:
        return []
    occupied = {_xy(d) for d in _alive(state.get("defences", []))}
    stamp = f"t={state.get('elapsedSeconds', 0):g}s"
    fallback = _fallback(enemies, occupied)
    budget = float(state.get("timeBudgetSeconds") or 3.5) - RESERVE_S
    if budget <= 0.3:
        _log(f"{stamp} SKIPPED no time for an LLM call -> fallback")
        return [fallback] if fallback else []

    board = {
        # Session and time make every prompt unique, so the gateway cache
        # cannot answer and each pause is a real model call.
        "session": state.get("sessionID"), "elapsedSeconds": state.get("elapsedSeconds"),
        "enemies": [{"type": e.get("type"), "health": e.get("health"), "x": _xy(e)[0], "y": _xy(e)[1]}
                    for e in enemies],
        "occupiedCells": sorted([x, y] for x, y in occupied if x is not None),
    }
    start = time.monotonic()
    try:
        client = LLMClient(api_key=GATEWAY_KEY, base_url=GATEWAY_URL)
        reply = client.chat.completions.create(
            messages=[{"role": "system", "content": PROMPT},
             {"role": "user", "content": json.dumps(board)}],
            timeout=budget,
            **({"model": MODEL} if MODEL else {}),
        )
        elapsed = time.monotonic() - start
        answer = json.loads(reply["choices"][0]["message"]["content"])
        placement = {
            "defenceId": int(answer["defenceId"]),
            "defenceGridPosition": {"x": int(answer["x"]), "y": int(answer["y"])},
        }
    except Exception as exc:  # noqa: BLE001 - any failure is reported, then fallback
        _log(
            f"{stamp} FAILED {time.monotonic() - start:.2f}s {type(exc).__name__}: "
            f"{str(exc)[:160]} -> fallback {_describe(fallback) if fallback else 'none'}"
        )
        return [fallback] if fallback else []

    pos = placement["defenceGridPosition"]
    legal = (
        placement["defenceId"] in DEFENCES
        and 0 <= pos["x"] < WIDTH and 0 <= pos["y"] < HEIGHT
        and (pos["x"], pos["y"]) not in occupied
    )
    cached = "yes" if (reply.get("gateway") or {}).get("cached") else "no"
    model = reply.get("model") or "?"
    if not legal:
        _log(
            f"{stamp} OK {elapsed:.2f}s model={model} cached={cached} but illegal answer "
            f"{answer} -> fallback {_describe(fallback) if fallback else 'none'}"
        )
        return [fallback] if fallback else []
    _log(
        f"{stamp} OK {elapsed:.2f}s model={model} cached={cached} -> "
        f"{_describe(placement)}: {str(answer.get('reason', ''))[:100]}"
    )
    return [placement]
