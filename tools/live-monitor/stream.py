"""Stream the live game as readable terminal output (the monitor's feed, as text).

    ./quickstart.sh watch            or   python tools/live-monitor/stream.py [--history]

For every reinforcement pause it prints what the game sent, each LLM round
(tool calls, tool results, the model's reasoning), the agent's proposals, the
referee's decision and how long the game waited. Game start and game over are
printed too. Needs the monitor (:8020) that quickstart.sh starts.
"""
import argparse
import json
import sys
import time

import httpx

MONITOR = "http://127.0.0.1:8020"
DEF = {0: "Penguin", 1: "Soldier", 2: "Archer", 3: "Shield"}
ENEMY = {0: "Clown", 1: "Monk", 2: "Wizard", 3: "Pirate"}
C = {"dim": "\033[2m", "b": "\033[1m", "r": "\033[0m", "red": "\033[31m", "green": "\033[32m",
     "yellow": "\033[33m", "blue": "\033[34m", "mag": "\033[35m", "cyan": "\033[36m"}


def c(color, text):
    return f"{C[color]}{text}{C['r']}" if sys.stdout.isatty() else str(text)


def out(line=""):
    print(line, flush=True)


def cell(p):
    g = p.get("defenceGridPosition") or p.get("position") or {}
    return f"({g.get('x')},{g.get('y')})"


def unit_name(t):
    t = str(t or "")
    return t.replace("Enemy", "").replace("Enem", "").replace("defence", "").replace("Soilder", "Soldier")


def call_text(call):
    fn = call.get("function") or {}
    try:
        args = ", ".join(f"{k}={json.dumps(v)}" for k, v in json.loads(fn.get("arguments") or "{}").items())
    except ValueError:
        args = fn.get("arguments", "")
    return f"{fn.get('name')}({args})"


def message(resp):
    try:
        return resp["choices"][0]["message"] or {}
    except (KeyError, IndexError, TypeError):
        return {}


def answer(msg):
    text = msg.get("content")
    if not isinstance(text, str):
        return None
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None


def short(value, limit=170):
    text = value if isinstance(value, str) else json.dumps(value, separators=(", ", ": "))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def llm_input(req, first_round):
    """What the agent sent: the board summary (first round) or tool results."""
    lines = []
    msgs = req.get("messages") or []
    if first_round:
        system = next((m.get("content") for m in msgs if m.get("role") == "system"), None)
        if isinstance(system, str):
            lines.append(c("dim", f"     system  {short(system, 110)}"))
        user = next((m.get("content") for m in reversed(msgs) if m.get("role") == "user"), None)
        if user is not None:
            lines.append(f"     user    {short(user)}")
        tools = [t.get("function", {}).get("name") for t in req.get("tools") or []]
        if tools:
            lines.append(c("dim", f"     tools   {', '.join(tools)}"))
    else:
        # Only the results of the previous round's tool calls are new.
        tail = []
        for m in reversed(msgs[:-1] if msgs and msgs[-1].get("role") == "user" else msgs):
            if m.get("role") != "tool":
                break
            tail.append(m)
        for m in reversed(tail):
            lines.append(c("dim", f"     ↳ {m.get('name') or 'tool'} → {short(m.get('content'), 150)}"))
        if msgs and msgs[-1].get("role") == "user":
            lines.append(c("dim", f"     user    {short(msgs[-1].get('content'), 120)}"))
    return lines


