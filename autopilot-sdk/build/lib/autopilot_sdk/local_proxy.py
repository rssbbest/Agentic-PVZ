"""Participant-side game proxy (`autopilot play` / `autopilot eval`).

The game calls the backend at a fixed local address (127.0.0.1:5000). This
proxy listens there instead, runs the participant's agent on this machine and
forwards everything else to the real backend:

    POST /api/login           forward; on success remember the team's login
                              token (sent with every later call) and its LLM
                              token (given to the agent)
    POST /api/team            the SDK picks what is played: it adds the test
                              case (play) or the eval run and slot (eval) and
                              forwards; remember the level (the wave, budget)
    POST /api/reinforcements  run get_reinforcements(snapshot) locally, put the
                              candidates in snapshot.candidatePlacements and
                              forward; the backend referee approves or drops
                              them and does not call its own SDK. A pause with
                              no candidates is still reported (POST /api/state)
                              so the backend sees every unit of the match
    POST /api/results         forward, then print a game summary with the
                              backend's score and counts
    *    /api/*               forwarded unchanged (state, ...)
    GET  /health              -> 200 {"status": "ok"} (answered locally)
    GET  /suite               progress of `play --all`

Modes (RunContext): `play` with one test case replays it at every press of
play in the game, `play --all` plays the public test cases one after another
and prints the average, and `eval` plays every hidden test case once with a
frozen copy of agent.py and uploads the run log; the backend scores it.

The game (build7) asks for reinforcements right after the level starts. That
first call is the agent's opening: state["phase"] == "start", with the whole
wave in state["upcomingEnemies"]; later calls have phase "pause". So a game
starts with one agent call, not two.

The agent gets state["budget"] ({remaining, spent, unlocked, total}) and
state["unitCosts"] ({"0": 250, ...}), worked out the way the referee does.

Every run writes a log folder (runlog.py). Agent failures, an empty
candidate list and backend errors on a reinforcement all answer the game with
a skip, so the game resumes.
"""
import ast
import base64
import ipaddress
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import requests
from pydantic import ValidationError

from autopilot_sdk import __version__, console
from autopilot_sdk.config import MAX_AGENT_TIME_S
from autopilot_sdk.contracts.schemas import DefencePlacement, ReinforcementSnapshot
from autopilot_sdk.runlog import RunLog, sha256
from autopilot_sdk.runtime.reinforcements import run_reinforcements
from autopilot_sdk.verifier.static_scan import scan_source

logger = logging.getLogger("autopilot.play")
http_log = logging.getLogger("autopilot.http")  # shown with `autopilot play --verbose`
DEFENCE_NAMES = {0: "Penguin", 1: "Soldier", 2: "Archer", 3: "Shield"}
ENEMY_NAMES = {0: "Clown", 1: "Monk", 2: "Wizard", 3: "Pirate"}

MAX_BODY_BYTES = 2 * 1024 * 1024
REINFORCEMENT_FORWARD_TIMEOUT_S = 1.5
LLM_RELAY_TIMEOUT_S = 30.0
# The game (build7) stays frozen at a pause until the reply arrives, and at
# least gameSettings.reinforcementHoldSeconds; the hold is not a deadline. So
# the agent always gets the full reinforcement_timeout_s, and the pause lasts
# only as long as the agent takes.
SKIP = {"placeDefences": False, "defencePlacementData": []}
STATE_REPORT_TIMEOUT_S = 5.0

# One keep-alive session for all backend calls (urllib3's pool is thread-safe;
# the server handles each request on a new thread). A hosted backend behind
# HTTPS and a relay such as Tailscale Funnel costs ~1 s per new TLS
# connection and ~0.3 s on a reused one.
_session = requests.Session()
_session.mount("http://", requests.adapters.HTTPAdapter(pool_maxsize=16))
_session.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=16))

class AgentFileError(ValueError):
    pass


class SuiteError(RuntimeError):
    pass


def fetch_test_cases(backend_url: str) -> list[dict]:
    """The backend's public test cases, in order: [{"code": "TC01", "name": ...}, ...]."""
    try:
        resp = _session.get(backend_url.rstrip("/") + "/api/test-cases", timeout=10)
        resp.raise_for_status()
        cases = [c for c in resp.json() if isinstance(c, dict) and (c.get("code") or c.get("name"))]
    except (requests.RequestException, ValueError) as exc:
        raise SuiteError(f"could not get the test cases from {backend_url}: {exc}") from exc
    if not cases:
        raise SuiteError(f"the backend at {backend_url} has no test cases")
    return [{**c, "code": str(c.get("code") or c["name"]).upper()} for c in cases]


def new_suite(cases: list[dict]) -> dict:
    return {"cases": cases, "scores": {}}


def suite_status(suite: dict | None) -> dict:
    if not suite:
        return {"active": False}
    done = [c for c in suite["cases"] if c["code"] in suite["scores"]]
    pending = [c for c in suite["cases"] if c["code"] not in suite["scores"]]
    scores = list(suite["scores"].values())
    return {
        "active": True, "total": len(suite["cases"]), "done": len(done),
        "next": pending[0]["code"] if pending else None,
        "nextName": pending[0].get("name") if pending else None,
        "finished": not pending,
        "average": round(sum(scores) / len(scores), 2) if scores else None,
    }


def suite_summary(suite: dict) -> str:
    """Score table and average once every test case has been played."""
    lines = [" ALL TEST CASES ".center(72, "="), f"  {'Test case':<12}{'Name':<28}Score"]
    for case in suite["cases"]:
        lines.append(f"  {case['code']:<12}{str(case.get('name', ''))[:26]:<28}{_num(suite['scores'].get(case['code']))}")
    status = suite_status(suite)
    lines.append("Average:   " + console.paint(f"{status['average']:g}", "bold", "cyan")
                 + f"  over {status['total']} test cases")
    lines.append("=" * 72)
    return "\n".join(lines)


