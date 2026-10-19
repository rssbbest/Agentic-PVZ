"""Smoke test for a built `autopilot` binary (run on each OS in CI).

    python tests/smoke_binary.py dist/autopilot-linux-x86_64

Uses only the standard library. Checks, against a stand-in backend:
  1. `version` and `verify` (the dry run starts the agent in a worker process,
     which is what breaks first in a frozen binary);
  2. `play --test-case TC01`: a whole game through the binary on port 5000:
     login, level (with the test case added by the SDK), the opening and a
     pause (agent runs, referee answers), the result, and the run log folder;
  3. the interactive console asks for agent.py, then the login, and quits on "q".
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BINARY = Path(sys.argv[1]).resolve()
ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "examples" / "reinforcement_agent.py"
seen: list = []


class Backend(BaseHTTPRequestHandler):
    def _reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._reply(200, {"status": "ok"})
        if self.path == "/api/test-cases":
            return self._reply(200, [{"code": "TC01", "name": "Smoke", "enemies": 4}])
        if self.path == "/api/eval/info":
            return self._reply(200, {"hiddenTestCases": 1})
        self._reply(404, {"detail": "not found"})

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        seen.append((self.path, body, self.headers.get("Authorization")))
        if self.path == "/api/login":
            return self._reply(200, {"token": "tok", "teamID": "0042", "teamName": "Smoke", "llmToken": None})
        if self.path == "/api/team":
            return self._reply(200, {
                "sessionID": "smoke-1", "defencePlacementData": [],
                "enemies": [{"enemyId": 0, "count": 4, "allowedRows": [1, 2]}],
                "budget": 3000.0, "matchBudget": 3200.0, "bountyPerKill": 50.0,
                "unitCosts": {"0": 250, "1": 200, "2": 300, "3": 150}})
        if self.path == "/api/reinforcements":
            return self._reply(200, {"placeDefences": True, "defencePlacementData": body["candidatePlacements"][:1],
                                     "budget": {"remaining": 2800, "spent": 200, "unlocked": 3000, "total": 3200}})
        if self.path == "/api/results":
            return self._reply(200, {"status": "ok", "score": 77.5, "testCase": "TC01",
                                     "evaluator_output": {"valid": True}})
        self._reply(200, {"status": "ok"})

    def log_message(self, *args):
        pass


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read())


def snapshot(elapsed):
    return {"teamID": "0042", "sessionID": "smoke-1", "elapsedSeconds": elapsed,
            "counts": {"activeDefenceCount": 0, "activeEnemyCount": 1, "enemiesQueued": 3, "totalEnemiesInWave": 4},
            "defences": [], "enemies": [{"id": "EnemClown_0", "type": "EnemClown", "health": 200.0, "maxHealth": 200.0,
                                         "position": {"x": 9, "y": 1}, "isAttacking": False, "isDead": False}]}


def run(*args, **kw):
    print("$", BINARY.name, *args, flush=True)
    result = subprocess.run([str(BINARY), *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120, **kw)
    print(result.stdout[-2000:], result.stderr[-2000:], flush=True)
    assert result.returncode == 0, f"exit {result.returncode}"
    return result.stdout


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # CI consoles on Windows are cp1252
        stream.reconfigure(encoding="utf-8", errors="replace")
    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{backend.server_address[1]}"
    work = Path(tempfile.mkdtemp())
    env = {**os.environ, "HOME": str(work), "USERPROFILE": str(work), "PYTHONIOENCODING": "utf-8"}

    assert "autopilot" in run("version", env=env)
    out = run("verify", "--agent", str(AGENT), env=env)
    assert "Dry run: opening" in out and "ready to play" in out, "dry run did not run the agent"
    # The starter agent imports autopilot_sdk.llm and .tools (no gateway here: its fallback answers).
    out = run("verify", "--agent", str(ROOT / "examples" / "agent.py"), env=env)
    assert "ready to play" in out, "the LLM starter agent did not load in the binary"

    log = open(work / "play.log", "w", encoding="utf-8")
    proc = subprocess.Popen([str(BINARY), "play", "--agent", str(AGENT), "--backend", url, "--test-case", "tc01"],
                            cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:5000/health", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise AssertionError("the binary never listened on 127.0.0.1:5000")
        game = "http://127.0.0.1:5000"
        assert post(game + "/api/login", {"teamID": "0042", "password": "pw"})[0] == 200
        assert post(game + "/api/team", {"teamID": "0042"})[1]["sessionID"] == "smoke-1"
        code, reply = post(game + "/api/reinforcements", snapshot(0.4))  # the opening
        assert code == 200 and reply.get("placeDefences"), reply
        assert post(game + "/api/reinforcements", snapshot(4.0))[0] == 200
        assert post(game + "/api/results", {"teamID": "0042", "sessionID": "smoke-1", "success": True,
                                            "durationSeconds": 30.0})[1]["score"] == 77.5
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
    print((work / "play.log").read_text(encoding="utf-8", errors="replace")[-3000:])
    team = next(b for p, b, _ in seen if p == "/api/team")
    assert team["testCase"] == "TC01" and len(team["agentSha256"]) == 64, team
    assert any(p == "/api/team" and auth == "Bearer tok" for p, _, auth in seen), "login token not forwarded"
    assert sum(p == "/api/reinforcements" for p, _, _ in seen) >= 1, "the agent's candidates never reached the referee"
    logs = list((work / "autopilot-runs").glob("*_play_TC01"))
    assert logs and (logs[0] / "events.jsonl").stat().st_size > 0, "no run log"
    kinds = [json.loads(line)["kind"] for line in (logs[0] / "events.jsonl").read_text().splitlines()]
    assert "pause" in kinds and "result" in kinds, kinds

    env_menu = {**env, "AUTOPILOT_BACKEND_URL": url}
    (work / "agent.py").write_text(AGENT.read_text())
    result = subprocess.run([str(BINARY)], input=f"{work / 'agent.py'}\nq\n", capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60, cwd=work, env=env_menu)
    print(result.stdout[-1500:], result.stderr[-1500:])
    # Startup asks only for agent.py, then the login ("q" at the team ID quits).
    assert "Backend address" not in result.stdout, "the console must not ask for the server"
    assert "LOG IN" in result.stdout and "Goodbye" in result.stdout, "interactive console failed"
    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