class Printer:
    def __init__(self):
        self.ex = {}          # id -> start event (+ end fields)
        self.levels = {}      # sessionID -> level
        self.pauses = {}      # sessionID -> count
        self.rounds = {}      # turn id -> rounds printed
        self.pending = []     # LLM calls of hosted participants, until their referee call

    def handle(self, e):
        kind = e.get("kind")
        if kind == "log":
            line = e.get("line", "")
            if e.get("source") == "sdk" and ("Skipping reinforcement" in line or line.startswith(("Traceback", "  File"))
                                             or (line[:1].isalpha() and "Error:" in line)):
                out(c("red", f"   ⚠ SDK: {line.rstrip()}"))
            return
        if kind == "start":
            self.ex[e["id"]] = dict(e)
            self.on_start(e)
        elif kind == "end" and e["id"] in self.ex:
            x = self.ex[e["id"]]
            x.update(status=e.get("status"), ms=e.get("ms"), response=e.get("response"))
            self.on_end(x)

    def on_start(self, x):
        req = x.get("request") if isinstance(x.get("request"), dict) else {}
        if x.get("hop") == "game-sdk" and x.get("path", "").startswith("/api/team"):
            out()
            out(c("cyan", c("b", f"▶ Team {req.get('teamID')} pressed play · the SDK asks agent.py for an opening setup")))
        elif x.get("turn"):
            sid = req.get("sessionID")
            n = self.pauses[sid] = self.pauses.get(sid, 0) + 1
            alive = [u for u in req.get("enemies", []) if u.get("health", 0) > 0 and not u.get("isDead")]
            defs = [d for d in req.get("defences", []) if d.get("health", 0) > 0]
            queued = (req.get("counts") or {}).get("enemiesQueued", 0)
            out()
            out(c("yellow", c("b", f"━━ Pause {n} · {req.get('elapsedSeconds', 0):.1f} s of game time · team {req.get('teamID')} " + "━" * 30)))
            out(f"🎮 Game froze and sent the board: {len(alive)} enemies alive, {queued} still to come, {len(defs)} defences")
            for u in alive:
                out(c("red", f"     enemy  {unit_name(u.get('type')):<8} at {cell(u)}  {u.get('health'):.0f}/{u.get('maxHealth'):.0f} HP"
                              + ("  attacking" if u.get("isAttacking") else "")))
            out(c("dim", "     defences " + "  ".join(f"{unit_name(d.get('type'))}{cell(d)}" for d in defs)))
        elif x.get("hop") == "agent-gateway" and x.get("parent"):
            turn = x["parent"]
            k = self.rounds[turn] = self.rounds.get(turn, 0) + 1
            x["round"] = k
            out(c("mag", f"🧠 Asking Qwen (round {k})…"))
            for line in llm_input(req, k == 1):
                out(line)

    def level_lines(self, resp):
        gs = resp.get("gameSettings") or {}
        waves = ", ".join(f"{e['count']}× {ENEMY.get(e['enemyId'], e['enemyId'])} (rows {','.join(map(str, e.get('allowedRows') or []))})"
                          for e in resp.get("enemies") or [])
        out(c("cyan", f"  Level from the backend: {waves}" + (f" · budget {resp['budget']:g}" if resp.get("budget") else "")))
        out(c("cyan", f"  {len(resp.get('defencePlacementData') or [])} defences on the board at the start · "
                      + (f"reinforcement pause every {gs.get('reinforcementIntervalSeconds')} s, game waits up to {gs.get('reinforcementHoldSeconds')} s"
                         if gs.get("reinforcementsEnabled", True) else "reinforcements off")))

    def llm_output(self, x):
        resp = x.get("response") if isinstance(x.get("response"), dict) else {}
        ms = x.get("ms") or 0
        if x.get("status") != 200:
            out(c("red", f"   {ms:.0f} ms · LLM call failed ({x.get('status')}): {short(resp, 160)}"))
            return
        msg = message(resp)
        thinking = msg.get("reasoning_content") or msg.get("reasoning")
        if thinking:
            out(c("dim", f"   💭 thinking: {short(thinking, 300)}"))
        calls = msg.get("tool_calls") or []
        if calls:
            out(c("mag", f"   {ms:.0f} ms · output: called ") + ", ".join(call_text(t) for t in calls))
            return
        plan = answer(msg)
        if plan:
            out(c("mag", f"   {ms:.0f} ms · output: final answer"))
            out(c("b", f"💭 \"{plan.get('reason', '')}\""))
            places = plan.get("placements") or []
            if places:
                out("   → " + ", ".join(f"{DEF.get(p.get('defenceId'), p.get('defenceId'))} {cell(p)}" for p in places if isinstance(p, dict)))
        else:
            out(c("red", f"   {ms:.0f} ms · unusable answer: {short(msg.get('content'), 120)}"))

    def on_end(self, x):
        req = x.get("request") if isinstance(x.get("request"), dict) else {}
        resp = x.get("response") if isinstance(x.get("response"), dict) else {}
        path = x.get("path", "")
        if x.get("hop") == "game-sdk" and path.startswith("/api/team") and x.get("status") == 200 and resp.get("sessionID"):
            self.levels[resp["sessionID"]] = resp
            gs = resp.get("gameSettings") or {}
            waves = ", ".join(f"{e['count']}× {ENEMY.get(e['enemyId'], e['enemyId'])} (rows {','.join(map(str, e.get('allowedRows') or []))})"
                              for e in resp.get("enemies") or [])
            out(c("cyan", c("b", f"▶ Game started · team {req.get('teamID')} · session {resp['sessionID']}")))
            self.level_lines(resp)
        elif x.get("hop") == "agent-gateway" and x.get("parent"):
            self.llm_output(x)
        elif x.get("hop") == "agent-gateway" and path.startswith("/v1/chat"):
            self.pending.append(x)  # a hosted participant's call; shown with its pause
        elif x.get("hop") == "sdk-backend" and path.startswith("/api/team") and x.get("status") == 200 \
                and resp.get("sessionID") and resp["sessionID"] not in self.levels:
            self.levels[resp["sessionID"]] = resp
            out()
            out(c("cyan", c("b", f"▶ Participant game started · team {req.get('teamID')} · session {resp['sessionID']}"
                                 " (their SDK, our hosted backend)")))
            self.level_lines(resp)
        elif x.get("hop") == "sdk-backend" and path.startswith("/api/reinforcements"):
            if self.pending:
                t = req.get("elapsedSeconds") or 0
                out()
                out(c("yellow", c("b", (f"━━ Opening setup · team {req.get('teamID')} " if not t else
                                        f"━━ Pause · {t:.1f} s of game time · team {req.get('teamID')} ") + "━" * 26)))
                alive = [u for u in req.get("enemies", []) if u.get("health", 0) > 0 and not u.get("isDead")]
                out(f"🎮 Board: {len(alive)} enemies alive, {(req.get('counts') or {}).get('enemiesQueued', 0)} still to come, "
                    f"{len([d for d in req.get('defences', []) if d.get('health', 0) > 0])} defences")
                for k, call in enumerate(self.pending, 1):
                    call["round"] = k
                    out(c("mag", f"🧠 Qwen round {k}"))
                    for line in llm_input(call.get("request") or {}, k == 1):
                        out(line)
                    self.llm_output(call)
                self.pending = []
            cands = req.get("candidatePlacements") or []
            approved = {cell(p) for p in (resp.get("defencePlacementData") or [])}
            out("📋 Agent proposed: " + (", ".join(f"{DEF.get(p['defenceId'], p['defenceId'])} → {cell(p)}" for p in cands) or "nothing"))
            out("⚖  Referee: " + "  ".join(c("green", f"✓ {DEF.get(p['defenceId'])} {cell(p)}") if cell(p) in approved
                                          else c("red", f"✗ {DEF.get(p['defenceId'])} {cell(p)}") for p in cands)
                + f"   ({len(approved)}/{len(cands)} approved)")
        elif x.get("turn"):
            sid = req.get("sessionID")
            hold = ((self.levels.get(sid) or {}).get("gameSettings") or {}).get("reinforcementHoldSeconds")
            placed = resp.get("defencePlacementData") or []
            if not placed and not self.rounds.get(x["id"]):
                out(c("dim", "   (no placements this pause)"))
            secs = (x.get("ms") or 0) / 1000
            out(c("green", f"✅ {len(placed)} defence(s) placed in the game · answered in {secs:.1f} s"
                           + (f" of the {hold} s the game allows" if hold else "")))
        elif x.get("hop") == "game-sdk" and path.startswith("/api/results"):
            outcome = "WON" if req.get("success") else "LOST"
            out()
            out(c("cyan", c("b", f"🏁 Game over · team {req.get('teamID')} · {outcome} · {req.get('durationSeconds', 0):.1f} s · "
                                 f"enemies killed {req.get('enemiesKilled')}/{len(req.get('enemies') or [])} · "
                                 f"defences lost {req.get('defencesDestroyed')}/{len(req.get('defences') or [])}"
                                 + (f" · backend score {resp.get('score')}" if "score" in resp else ""))))


def main():
    parser = argparse.ArgumentParser(description="Live game log in the terminal")
    parser.add_argument("--history", action="store_true", help="print what already happened first")
    args = parser.parse_args()
    printer = Printer()
    out(c("b", "Autopilot live run: game ⇄ SDK (agent.py) ⇄ Qwen on Bedrock ⇄ backend referee"))
    out(c("dim", "Waiting for the game… (start one at http://localhost:8000)"))
    if args.history:
        for e in httpx.get(MONITOR + "/history", timeout=10).json():
            printer.handle(e)
    while True:
        try:
            with httpx.stream("GET", MONITOR + "/events", timeout=None) as r:
                first = True
                for line in r.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    e = json.loads(line[6:])
                    if first and not args.history:
                        first = False
                    printer.handle(e)
        except httpx.HTTPError:
            out(c("dim", "monitor not reachable; retrying…"))
            time.sleep(2)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