@dataclass
class RunContext:
    """What this SDK run plays, shared by every request the game makes."""
    mode: str = "play"                 # "play" or "eval"
    test_case: str | None = None       # play: the test case every game plays
    suite: dict | None = None          # play --all
    auth: dict = field(default_factory=dict)   # {"token", "teamID", "teamName"} after a login
    eval: dict | None = None           # eval: {"source", "sha", "id", "slots", "scores", "finished"}
    log: RunLog | None = None
    finished: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)


def apply_login(run: RunContext, reply: dict) -> None:
    """Remember a successful login; give the agent the team's LLM token unless
    the participant set AUTOPILOT_GATEWAY_KEY themselves."""
    run.auth = {"token": reply.get("token"), "teamID": reply.get("teamID"), "teamName": reply.get("teamName")}
    token = reply.get("llmToken")
    if token and (not os.environ.get("AUTOPILOT_GATEWAY_KEY") or os.environ.get("AUTOPILOT_GATEWAY_KEY_FROM_LOGIN")):
        os.environ["AUTOPILOT_GATEWAY_KEY"] = token
        os.environ["AUTOPILOT_GATEWAY_KEY_FROM_LOGIN"] = "1"


class LoginError(RuntimeError):
    pass


def login(backend_url: str, team: str, password: str) -> dict:
    """Log in from the terminal (the game's login screen does the same)."""
    try:
        resp = _session.post(backend_url.rstrip("/") + "/api/login",
                             json={"teamID": team, "password": password}, timeout=15)
    except requests.RequestException as exc:
        raise LoginError(f"cannot reach the backend: {exc}") from exc
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not resp.ok:
        raise LoginError(body.get("detail") or f"HTTP {resp.status_code}")
    return body


def check_agent_file(agent_path: Path) -> None:
    """Fail fast on an agent that could never produce reinforcements."""
    try:
        source = agent_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise AgentFileError(f"cannot read {agent_path}: {exc}") from exc
    report = scan_source(source)
    if not report.passed:
        raise AgentFileError("static verification failed: " + "; ".join(report.violations))
    tree = ast.parse(source)
    if not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "get_reinforcements"
        for node in tree.body
    ):
        raise AgentFileError("agent.py must define a module-level get_reinforcements(state)")


def wire_candidates(raw: list[dict]) -> list[dict]:
    """Keep candidates the backend schema accepts.

    The backend rejects the whole request if one candidate is off the grid, so
    those are dropped here. Occupancy and duplicates stay with the referee.
    """
    candidates = []
    for item in raw:
        try:
            candidates.append(DefencePlacement.model_validate(item).model_dump())
        except ValidationError:
            logger.warning("Dropping malformed candidate: %s", str(item)[:200])
    return candidates


def _num(value: object) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) else "?"


def _cell(p: dict) -> str:
    g = p.get("defenceGridPosition") or {}
    return f"{DEFENCE_NAMES.get(p.get('defenceId'), p.get('defenceId'))} ({g.get('x')},{g.get('y')})"


def _verdict(candidates: list[dict], approved: list[dict], rejected: list[dict] | None = None) -> str:
    """Every proposed placement with the referee's ✓, and each ✗ with its reason."""
    keys = {json.dumps(p.get("defenceGridPosition"), sort_keys=True) for p in approved}
    taken = [p for p in candidates if json.dumps(p.get("defenceGridPosition"), sort_keys=True) in keys]
    lines = [f"referee approved {len(approved)}/{len(candidates)}: "
             + ("  ".join(console.paint(f"✓ {_cell(p)}", "green") for p in taken) or "none")]
    if rejected:
        lines += ["    " + console.paint(f"✗ {_cell(r)}: {r.get('reason')}", "red") for r in rejected]
    elif len(approved) < len(candidates):
        lines += ["    " + console.paint(f"✗ {_cell(p)}", "red") for p in candidates if p not in taken]
    return "\n".join(lines)


def _budget_line(budget: dict | None) -> str | None:
    if not isinstance(budget, dict):
        return None
    return (f"budget: {_num(budget.get('remaining'))} left · spent {_num(budget.get('spent'))} "
            f"of {_num(budget.get('unlocked'))} unlocked (match {_num(budget.get('total'))})")


