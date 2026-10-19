"""Live reinforcement agent that lets an LLM call your own Python tools.

The model gets a short board summary plus three tools defined below. It calls
them as needed (each runs here, inside the sandboxed agent process), then
answers with placements. If the model is slow or unusable, a lane-blocking
heuristic answers instead so the pause is never wasted. Every placement costs
money (state["unitCosts"]); the agent only proposes what state["budget"]
["remaining"] pays for. The backend referee approves or drops every placement
either way.

The LLM is reached through the organiser gateway, so this file holds no key.

    autopilot verify --agent llm_tool_agent.py
    autopilot play --agent llm_tool_agent.py
"""
import json
import os

from autopilot_sdk.llm import LLMClient
from autopilot_sdk.tools import run_tool_loop, tool

GATEWAY_URL = os.environ.get("AUTOPILOT_GATEWAY_URL", "http://127.0.0.1:8004/v1")
GATEWAY_KEY = os.environ.get("AUTOPILOT_GATEWAY_KEY", "")  # team token for a hosted gateway
# Unset: the gateway's configured model is used.
MODEL = os.environ.get("AUTOPILOT_LLM_MODEL") or None
# Optional provider hint, e.g. "low" for Gemini 3 models; unset sends nothing.
REASONING = os.environ.get("AUTOPILOT_LLM_REASONING") or None
# The SDK stops the agent after state["timeBudgetSeconds"] (3.5 s by default,
# less when the level's reinforcement hold is shorter). Keep this much back
# for the fallback and returning the answer.
RESERVE_S = 0.6
ROUND_S = 1.2           # typical time for one model call through the gateway
MAX_PER_PAUSE = 3
# ── DEMO B: more defences per pause ──────────────────────────────────────────
# Comment the line above and uncomment the next one, then save.
# MAX_PER_PAUSE = 6
WIDTH, HEIGHT = 11, 6
DEFENCES = {0: "penguin", 1: "soldier", 2: "archer", 3: "shield"}
# Used when the SDK does not send state["unitCosts"].
DEFAULT_COSTS = {0: 250, 1: 200, 2: 300, 3: 150}

RULES = (
    "You command the defence in a lane-defence game. Board: 11 columns (x 0-10) by "
    "6 rows (y 0-5). Enemies walk from the right (high x) toward x=0 along their row; "
    "reaching x=0 is a breach. Defence IDs: 0=penguin (200 HP, 500 collision damage, "
    "slides along its row), 1=soldier (150 HP, 20 damage, shoots along its row), "
    "2=archer (100 HP, 10 damage, any row), 3=shield (700 HP, no attack, blocks). "
    "Every defence costs money (unit_costs); the placements together must cost no "
    "more than budget_remaining. Each kill earns more budget for later pauses. "
    "If no enemies are on the board yet, more are coming: strengthen the weakest rows. "
    "Call the tools you need in ONE step (several calls at once is fine), then answer; "
    # ""
    # "",
    # ── DEMO A: change the strategy in plain English ─────────────────────────
    # Uncomment the next line and save; the LLM follows it from the next pause.
    # "Strategy: build a shield wall. Use defenceId 3 (shield) for every placement. "
    "you get a single tool step. Always place an archer in front of a clown. "
    "Place at most "
    f"{MAX_PER_PAUSE} defences on free cells, most urgent first. Final answer: raw JSON only, no code "
    'fences: {"reason": "<one short sentence>", "placements": '
    '[{"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 3}}]}'
)


def _alive(units):
    return [u for u in units if u.get("health", 0) > 0 and not u.get("isDead", False)]


def _xy(unit):
    pos = unit.get("position") or {}
    return pos.get("x"), pos.get("y")


def _costs(state):
    costs = dict(DEFAULT_COSTS)
    for kind, cost in (state.get("unitCosts") or {}).items():
        try:
            costs[int(kind)] = float(cost)
        except (TypeError, ValueError):
            pass
    return costs


def _money(state):
    """What this pause may spend; None when the SDK sends no budget."""
    remaining = (state.get("budget") or {}).get("remaining")
    return float(remaining) if isinstance(remaining, (int, float)) else None


def _heuristic(enemies, occupied, defences=(), money=None, costs=DEFAULT_COSTS, limit=MAX_PER_PAUSE):
    """A soldier near the base in the row of each closest enemy (a shield when
    a soldier no longer fits the budget), or in the least defended rows while
    the board is empty between waves."""
    placements, taken = [], set(occupied)
    left = float("inf") if money is None else money
    if not enemies:
        guard = {y: sum(d["health"] for d in defences if _xy(d)[1] == y) for y in range(HEIGHT)}
        enemies = [{"position": {"x": 0, "y": y}} for y in sorted(guard, key=guard.get)]
    for enemy in sorted(enemies, key=lambda e: _xy(e)[0] or 0):
        row = _xy(enemy)[1]
        kind = next((k for k in (1, 3) if costs[k] <= left), None)
        if kind is None:
            break
        for column in (1, 2, 0, 3, 4):
            if isinstance(row, int) and (column, row) not in taken:
                taken.add((column, row))
                left -= costs[kind]
                placements.append({"defenceId": kind, "defenceGridPosition": {"x": column, "y": row}})
                break
        if len(placements) == limit:
            break
    return placements