def _llm_summary(content: bytes) -> str:
    try:
        message = json.loads(content)["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        return "answered"
    calls = message.get("tool_calls") or []
    if calls:
        return "asked to run " + ", ".join(str((c.get("function") or {}).get("name")) for c in calls)
    return "answered"


LLM_HINTS = {
    401: "check AUTOPILOT_GATEWAY_KEY (your team token)",
    403: "check AUTOPILOT_GATEWAY_KEY (your team token)",
    413: "the request is too large; send the model less",
    429: "rate limit: too many LLM calls per minute, slow down",
}


def format_results(result: dict, scored: dict | None = None) -> str:
    """Human-readable summary of the game's end-of-run result payload, with the
    backend's score from its /api/results reply (``scored``) when given."""
    enemies = result.get("enemies") or []
    defences = result.get("defences") or []
    killed = result.get("enemiesKilled", sum(1 for e in enemies if e.get("killed")))
    destroyed = result.get(
        "defencesDestroyed", sum(1 for d in defences if d.get("destroyed"))
    )
    enemy_total, defence_total = len(enemies), len(defences)
    # The game's report lists only units that fought; the backend completes it
    # from every pause snapshot and the approved placements, so prefer its counts.
    output = (scored or {}).get("evaluator_output")
    stats = output.get("stats") if isinstance(output, dict) and isinstance(output.get("stats"), dict) else {}
    source = "game report"
    if isinstance(stats.get("total_enemies"), (int, float)):
        killed, enemy_total = stats.get("enemies_killed", killed), stats["total_enemies"]
        source = "backend"
    if isinstance(stats.get("defences_placed"), (int, float)):
        defence_total = stats["defences_placed"]
        destroyed = stats.get("defences_destroyed", defence_total - (stats.get("defences_survived") or 0))
        source = "backend"
    outcome = {True: "WON", False: "LOST"}.get(result.get("success"), "UNKNOWN")
    duration = result.get("durationSeconds")
    title = f" GAME OVER - team {result.get('teamID', '?')} (session {result.get('sessionID', '?')}) "
    lines = [
        title.center(72, "="),
        "Result:    " + console.paint(outcome, "bold", "green" if outcome == "WON" else "red"),
        f"Duration:  {duration:.1f} s" if isinstance(duration, (int, float)) else "Duration:  ?",
        f"Enemies:   {_num(killed)}/{_num(enemy_total)} killed"
        f"   (damage dealt by enemies: {_num(result.get('totalEnemyDamage'))})",
        f"Defences:  {_num(destroyed)}/{_num(defence_total)} destroyed"
        f"   (damage dealt by defences: {_num(result.get('totalDefenceDamage'))})",
    ]
    report = (scored or {}).get("report")
    if source == "backend" and isinstance(report, dict) and (report.get("addedDefences") or report.get("addedEnemies")):
        lines.append(console.paint(
            f"           (the game's report listed {_num(report.get('reportedDefences'))} defences and "
            f"{_num(report.get('reportedEnemies'))} enemies, the ones that fought; "
            "the rest were seen in the pause snapshots)", "dim"))
    for label, key, lost in (
        ("Enemy types that fought", "enemyTypeStats", "unitsKilled"),
        ("Defence types that fought", "defenceTypeStats", "unitsDestroyed"),
    ):
        stats = result.get(key) or []
        if stats:
            lines.append(f"{label}:")
            verb = "killed" if lost == "unitsKilled" else "destroyed"
            for row in stats:
                lines.append(
                    f"  {str(row.get('type', '?')):<16} {_num(row.get('unitCount'))} units, "
                    f"{_num(row.get(lost))} {verb}, {_num(row.get('totalDamageDealt'))} damage"
                )
    lines.extend(_score_lines(scored))
    lines.append("=" * 72)
    return "\n".join(lines)


def _score_lines(scored: dict | None) -> list[str]:
    if scored is None:
        return ["Score:     " + console.paint("unknown (the backend's reply did not arrive)", "red")]
    output = scored.get("evaluator_output") if isinstance(scored.get("evaluator_output"), dict) else {}
    score = scored.get("score")
    if not isinstance(score, (int, float)):
        reason = output.get("error") or "the backend returned no score for this session"
        return ["Score:     " + console.paint(f"not scored ({reason})", "red")]
    lines = ["Score:     " + console.paint(f"{score:g}", "bold", "cyan") + "  (from the backend)"]
    stats = output.get("stats") if isinstance(output.get("stats"), dict) else {}
    if stats.get("test_case_budget") is not None:
        lines.append(
            f"Cost:      {_num(stats.get('total_defence_cost'))} of {_num(stats.get('test_case_budget'))} budget"
            + (f"   ({stats['cost_status'].replace('_', ' ').lower()})" if stats.get("cost_status") else "")
        )
    breakdown = output.get("breakdown") if isinstance(output.get("breakdown"), dict) else {}
    parts = [(name, breakdown.get(key)) for name, key in (
        ("outcome", "outcome_score"), ("kills", "kill_score"), ("survival", "survival_score"),
        ("speed", "speed_bonus"), ("economy", "cost_score"))]
    if any(isinstance(v, (int, float)) for _, v in parts):
        lines.append("Breakdown: " + " · ".join(f"{name} {_num(v)}" for name, v in parts
                                                 if isinstance(v, (int, float))))
    return lines


def _json_or_text(data: bytes | None):
    try:
        return json.loads(data) if data else None
    except ValueError:
        return (data or b"")[:2000].decode("utf-8", "replace")


def _error_text(data: bytes | None) -> str:
    body = _json_or_text(data)
    if isinstance(body, dict):
        return str(body.get("error") or body.get("detail") or body)[:300]
    return str(body)[:300]


def start_eval(run: RunContext, backend_url: str) -> str | None:
    """Open the eval run on the backend; returns an error to show, or None."""
    ev = run.eval
    try:
        resp = _session.post(backend_url.rstrip("/") + "/api/eval/start",
                             json={"agentSource": ev["source"].decode("utf-8-sig"), "sdkVersion": __version__},
                             headers={"Authorization": f"Bearer {run.auth.get('token')}"}, timeout=20)
    except requests.RequestException as exc:
        return f"cannot reach the backend: {exc}"
    if not resp.ok:
        return _error_text(resp.content)
    body = resp.json()
    ev.update(id=body["evalId"], slots=int(body["slots"]), scores={})
    if run.log is not None:
        run.log.event("eval_start", evalId=ev["id"], slots=ev["slots"], agentSha256=ev["sha"])
        run.log.update(evalId=ev["id"], hiddenTestCases=ev["slots"], team=run.auth.get("teamID"))
    console.say(f"▶ Eval #{ev['id']} started: {ev['slots']} hidden test cases, agent.py {ev['sha'][:12]} "
                "(frozen for this eval)", "cyan", "bold")
    return None


def finish_eval(run: RunContext, backend_url: str) -> None:
    """Upload the run log and print the eval's final score."""
    ev = run.eval
    summary = {"sdkVersion": __version__, "games": (run.log.meta.get("games") if run.log else [])}
    log = base64.b64encode(run.log.gzipped_events()).decode() if run.log else None
    try:
        resp = _session.post(f"{backend_url.rstrip('/')}/api/eval/{ev['id']}/finish",
                             json={"log": log, "summary": summary},
                             headers={"Authorization": f"Bearer {run.auth.get('token')}"}, timeout=60)
        body = resp.json() if resp.ok else None
        error = None if resp.ok else _error_text(resp.content)
    except (requests.RequestException, ValueError) as exc:
        body, error = None, str(exc)
    lines = [" EVAL RESULT ".center(72, "=")]
    for slot in range(1, ev["slots"] + 1):
        lines.append(f"  Hidden test case {slot:<3} {_num(ev['scores'].get(slot))}")
    if body:
        lines.append("Eval score: " + console.paint(f"{body.get('score'):g}", "bold", "cyan")
                     + f"  (eval #{ev['id']}, {body.get('status')}; recorded by the backend with your agent.py and run log)")
    else:
        lines.append(console.paint(f"The backend did not take the result: {error}", "red"))
    lines.append("=" * 72)
    console.say(stamp=False)
    console.say("\n".join(lines), stamp=False)
    ev["result"] = body
    if run.log is not None:
        run.log.event("eval_finish", reply=body, error=error)
        run.log.update(evalScore=(body or {}).get("score"))
    run.finished.set()


class _Handler(BaseHTTPRequestHandler):
    agent_path: Path = Path("agent.py")
    backend_url: str = "http://localhost:8010"
    reinforcement_timeout_s: float = 10.0
    backend_timeout_s: float = 10.0
    # sessionID / teamID -> {"total", "opening", "perKill", "spent", "costs"}
    ledgers: dict[str, dict] = {}
    levels: dict[str, dict] = {}  # sessionID / teamID -> {"upcomingEnemies", "spawnSettings", "gameSettings"}
    run: RunContext = RunContext()

    @property
    def suite(self) -> dict | None:  # `play --all`: {"cases": [...], "scores": {code: score}}
        return self.run.suite

    def _log(self, kind: str, **data) -> None:
        if self.run.log is not None:
            self.run.log.event(kind, **data)

    def _agent_source(self) -> bytes:
        """The agent that plays now: eval plays its frozen copy, play the file as it is."""
        source = self.run.eval["source"] if self.run.mode == "eval" and self.run.eval else self.agent_path.read_bytes()
        if self.run.log is not None:
            self.run.log.agent(source)
        return source

    def _auth_headers(self) -> dict:
        token = self.run.auth.get("token")
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _send(self, code: int, data: bytes, content_type: str = "application/json") -> None:
        try:
            self.send_response(code)
            self._cors_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except ConnectionError:
            # The caller already gave up, e.g. an agent past its deadline whose
            # relayed LLM reply arrived late. Nothing is waiting for this reply.
            logger.info("%s %s: the caller closed the connection before the reply", self.command, self.path)
            self.close_connection = True

    def _send_json(self, code: int, body: dict) -> None:
        self._send(code, json.dumps(body).encode())

    def _cors_headers(self) -> None:
        # The WebGL page is served from another origin (port), like the backend allows.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            self.headers.get("Access-Control-Request-Headers") or "Content-Type",
        )
        if self.headers.get("Access-Control-Request-Private-Network"):
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length < 0 or length > MAX_BODY_BYTES:
            self._send_json(400, {"detail": "Oversized request body"})
            return None
        return self.rfile.read(length) if length else b""

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        elif self.path == "/suite":
            self._send_json(200, suite_status(self.suite))
        elif self.path.startswith("/api/"):
            self._forward("GET", b"")
        else:
            self._send_json(404, {"detail": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.llm_upstream and self.path.startswith("/llm/v1/"):
            body = self._read_body()
            if body is not None:
                self._relay_llm(body)
            return
        if not self.path.startswith("/api/"):
            self._send_json(404, {"detail": "Not found"})
            return
        body = self._read_body()
        if body is None:
            return
        route = self.path.split("?", 1)[0]
        if route == "/api/login":
            self._handle_login(body)
        elif route == "/api/team":
            self._handle_team(body)
        elif route == "/api/reinforcements":
            self._handle_reinforcements(body)
        elif route == "/api/results":
            # Forward first: the backend scores the game and its reply carries the
            # score. Print (and record, for --all) before the game gets the reply,
            # so the next test case never starts before this one is counted.
            self._forward("POST", body, before_reply=lambda reply: self._print_results(body, reply))
        else:
            self._forward("POST", body)

    def _relay_llm(self, body: bytes) -> None:
        """Pass the agent's LLM call to the organiser's gateway over the SDK's
        warm connection; a fresh agent process would otherwise pay a new TLS
        handshake (~1 s through a hosted relay) at every pause."""
        if self.headers.get("Origin"):  # browsers send Origin; agents do not
            self._send_json(403, {"detail": "The LLM relay serves the agent only"})
            return
        headers = {"Content-Type": "application/json"}
        if self.headers.get("Authorization"):
            headers["Authorization"] = self.headers["Authorization"]
        started = time.monotonic()
        try:
            resp = _session.post(
                self.llm_upstream + self.path[len("/llm/v1"):], data=body,
                headers=headers, timeout=LLM_RELAY_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            console.fail(f"LLM call failed: cannot reach the LLM gateway {self.llm_upstream} ({type(exc).__name__})")
            self._send_json(502, {"detail": f"LLM gateway unreachable: {exc}"[:300]})
            return
        took = time.monotonic() - started
        self._log("llm", status=resp.status_code, seconds=round(took, 2), request_bytes=len(body),
                  reply=resp.content[:4000].decode("utf-8", "replace"))
        if resp.ok:
            console.say(f"    🧠 LLM {_llm_summary(resp.content)} ({took:.1f} s)", "magenta")
        else:
            try:
                detail = json.loads(resp.content).get("detail")
            except (ValueError, AttributeError):
                detail = resp.text[:200]
            hint = LLM_HINTS.get(resp.status_code, "")
            console.fail(f"LLM call failed: HTTP {resp.status_code} {detail or ''}"
                         + (f" - {hint}" if hint else "") + f" ({took:.1f} s)")
        self._send(resp.status_code, resp.content, resp.headers.get("Content-Type", "application/json"))

    def _forward(self, method: str, body: bytes, before_reply=None, extra_headers: dict | None = None) -> bytes | None:
        """Pass the game's request to the backend and its reply to the game.
        ``before_reply(reply_or_None)`` runs before the game gets the reply.
        Returns the backend's reply body, or None if the backend was unreachable."""
        headers = {"Content-Type": self.headers.get("Content-Type") or "application/json",
                   **self._auth_headers(), **(extra_headers or {})}
        try:
            resp = _session.request(
                method, self.backend_url + self.path, data=body or None,
                headers=headers, timeout=self.backend_timeout_s,
            )
        except requests.RequestException as exc:
            logger.warning("Backend unreachable for %s %s: %s", method, self.path, exc)
            if before_reply:
                before_reply(None)
            self._send_json(502, {"detail": f"Backend unreachable: {exc}"[:500]})
            return None
        content = resp.content
        if method == "POST" and self.path.split("?", 1)[0] == "/api/team":
            self._log("level", status=resp.status_code, request=_json_or_text(body), reply=_json_or_text(content))
            if resp.ok:
                self._announce_level(body, content)
            else:
                console.fail(f"The backend refused this game (HTTP {resp.status_code}): {_error_text(content)}")
        if method == "POST" and self.path.split("?", 1)[0] == "/api/team" and resp.ok:
            self._remember_level(body, content)
        if before_reply:
            before_reply(resp.content if resp.ok else None)
        self._send(
            resp.status_code, content,
            resp.headers.get("Content-Type", "application/json"),
        )
        return resp.content if resp.ok else None

    def _handle_login(self, body: bytes) -> None:
        """The game's login screen: forward, and keep the team's tokens."""
        try:
            resp = _session.post(self.backend_url + "/api/login", data=body,
                                 headers={"Content-Type": "application/json"}, timeout=self.backend_timeout_s)
        except requests.RequestException as exc:
            console.fail(f"Login failed: cannot reach the backend ({type(exc).__name__})")
            self._send_json(502, {"detail": f"Backend unreachable: {exc}"[:300]})
            return
        team = (_json_or_text(body) or {}).get("teamID") if isinstance(_json_or_text(body), dict) else None
        self._log("login", team=team, status=resp.status_code)
        if resp.ok:
            reply = resp.json()
            apply_login(self.run, reply)
            console.say(f"✓ Logged in: team {reply.get('teamID')} ({reply.get('teamName')})"
                        + (" · LLM token received" if reply.get("llmToken") else ""), "green", "bold")
            if self.run.log is not None:
                self.run.log.update(team=reply.get("teamID"), teamName=reply.get("teamName"))
        else:
            console.fail(f"Login refused for team {team}: {_error_text(resp.content)}")
        self._send(resp.status_code, resp.content, resp.headers.get("Content-Type", "application/json"))

    def _handle_team(self, body: bytes) -> None:
        """The game asks for its level: the SDK says which test case it is."""
        request = _json_or_text(body)
        if not isinstance(request, dict):
            self._forward("POST", body)
            return
        run = self.run
        request["agentSha256"] = sha256(self._agent_source())
        if run.mode == "eval":
            with run.lock:
                if not run.auth.get("token"):
                    console.fail("Eval needs your team's login: log in on the game page first.")
                    self._send_json(401, {"error": "Log in with your team ID and password first"})
                    return
                if run.eval.get("id") is None:
                    error = start_eval(run, self.backend_url)
                    if error:
                        console.fail(f"Could not start the eval: {error}")
                        self._send_json(409, {"error": error})
                        return
                ev = run.eval
                pending = [i for i in range(1, ev["slots"] + 1) if i not in ev["scores"]]
                if not pending or run.finished.is_set():
                    self._send_json(409, {"error": "This eval is finished. Start a new one from the SDK."})
                    return
            request.update(evalId=ev["id"], evalSlot=pending[0])
            label = f"Hidden test case {pending[0]}/{ev['slots']}"
        else:
            if self.suite is not None and suite_status(self.suite)["finished"]:
                self.suite["scores"].clear()  # all played: the next press of play starts again
            code = suite_status(self.suite)["next"] if self.suite else run.test_case
            if code:
                request["testCase"] = code
            case = self._suite_case(code) or {}
            label = f"Test case {code}" + (f" · {case['name']}" if case.get("name") else "") if code else ""
        self._label = label
        reply = self._forward("POST", json.dumps(request).encode())
        level = _json_or_text(reply) if reply else None
        if isinstance(level, dict) and level.get("sessionID"):
            self.sessions[str(level["sessionID"])] = request.get("testCase")

    def _announce_level(self, request_body: bytes, response_body: bytes) -> None:
        try:
            team = json.loads(request_body).get("teamID")
            level = json.loads(response_body)
            waves = ", ".join(
                f"{e.get('count')}× {ENEMY_NAMES.get(e.get('enemyId'), e.get('enemyId'))}"
                for e in level.get("enemies") or [])
            settings = level.get("gameSettings") or {}
        except (ValueError, AttributeError, TypeError):
            return
        console.say()
        if getattr(self, "_label", ""):
            console.say(f"▶ {self._label}", "cyan", "bold")
        console.say(f"▶ Game started · team {self.run.auth.get('teamID') or team} · enemies coming: {waves or '?'}",
                    "cyan", "bold")
        details = []
        if level.get("budget"):
            details.append(f"budget {level['budget']:g}")
        if settings.get("reinforcementsEnabled", True) and settings.get("reinforcementIntervalSeconds"):
            details.append(f"pause every {settings['reinforcementIntervalSeconds']:g} s of game time, "
                           f"your agent has {self.reinforcement_timeout_s:g} s to answer")
        if details:
            console.say("  " + " · ".join(details), "cyan")

    def _remember_level(self, request_body: bytes, response_body: bytes) -> None:
        """Note the level's wave (for the agent's opening call) and budget (so the
        agent can be told what it may spend)."""
        try:
            level = json.loads(response_body)
        except ValueError:
            return
        if not isinstance(level, dict):
            return
        keys = [str(level.get("sessionID") or "")]
        try:
            keys.append(str(json.loads(request_body).get("teamID") or "").strip().lower())
        except (ValueError, AttributeError):
            pass
        ledger = None
        costs = level.get("unitCosts")
        if isinstance(costs, dict) and isinstance(level.get("budget"), (int, float)):
            starting = level.get("defencePlacementData") or []
            ledger = {
                "total": float(level.get("matchBudget") or level["budget"]),
                "opening": float(level["budget"]),
                "perKill": float(level.get("bountyPerKill") or 0),
                "costs": {str(k): v for k, v in costs.items()},
                "spent": float(sum(costs.get(str(p.get("defenceId")), 0) for p in starting if isinstance(p, dict))),
            }
        wave = {"upcomingEnemies": list(level.get("enemies") or []),
                "spawnSettings": level.get("spawnSettings"), "gameSettings": level.get("gameSettings")}
        for key in filter(None, keys):
            self.levels[key] = wave
            if ledger is not None:
                self.ledgers[key] = ledger

    def _ledger(self, snapshot: dict) -> dict | None:
        return (self.ledgers.get(str(snapshot.get("sessionID") or ""))
                or self.ledgers.get(str(snapshot.get("teamID") or "").strip().lower()))

    def _budget(self, snapshot: dict) -> dict | None:
        """{remaining, spent, unlocked, total} as the referee will compute it:
        the opening budget plus a bounty per enemy killed so far."""
        ledger = self._ledger(snapshot)
        if ledger is None:
            return None
        counts = snapshot.get("counts") or {}
        killed = max(0, int(counts.get("totalEnemiesInWave") or 0) - int(counts.get("enemiesQueued") or 0)
                     - int(counts.get("activeEnemyCount") or 0))
        killed = max(killed, sum(1 for e in snapshot.get("enemies") or [] if e.get("isDead")))
        unlocked = min(ledger["total"], ledger["opening"] + ledger["perKill"] * killed)
        return {"remaining": round(max(0.0, unlocked - ledger["spent"]), 1), "spent": round(ledger["spent"], 1),
                "unlocked": round(unlocked, 1), "total": round(ledger["total"], 1)}

    def _agent_state(self, snapshot: dict, state: dict) -> dict:
        budget = self._budget(snapshot)
        if budget is not None:
            state["budget"] = budget
            state["unitCosts"] = dict(self._ledger(snapshot)["costs"])
        return state

    def _note_decision(self, snapshot: dict, decision: dict) -> None:
        """Take the referee's spent total, which is authoritative."""
        ledger, budget = self._ledger(snapshot), decision.get("budget")
        if ledger is not None and isinstance(budget, dict) and isinstance(budget.get("spent"), (int, float)):
            ledger["spent"] = float(budget["spent"])

    def _skip(self, snapshot: dict) -> dict:
        """A skip reply; with the budget left for the game's coin counter when known."""
        budget = self._budget(snapshot)
        return {**SKIP, "coins": int(budget["remaining"])} if budget else dict(SKIP)

    def _report_state(self, body: bytes) -> None:
        """Send a pause with no placements to the backend in the background, so
        its picture of the match (and the score) includes every unit."""
        def send() -> None:
            try:
                _session.post(self.backend_url + "/api/state", data=body,
                              headers={"Content-Type": "application/json"}, timeout=STATE_REPORT_TIMEOUT_S)
            except requests.RequestException as exc:
                http_log.info("Could not report the pause state: %s", exc)
        threading.Thread(target=send, daemon=True).start()

    def _suite_case(self, code: object) -> dict | None:
        if not self.suite:
            return None
        return next((c for c in self.suite["cases"] if c["code"] == str(code or "").strip().upper()), None)

    def _record_suite(self, result: dict, scored: dict | None) -> None:
        case = self._suite_case((scored or {}).get("testCase") or self.sessions.get(str(result.get("sessionID"))))
        if not case:
            return
        score = (scored or {}).get("score")
        self.suite["scores"][case["code"]] = float(score) if isinstance(score, (int, float)) else 0.0
        status = suite_status(self.suite)
        if status["finished"]:
            console.say(suite_summary(self.suite), stamp=False)
            console.say("Press play in the game to start the test cases again, or Ctrl+C here for the menu.",
                        "dim", stamp=False)
            console.say(stamp=False)
            if self.run.log is not None:
                self.run.log.update(average=status["average"])
        else:
            console.say(f"{case['code']} done: {_num(self.suite['scores'][case['code']])} · "
                        f"{status['done']}/{status['total']} test cases · average so far "
                        f"{_num(status['average'])} · next: {status['next']} {status['nextName'] or ''}"
                        " - press play in the game", "cyan", "bold")

    def _record_eval(self, result: dict, scored: dict | None) -> None:
        ev = self.run.eval
        slot = (scored or {}).get("evalSlot")
        if self.run.mode != "eval" or not ev or not isinstance(slot, int):
            return
        score = (scored or {}).get("score")
        with self.run.lock:
            ev["scores"][slot] = float(score) if isinstance(score, (int, float)) else 0.0
            done = len(ev["scores"])
            last = done >= ev["slots"] and not ev.get("finishing")
            if last:
                ev["finishing"] = True
        if not last:
            console.say(f"Hidden test case {slot}/{ev['slots']} done: {_num(ev['scores'][slot])} · "
                        f"{done}/{ev['slots']} played - press play in the game for the next one", "cyan", "bold")
            return
        threading.Thread(target=finish_eval, args=(self.run, self.backend_url), daemon=True).start()

    def _print_results(self, body: bytes, reply: bytes | None = None) -> None:
        try:
            result = json.loads(body)
        except ValueError:
            logger.warning("Game results were not valid JSON")
            return
        try:
            scored = json.loads(reply) if reply else None
        except ValueError:
            scored = None
        if isinstance(result, dict):
            scored = scored if isinstance(scored, dict) else None
            self._log("result", result=result, reply=scored)
            console.say(stamp=False)
            console.say(format_results(result, scored), stamp=False)
            console.say(stamp=False)
            if self.run.log is not None:
                score = (scored or {}).get("score")
                self.run.log.game({"sessionID": result.get("sessionID"),
                                   "testCase": (scored or {}).get("testCase") or self.sessions.get(str(result.get("sessionID"))),
                                   "evalSlot": (scored or {}).get("evalSlot"),
                                   "won": result.get("success"), "score": score,
                                   "durationSeconds": result.get("durationSeconds")})
            self._record_suite(result, scored)
            self._record_eval(result, scored)
            if self.run.mode == "play" and not self.suite and self.run.test_case:
                console.say(f"Press play in the game to run {self.run.test_case} again, "
                            "or Ctrl+C here to choose another test case.", "dim", stamp=False)

    def _handle_reinforcements(self, body: bytes) -> None:
        try:
            payload = json.loads(body)
            snapshot = ReinforcementSnapshot.model_validate(payload)
        except (ValueError, ValidationError):
            # Let the backend report the malformed request in its usual shape.
            self._forward("POST", body)
            return

        n = self.pauses[snapshot.sessionID] = self.pauses.get(snapshot.sessionID, 0) + 1
        wave = (self.levels.get(str(snapshot.sessionID or ""))
                or self.levels.get(snapshot.teamID.strip().lower()))
        opening = n == 1 and wave is not None
        alive = [e for e in snapshot.enemies if e.health > 0 and not getattr(e, "isDead", False)]
        budget = self._budget(payload)
        label = "▶ Opening" if opening else f"⏸ Pause {n}"
        console.say(f"{label} · {snapshot.elapsedSeconds:.1f} s · {len(alive)} enemies on the board, "
                    f"{snapshot.counts.enemiesQueued} still to come"
                    + (f" · {_num(budget['remaining'])} to spend" if budget else ""), "yellow")
        started = time.monotonic()
        try:
            time_limit = self.reinforcement_timeout_s
            state = self._agent_state(payload, snapshot.model_dump(mode="json"))
            # Agents may read this to size their work, e.g. how many LLM calls fit.
            state["timeBudgetSeconds"] = time_limit
            state["phase"] = "start" if opening else "pause"
            if opening:
                state.update(wave)
            source = self._agent_source()
            raw = run_reinforcements(source, state, time_limit)
        except Exception:
            logger.exception("reinforcement agent failed")
            raw = []
        candidates = wire_candidates(raw)
        took = time.monotonic() - started
        pause = {"session": snapshot.sessionID, "n": n, "phase": "start" if opening else "pause",
                 "snapshot": payload, "budget": budget, "agentSeconds": round(took, 3),
                 "agentReturned": raw if isinstance(raw, list) else str(raw)[:2000], "candidates": candidates}
        if not candidates:
            self._log("pause", **pause, decision=None)
            console.say(f"  agent proposed nothing ({took:.1f} s)", "dim")
            # Empty candidates would make the backend query its own SDK; skip
            # here, but still let the backend see the units on the board.
            self._send_json(200, self._skip(payload))
            self._report_state(body)
            return
        console.say(f"  agent proposed {len(candidates)} in {took:.1f} s")

        payload["candidatePlacements"] = candidates
        try:
            resp = _session.post(
                self.backend_url + self.path, json=payload, headers=self._auth_headers(),
                timeout=REINFORCEMENT_FORWARD_TIMEOUT_S,
            )
            resp.raise_for_status()
            decision = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Backend referee failed, skipping reinforcement: %s", exc)
            self._log("pause", **pause, decision=None, error=str(exc)[:300])
            self._send_json(200, self._skip(payload))
            return
        self._send_json(200, decision)
        self._log("pause", **pause, decision=decision)
        self._note_decision(payload, decision)
        console.say("  " + _verdict(candidates, decision.get("defencePlacementData") or [], decision.get("rejected")))
        if line := _budget_line(decision.get("budget")):
            console.say("  " + line, "dim")

    def log_message(self, fmt: str, *args: object) -> None:
        http_log.info("%s - %s", self.address_string(), fmt % args)


def make_proxy(
    host: str,
    port: int,
    agent_path: Path | str,
    backend_url: str,
    reinforcement_timeout_s: float = 10.0,
    backend_timeout_s: float = 10.0,
    llm_upstream: str | None = None,
    suite: dict | None = None,
    run: RunContext | None = None,
) -> ThreadingHTTPServer:
    agent_path = Path(agent_path).expanduser().resolve()
    check_agent_file(agent_path)
    run = run or RunContext()
    if suite is not None:
        run.suite = suite
    handler = type(
        "Handler",
        (_Handler,),
        {
            "agent_path": agent_path,
            "backend_url": backend_url.rstrip("/"),
            "reinforcement_timeout_s": min(max(float(reinforcement_timeout_s), 0.05), MAX_AGENT_TIME_S),
            "backend_timeout_s": backend_timeout_s,
            "pauses": {},
            "ledgers": {},
            "levels": {},
            "sessions": {},  # sessionID -> test case code
            "run": run,
            "llm_upstream": llm_upstream.rstrip("/") if llm_upstream else None,
        },
    )
    return ThreadingHTTPServer((host, port), handler)


def _remote_gateway(url: str) -> str | None:
    """The gateway URL when it is remote (worth relaying), else None."""
    host = urlsplit(url).hostname if url else None
    if not host or host == "localhost":
        return None
    try:
        if ipaddress.ip_address(host).is_loopback:
            return None
    except ValueError:
        pass
    return url.rstrip("/")


def site_root(backend_url: str) -> str:
    """https://host/ for a hosted backend at https://host/backend."""
    url = backend_url.rstrip("/")
    return url[: -len("/backend")] if url.endswith("/backend") else url


def game_url(backend_url: str) -> str:
    return os.environ.get("AUTOPILOT_GAME_URL") or site_root(backend_url) + "/"


def gateway_upstream(backend_url: str) -> str | None:
    """The LLM gateway the agent's calls go to (relayed by this SDK when remote).
    AUTOPILOT_GATEWAY_URL wins; a hosted backend's own /llm/v1 is the default."""
    if "AUTOPILOT_GATEWAY_UPSTREAM" not in os.environ:
        given = os.environ.get("AUTOPILOT_GATEWAY_URL") or (
            site_root(backend_url) + "/llm/v1" if backend_url.rstrip("/").endswith("/backend") else "")
        os.environ["AUTOPILOT_GATEWAY_UPSTREAM"] = given
    return os.environ["AUTOPILOT_GATEWAY_UPSTREAM"] or None


def _relay_llm_through(port: int, upstream: str | None) -> str | None:
    remote = _remote_gateway(upstream or "")
    if remote:
        # Agents (new processes per call) inherit these and reach the gateway
        # through this server, which keeps the remote connection warm.
        os.environ["AUTOPILOT_GATEWAY_URL"] = f"http://127.0.0.1:{port}/llm/v1"
        os.environ["AUTOPILOT_SDK_RELAY_PORT"] = str(port)
        http_log.info("LLM gateway %s relayed for the agent at %s", remote, os.environ["AUTOPILOT_GATEWAY_URL"])
    elif upstream:
        os.environ["AUTOPILOT_GATEWAY_URL"] = upstream
    return remote


def new_run(mode: str, agent_path: Path | str, backend_url: str, *, test_case: str | None = None,
            suite: dict | None = None, auth: dict | None = None, log_dir: Path | str | None = None) -> RunContext:
    """A RunContext with its run log; eval freezes agent.py now."""
    agent_path = Path(agent_path).expanduser().resolve()
    source = agent_path.read_bytes()
    label = "all" if suite else (test_case or "")
    log = RunLog(log_dir or os.environ.get("AUTOPILOT_LOG_DIR") or "autopilot-runs", mode, label, meta={
        "testCase": test_case, "suite": [c["code"] for c in suite["cases"]] if suite else None,
        "agent": str(agent_path), "agentSha256": sha256(source), "backend": backend_url,
        "sdkVersion": __version__, "team": (auth or {}).get("teamID")})
    log.agent(source)
    run = RunContext(mode=mode, test_case=test_case, suite=suite, auth=dict(auth or {}), log=log)
    if mode == "eval":
        run.eval = {"source": source, "sha": sha256(source), "id": None, "slots": 0, "scores": {}}
    console.add_sink(log.text)
    return run


def serve_run(host: str, port: int, agent_path: Path | str, backend_url: str, run: RunContext,
              reinforcement_timeout_s: float = 10.0) -> str:
    """Serve the game until Ctrl+C ("stopped") or the eval is done ("finished")."""
    upstream = _relay_llm_through(port, gateway_upstream(backend_url))
    try:
        server = make_proxy(host, port, agent_path, backend_url, reinforcement_timeout_s,
                            llm_upstream=upstream, run=run)
    except BaseException:
        close_run(run)
        raise
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.25}, daemon=True)
    thread.start()
    # Ctrl+C only sets a flag: no exception can land halfway through the
    # shutdown, and a one-file binary's launcher (which passes Ctrl+C on, so it
    # arrives twice) is harmless. Sleep-and-look also works on Windows, where an
    # Event.wait() interrupted by Ctrl+C can break its lock.
    # The handler takes no lock (Event.set() does): a second Ctrl+C arriving
    # inside the first handler would otherwise deadlock on that lock.
    stop: list = []
    previous = _on_ctrl_c(lambda: stop.append(True))
    outcome = "stopped"
    try:
        while not run.finished.is_set() and not stop:
            time.sleep(0.3)
        outcome = "finished" if run.finished.is_set() else "stopped"
    except KeyboardInterrupt:  # no handler installed (not the main thread)
        pass
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        close_run(run)
        if previous is not None:
            time.sleep(0.3)  # a duplicate Ctrl+C still lands on the flag
            _on_ctrl_c(previous)
    return outcome


def _on_ctrl_c(handler):
    """Install ``handler`` for Ctrl+C (main thread only); returns the previous one."""
    import signal

    if threading.current_thread() is not threading.main_thread():
        return None
    if not callable(handler) or handler in (signal.SIG_IGN, signal.SIG_DFL, signal.default_int_handler):
        return signal.signal(signal.SIGINT, handler)
    return signal.signal(signal.SIGINT, lambda *_: handler())


def close_run(run: RunContext) -> None:
    if run.log is not None:
        console.remove_sink(run.log.text)
        run.log.close()


def play(
    host: str,
    port: int,
    agent_path: Path | str,
    backend_url: str,
    reinforcement_timeout_s: float = 10.0,
    run_all: bool = False,
    test_case: str | None = None,
    mode: str = "play",
    auth: dict | None = None,
) -> RunContext:
    """`autopilot play` / `autopilot eval` without the interactive console."""
    suite = new_suite(fetch_test_cases(backend_url)) if run_all and mode == "play" else None
    check_agent_file(Path(agent_path))
    run = new_run(mode, agent_path, backend_url, test_case=test_case, suite=suite, auth=auth)
    upstream = gateway_upstream(backend_url)
    token = "set" if os.environ.get("AUTOPILOT_GATEWAY_KEY") else "from your game login"
    console.say(f"Autopilot SDK {__version__} · {mode.upper()}", "bold", stamp=False)
    console.say(f"  agent     {Path(agent_path)}", stamp=False)
    console.say(f"  backend   {backend_url}", stamp=False)
    console.say(f"  LLM       {upstream or 'not set (AUTOPILOT_GATEWAY_URL)'} · team token {token}", stamp=False)
    console.say(f"  game API  http://{host}:{port}  (the game in your browser connects here)", stamp=False)
    console.say(f"  run log   {run.log.dir}", stamp=False)
    if mode == "eval":
        console.say("Open the game page, log in and press play: each game is one hidden test case.", stamp=False)
    elif suite:
        console.say(f"  test cases {len(suite['cases'])}: {', '.join(c['code'] for c in suite['cases'])}", stamp=False)
        console.say("Open the game page and press play for each test case; the average prints at the end.",
                    stamp=False)
    elif test_case:
        console.say(f"Open the game page and press play: every game is test case {test_case}.", stamp=False)
    else:
        console.say("Open the game page, type your team ID and press play.", stamp=False)
    console.say(f"  game      {game_url(backend_url)}   ·   Ctrl+C stops", "dim", stamp=False)
    serve_run(host, port, agent_path, backend_url, run, reinforcement_timeout_s)
    return run