def _legal(raw, occupied, limit=MAX_PER_PAUSE, money=None, costs=DEFAULT_COSTS):
    """The model's placements that are on the board, on free cells and, in
    the order given, paid for by the budget left."""
    chosen, seen = [], set(occupied)
    left = float("inf") if money is None else money
    for item in raw if isinstance(raw, list) else []:
        try:
            kind = int(item["defenceId"])
            x, y = int(item["defenceGridPosition"]["x"]), int(item["defenceGridPosition"]["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if kind in DEFENCES and 0 <= x < WIDTH and 0 <= y < HEIGHT and (x, y) not in seen \
                and costs[kind] <= left:
            seen.add((x, y))
            left -= costs[kind]
            chosen.append({"defenceId": kind, "defenceGridPosition": {"x": x, "y": y}})
            if len(chosen) == limit:
                break
    return chosen


OPENING_RULES = (
    "You command the defence in a lane-defence game. Board: 11 columns (x 0-10) by "
    "6 rows (y 0-5). Enemies will walk from the right (high x) toward x=0 along "
    "their row; reaching x=0 is a breach. No enemy is on the board yet: you get "
    "the wave that is coming (enemy type id, count, rows it may use). Defence IDs: "
    "0=penguin (200 HP, 500 collision damage, slides along its row), 1=soldier "
    "(150 HP, 20 damage, shoots along its row), 2=archer (100 HP, 10 damage, any "
    "row), 3=shield (700 HP, no attack, blocks). Every defence costs money "
    "(unit_costs) and the placements together must cost no more than budget_remaining; "
    "kills earn more budget for the pauses later in the game, so cover the rows "
    "the wave uses most first. Final answer: raw JSON only, no code fences: "
    '{"reason": "<one short sentence>", "placements": '
    '[{"defenceId": 1, "defenceGridPosition": {"x": 1, "y": 2}}]}'
)


def _wave_rows(state):
    """Rows the wave may use, the busiest first (expected enemies per row)."""
    load = {}
    for entry in state.get("upcomingEnemies", []):
        rows = [r for r in entry.get("allowedRows") or range(HEIGHT) if isinstance(r, int) and 0 <= r < HEIGHT]
        for row in rows:
            load[row] = load.get(row, 0) + (entry.get("count") or 1) / len(rows)
    return sorted(load, key=lambda row: (-load[row], row))


def _opening(state, occupied):
    """Called once before the first enemy (state["phase"] == "start")."""
    money, costs = _money(state), _costs(state)
    rows = [{"position": {"x": 0, "y": row}} for row in _wave_rows(state)]
    fallback = _heuristic(rows, occupied, money=money, costs=costs, limit=HEIGHT)
    deadline = float(state.get("timeBudgetSeconds") or 3.5) - RESERVE_S
    if deadline < ROUND_S:
        return fallback
    wave = {
        "upcomingEnemies": state.get("upcomingEnemies", []),
        "occupiedCells": sorted([x, y] for x, y in occupied if x is not None),
        "budget_remaining": money,
        "unit_costs": {DEFENCES[k]: v for k, v in costs.items()},
    }
    try:
        client = LLMClient(api_key=GATEWAY_KEY, base_url=GATEWAY_URL)
        reply = client.chat.completions.create(
            messages=[{"role": "system", "content": OPENING_RULES},
             {"role": "user", "content": json.dumps(wave)}],
            timeout=deadline,
            **({"model": MODEL} if MODEL else {}),
            **({"reasoning_effort": REASONING} if REASONING else {}),
        )
        text = reply["choices"][0]["message"]["content"].strip()
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```")
        chosen = _legal(json.loads(text).get("placements"), occupied, limit=HEIGHT, money=money, costs=costs)
        if chosen:
            return chosen
    except Exception:
        pass
    return fallback


def get_reinforcements(state: dict) -> list[dict]:
    # ── DEMO D: the safety net ───────────────────────────────────────────────
    # Uncomment the next line and save: the agent crashes, the pause is skipped
    # and the game keeps running. Comment it again and the next pause recovers.
    # 1 / 0
    enemies = _alive(state.get("enemies", []))
    defences = _alive(state.get("defences", []))
    if state.get("phase") == "start":
        return _opening(state, {_xy(d) for d in defences})
    queued = (state.get("counts") or {}).get("enemiesQueued") or 0
    if not enemies and not queued:
        return []  # nothing left to defend against
    occupied = {_xy(d) for d in defences}
    money, costs = _money(state), _costs(state)
    if money is not None and money < min(costs.values()):
        return []  # nothing is affordable until more enemies are killed

    @tool
    def threatened_rows(limit: int = 3) -> list[dict]:
        """Rows ranked by danger: enemy health weighted by closeness to the base,
        minus the health of living defences in that row.

        Args:
            limit: How many rows to return (1-6).
        """
        rows = []
        for y in range(HEIGHT):
            threat = sum(e["health"] * (1 + (WIDTH - (_xy(e)[0] or 0)) / WIDTH)
                         for e in enemies if _xy(e)[1] == y)
            guard = sum(d["health"] for d in defences if _xy(d)[1] == y)
            if threat:
                rows.append({"row": y, "threat": round(threat), "defence_hp": guard,
                             "danger": round(threat - 0.5 * guard)})
        return sorted(rows, key=lambda r: -r["danger"])[:max(1, min(limit, HEIGHT))]

    @tool
    def lane_report(row: int) -> dict:
        """Everything in one row: enemies, defences and free columns.

        Args:
            row: Board row, 0-5.
        """
        return {
            "enemies": [{"type": e.get("type"), "hp": e["health"], "x": _xy(e)[0],
                         "attacking": e.get("isAttacking", False)}
                        for e in enemies if _xy(e)[1] == row],
            "defences": [{"type": d.get("type"), "hp": d["health"], "x": _xy(d)[0]}
                         for d in defences if _xy(d)[1] == row],
            "free_columns": [x for x in range(WIDTH) if (x, row) not in occupied],
        }

    @tool
    def check_cells(cells: list[dict]) -> list[dict]:
        """Check proposed cells before answering; occupied or off-board cells are rejected.

        Args:
            cells: Objects like {"x": 2, "y": 3}.
        """
        out = []
        for cell in cells:
            x, y = cell.get("x"), cell.get("y")
            ok = isinstance(x, int) and isinstance(y, int) and 0 <= x < WIDTH and 0 <= y < HEIGHT
            out.append({"x": x, "y": y, "free": ok and (x, y) not in occupied})
        return out

    # ── DEMO C: add your own tool ────────────────────────────────────────────
    # Uncomment these six lines, then switch the tool list in run_tool_loop below.
    @tool
    def strongest_enemy() -> dict:
        """The living enemy with the most health."""
        e = max(enemies, key=lambda e: e["health"])
        return {"type": e.get("type"), "hp": e["health"], "x": _xy(e)[0], "y": _xy(e)[1]}

    # Tools are ordinary functions, so the agent can call them too: give the
    # model the headline numbers up front in case it only has time to answer.
    ranked = threatened_rows(limit=HEIGHT)
    summary = {
        "elapsedSeconds": state.get("elapsedSeconds"),
        "enemies": len(enemies), "enemies_still_to_come": queued, "defences": len(defences),
        "budget_remaining": money,
        "unit_costs": {DEFENCES[k]: v for k, v in costs.items()},
        "defence_hp_by_row": {y: sum(d["health"] for d in defences if _xy(d)[1] == y) for y in range(HEIGHT)},
        "threatened_rows": ranked,
        "free_columns": {r["row"]: lane_report(r["row"])["free_columns"] for r in ranked[:3]},
    }
    deadline = float(state.get("timeBudgetSeconds") or 3.5) - RESERVE_S
    # Two rounds (tools, then answer) when they fit; one round answers directly.
    rounds = 2 if deadline >= 2 * ROUND_S else 1 if deadline >= ROUND_S else 0
    if rounds == 0:
        return _heuristic(enemies, occupied, defences, money, costs)
    try:
        client = LLMClient(api_key=GATEWAY_KEY, base_url=GATEWAY_URL)
        run = run_tool_loop(
            client,
            [{"role": "system", "content": RULES},
             {"role": "user", "content": json.dumps(summary)}],
            [threatened_rows, lane_report, check_cells],
            # DEMO C: replace the line above with this one.
            # [threatened_rows, lane_report, check_cells, strongest_enemy],
            # The last round sends no tools, so the model has to answer.
            model=MODEL, deadline_s=deadline, max_rounds=rounds,
            **({"reasoning_effort": REASONING} if REASONING else {}),
        )
        if run.content:
            text = run.content.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
            placements = _legal(json.loads(text).get("placements"), occupied, money=money, costs=costs)
            if placements:
                return placements
    except Exception:
        pass
    return _heuristic(enemies, occupied, defences, money, costs)
