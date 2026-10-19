"""Tests for `autopilot play`: local agent runs, backend gets only candidates."""
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from autopilot_sdk.local_proxy import AgentFileError, format_results, make_proxy
from test_reinforcements import SNAPSHOT, _source


class _FakeBackend(BaseHTTPRequestHandler):
    received: list = []

    def do_GET(self):  # noqa: N802
        data = json.dumps([{"code": "TC01", "name": "Opening"}, {"code": "TC02", "name": "Swarm"}]
                          if self.path == "/api/test-cases" else {"detail": "Not found"}).encode()
        self.send_response(200 if self.path == "/api/test-cases" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.received.append((self.path, body))
        self.auth.append((self.path, self.headers.get("Authorization")))
        if self.path == "/api/login":
            ok = body.get("password") == "secret123"
            reply = ({"token": "tok-7", "teamID": "0007", "teamName": "Seven", "llmToken": "0007.llm"} if ok
                     else {"detail": "Wrong team ID or password"})
            code = 200 if ok else 401
        elif self.path == "/api/eval/start":
            reply, code = {"evalId": 41, "slots": 2, "agentSha256": "x"}, 201
        elif self.path == "/api/eval/41/finish":
            reply, code = {"evalId": 41, "status": "finished", "score": 60.0, "slots": []}, 200
        elif self.path == "/api/team" and body.get("evalId"):
            reply, code = {"sessionID": f"e-{body['evalSlot']}", "enemies": [{"enemyId": 0, "count": 2}]}, 200
        elif self.path == "/api/team" and body.get("testCase") == "OLDSDK":
            # the backend refuses an outdated SDK (and says which build it saw)
            reply, code = {"error": "out of date", "sdk": self.headers.get("X-Autopilot-SDK")}, 426
        elif self.path == "/api/team" and body.get("testCase"):
            reply, code = {"sessionID": f"s-{body['testCase']}", "enemies": [{"enemyId": 0, "count": 3}]}, 200
        elif self.path == "/api/results" and str(body.get("sessionID", "")).startswith("e-"):
            slot = int(body["sessionID"][2:])
            reply, code = {"status": "ok", "score": 50.0 + 20 * (slot - 1), "evalSlot": slot}, 200
        elif self.path == "/api/results" and str(body.get("sessionID", "")).startswith("s-TC"):
            case = body["sessionID"][2:]
            reply, code = {"status": "ok", "score": 71.25 if case == "TC01" else None, "testCase": case,
                           "evaluator_output": {"valid": case == "TC01"}}, 200
        elif self.path == "/api/reinforcements" and body.get("sessionID") == "s-budget":
            first, *rest = body["candidatePlacements"]
            reply = {"placeDefences": True, "defencePlacementData": [first],
                     "budget": {"unlocked": 650.0, "spent": 550.0, "remaining": 100.0, "total": 1000.0},
                     "rejected": [{**p, "reason": "over budget: costs 300, only 100 left"} for p in rest]}
            code = 200
        elif self.path == "/api/reinforcements":
            # Referee stand-in: approve only the first candidate.
            reply = {"placeDefences": True, "defencePlacementData": body["candidatePlacements"][:1]}
            code = 200
        elif self.path == "/api/team" and body["teamID"] == "BUDGET":
            reply = {"sessionID": "s-budget", "enemies": [{"enemyId": 0, "count": 4}],
                     "defencePlacementData": [{"defenceId": 3, "defenceGridPosition": {"x": 0, "y": 0}}],
                     "budget": 600.0, "matchBudget": 1000.0, "bountyPerKill": 50.0,
                     "unitCosts": {"0": 250, "1": 200, "2": 300, "3": 150}}
            code = 200
        elif self.path == "/api/team" and body["teamID"] == "HOLD":
            reply = {"sessionID": "s-hold", "defencePlacementData": [], "enemies": [],
                     "gameSettings": {"reinforcementsEnabled": True,
                                      "reinforcementHoldSeconds": 2.5}}
            code = 200
        elif self.path == "/api/team" and body["teamID"] == "START":
            reply = {"sessionID": "s-start",
                     "defencePlacementData": [{"defenceId": 3, "defenceGridPosition": {"x": 0, "y": 0}}],
                     "enemies": [{"enemyId": 0, "count": 4, "allowedRows": [1, 2]},
                                 {"enemyId": 1, "count": 2, "allowedRows": [4]}],
                     "gameSettings": {"reinforcementsEnabled": True,
                                      "reinforcementHoldSeconds": 5.0}}
            code = 200
        elif self.path == "/api/team":
            reply, code = {"error": f"Team '{body['teamID']}' not found"}, 404
        elif self.path == "/api/results" and body.get("teamID") == "SCORED":
            reply, code = {"status": "ok", "stored": 2, "score": 71.25, "evaluator_output": {
                "valid": True, "stats": {"total_defence_cost": 600, "test_case_budget": 1000,
                                         "cost_status": "UNDER_BUDGET", "enemies_killed": 8,
                                         "total_enemies": 10, "defences_survived": 2,
                                         "defences_placed": 3},
                "breakdown": {"outcome_score": 25.0, "kill_score": 20.0, "survival_score": 16.67,
                              "speed_bonus": 0.0, "cost_score": 9.58}},
                "report": {"reportedDefences": 2, "reportedEnemies": 2, "addedDefences": 1, "addedEnemies": 8}}, 200
        elif self.path == "/api/results" and body.get("teamID") == "INVALID":
            reply, code = {"status": "ok", "stored": 3, "evaluator_output": {
                "valid": False, "error": "Impossible stats: enemiesKilled (9) exceeds total enemies spawned (2)."}}, 200
        else:
            reply, code = {"status": "ok", "stored": 1}, 200
        data = json.dumps(reply).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def _start(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
def backend():
    handler = type("Backend", (_FakeBackend,), {"received": [], "auth": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = _start(server)
    yield f"http://127.0.0.1:{server.server_address[1]}", handler.received, handler.auth
    _stop(server, thread)


@pytest.fixture
def proxy_for(tmp_path, backend):
    servers = []

    def start(agent_body: str, backend_url: str | None = None, suite: dict | None = None, run=None) -> str:
        agent = tmp_path / "agent.py"
        agent.write_bytes(_source(agent_body))
        server = make_proxy("127.0.0.1", 0, agent, backend_url or backend[0], suite=suite, run=run)
        servers.append((server, _start(server)))
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server, thread in servers:
        _stop(server, thread)


def _post(url: str, body: dict):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_reinforcement_runs_locally_and_backend_referees(proxy_for, backend):
    proxy = proxy_for(
        'return [{"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 2}},'
        ' {"defenceId": 0, "defenceGridPosition": {"x": 99, "y": 2}},'
        ' {"defenceId": 3, "defenceGridPosition": {"x": 4, "y": 1}}]'
    )
    code, body = _post(f"{proxy}/api/reinforcements", SNAPSHOT)
    assert code == 200
    assert body == {
        "placeDefences": True,
        "defencePlacementData": [{"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 2}}],
    }
    path, sent = backend[1][0]
    assert path == "/api/reinforcements"
    # Off-grid candidate dropped so the backend schema accepts the request.
    assert sent["candidatePlacements"] == [
        {"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 2}},
        {"defenceId": 3, "defenceGridPosition": {"x": 4, "y": 1}},
    ]
    assert sent["enemies"] == SNAPSHOT["enemies"]
    assert "get_reinforcements" not in json.dumps(sent)


def _wait_for(condition, timeout_s=3.0):
    import time

    deadline = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    return condition()


def test_empty_or_failing_agent_skips_the_referee_but_reports_the_state(proxy_for, backend):
    for agent_body in ("return []", "raise ValueError('boom')", "return 'nope'"):
        code, body = _post(f"{proxy_for(agent_body)}/api/reinforcements", SNAPSHOT)
        assert code == 200
        assert body == {"placeDefences": False, "defencePlacementData": []}
    # The backend still sees each pause's units (for the true end-of-game counts).
    assert _wait_for(lambda: len(backend[1]) == 3)
    assert backend[1] == [("/api/state", SNAPSHOT)] * 3


def test_backend_down_skips_reinforcement(proxy_for):
    proxy = proxy_for(
        'return [{"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 2}}]',
        backend_url="http://127.0.0.1:9",
    )
    code, body = _post(f"{proxy}/api/reinforcements", SNAPSHOT)
    assert code == 200 and body == {"placeDefences": False, "defencePlacementData": []}
    code, _ = _post(f"{proxy}/api/results", {"x": 1})
    assert code == 502


def test_other_game_calls_pass_through(proxy_for, backend):
    proxy = proxy_for("return []")
    assert _post(f"{proxy}/api/results", {"won": True}) == (200, {"status": "ok", "stored": 1})
    assert _post(f"{proxy}/api/team", {"teamID": "nobody"}) == (
        404, {"error": "Team 'nobody' not found"},
    )
    (path1, body1), (path2, body2) = backend[1]
    assert (path1, body1) == ("/api/results", {"won": True})
    # Without a test case the team's own level is asked for; the SDK names the agent that plays.
    assert path2 == "/api/team" and body2["teamID"] == "nobody" and len(body2.pop("agentSha256")) == 64
    assert body2 == {"teamID": "nobody"}


def test_cors_preflight_is_answered(proxy_for):
    proxy = proxy_for("return []")
    request = urllib.request.Request(
        f"{proxy}/api/reinforcements", method="OPTIONS",
        headers={"Origin": "http://localhost:8080", "Access-Control-Request-Headers": "content-type"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 204
        assert response.headers["Access-Control-Allow-Origin"] == "*"
        assert response.headers["Access-Control-Allow-Headers"] == "content-type"


def test_unusable_agent_is_rejected_at_startup(tmp_path):
    bad = tmp_path / "agent.py"
    bad.write_text("import anthropic\ndef get_reinforcements(state): return []\n")
    with pytest.raises(AgentFileError, match="static verification"):
        make_proxy("127.0.0.1", 0, bad, "http://127.0.0.1:9")
    bad.write_text("def something_else(state): return []\n")
    with pytest.raises(AgentFileError, match="get_reinforcements"):
        make_proxy("127.0.0.1", 0, bad, "http://127.0.0.1:9")


RESULT = {
    "teamID": "TEAM", "sessionID": "s1", "success": True, "durationSeconds": 51.94,
    "enemiesKilled": 2, "defencesDestroyed": 1,
    "totalEnemyDamage": 710.0, "totalDefenceDamage": 1880.0,
    "enemies": [{"id": "EnemClown_0", "killed": True}, {"id": "EnemyMonk_0", "killed": True}],
    "defences": [{"id": "defenceArcher_0", "destroyed": False},
                 {"id": "defenceSoilder_0", "destroyed": True}],
    "enemyTypeStats": [{"type": "EnemClown", "unitCount": 1, "unitsKilled": 1, "totalDamageDealt": 650.0}],
    "defenceTypeStats": [{"type": "defenceArcher", "unitCount": 1, "unitsDestroyed": 0, "totalDamageDealt": 560.0}],
}


def test_results_are_printed_and_forwarded(proxy_for, backend, capsys):
    proxy = proxy_for("return []")
    assert _post(f"{proxy}/api/results", RESULT) == (200, {"status": "ok", "stored": 1})
    assert backend[1] == [("/api/results", RESULT)]
    out = capsys.readouterr().out
    assert "GAME OVER - team TEAM (session s1)" in out
    assert "Result:    WON" in out
    assert "Enemies:   2/2 killed" in out
    assert "Defences:  1/2 destroyed" in out
    assert "EnemClown        1 units, 1 killed, 650 damage" in out


def test_game_over_shows_the_backends_score_and_cost(proxy_for, backend, capsys):
    proxy = proxy_for("return []")
    status, reply = _post(f"{proxy}/api/results", {**RESULT, "teamID": "SCORED"})
    assert status == 200 and reply["score"] == 71.25  # the game still gets the reply
    out = capsys.readouterr().out
    assert "GAME OVER - team SCORED" in out
    assert "Score:     71.25  (from the backend)" in out
    assert "Cost:      600 of 1000 budget   (under budget)" in out
    # The backend's counts, which include units the game's report left out.
    assert "Enemies:   8/10 killed" in out
    assert "Defences:  1/3 destroyed" in out
    assert "the game's report listed 2 defences and 2 enemies" in out
    assert "Breakdown: outcome 25 · kills 20 · survival 16.67 · speed 0 · economy 9.58" in out


def test_game_over_says_why_a_game_was_not_scored(proxy_for, backend, capsys):
    proxy = proxy_for("return []")
    _post(f"{proxy}/api/results", {**RESULT, "teamID": "INVALID"})
    assert "Score:     not scored (Impossible stats: enemiesKilled (9) exceeds" in capsys.readouterr().out
    _post(f"{proxy}/api/results", RESULT)  # a reply without score or reason
    assert "not scored (the backend returned no score for this session)" in capsys.readouterr().out


def test_game_over_without_a_backend_reply(proxy_for, capsys):
    proxy = proxy_for("return []", backend_url="http://127.0.0.1:9")
    assert _post(f"{proxy}/api/results", RESULT)[0] == 502
    assert "Score:     unknown (the backend's reply did not arrive)" in capsys.readouterr().out


def test_result_summary_tolerates_missing_fields():
    text = format_results({"teamID": "TEAM"})
    assert "Result:    UNKNOWN" in text and "Duration:  ?" in text


def test_the_games_first_call_is_the_opening_with_the_wave(proxy_for, backend):
    proxy = proxy_for(
        'if state["phase"] == "start":\n'
        '    assert state["enemies"][0]["position"] == {"x": 3, "y": 2}\n'
        '    assert state["enemies"][0]["health"] == 45.0\n'
        '    assert [e["allowedRows"] for e in state["upcomingEnemies"]] == [[1, 2], [4]]\n'
        '    return [{"defenceId": 1, "defenceGridPosition": {"x": 1, "y": row}} for row in (1, 2, 4)]\n'
        'assert "upcomingEnemies" not in state\n'
        'return [{"defenceId": 3, "defenceGridPosition": {"x": 5, "y": 5}}]'
    )
    code, level = _post(f"{proxy}/api/team", {"teamID": "START"})
    # The level passes through unchanged: the agent is not run when it loads.
    assert code == 200 and level["defencePlacementData"] == [
        {"defenceId": 3, "defenceGridPosition": {"x": 0, "y": 0}}]
    assert [path for path, _ in backend[1]] == ["/api/team"]
    snapshot = {**SNAPSHOT, "teamID": "START", "sessionID": "s-start"}
    _post(f"{proxy}/api/reinforcements", {**snapshot, "elapsedSeconds": 0.4})  # the opening (phase "start")
    _post(f"{proxy}/api/reinforcements", {**snapshot, "elapsedSeconds": 3.4})  # a pause
    sent = [body["candidatePlacements"] for path, body in backend[1] if path == "/api/reinforcements"]
    assert [len(c) for c in sent] == [3, 1]
    assert sent[1] == [{"defenceId": 3, "defenceGridPosition": {"x": 5, "y": 5}}]


def test_agent_gets_its_full_time_whatever_the_levels_hold(proxy_for):
    # build7 waits for the reply at a pause; the hold is only a minimum freeze,
    # so a short hold must not shorten the agent's time.
    proxy = proxy_for(
        'if state.get("timeBudgetSeconds") != 7.0:\n'
        '    return []\n'
        'return [{"defenceId": 1, "defenceGridPosition": {"x": 0, "y": 0}}]'
    )
    code, level = _post(f"{proxy}/api/team", {"teamID": "HOLD"})
    assert code == 200 and level["gameSettings"]["reinforcementHoldSeconds"] == 2.5
    snapshot = {**SNAPSHOT, "teamID": "HOLD", "sessionID": "s-hold"}
    code, body = _post(f"{proxy}/api/reinforcements", snapshot)
    assert body["placeDefences"] is True


class _FakeGateway(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.seen.append((self.path, self.headers.get("Authorization"), body))
        data = json.dumps({"choices": [{"message": {"role": "assistant", "content": "{}"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def test_llm_calls_are_relayed_to_a_remote_gateway(tmp_path, backend):
    handler = type("Gateway", (_FakeGateway,), {"seen": []})
    gateway = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    gateway_thread = _start(gateway)
    agent = tmp_path / "agent.py"
    agent.write_bytes(_source("    return []"))
    upstream = f"http://127.0.0.1:{gateway.server_address[1]}/llm/v1"
    proxy = make_proxy("127.0.0.1", 0, agent, backend[0], llm_upstream=upstream)
    proxy_thread = _start(proxy)
    url = f"http://127.0.0.1:{proxy.server_address[1]}/llm/v1/chat/completions"
    try:
        request = urllib.request.Request(
            url, data=json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer team-a"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
        assert handler.seen == [("/llm/v1/chat/completions", "Bearer team-a",
                                 {"messages": [{"role": "user", "content": "hi"}]})]
        # A web page (it sends Origin) may not spend the team's LLM token.
        request.add_header("Origin", "https://evil.example")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request, timeout=10)
        assert exc.value.code == 403 and len(handler.seen) == 1
    finally:
        _stop(proxy, proxy_thread)
        _stop(gateway, gateway_thread)


def test_only_remote_gateways_are_relayed():
    from autopilot_sdk.local_proxy import _remote_gateway

    assert _remote_gateway("http://127.0.0.1:8005/v1") is None
    assert _remote_gateway("http://localhost:8004/v1") is None
    assert _remote_gateway("") is None
    assert _remote_gateway("https://host.ts.net:8443/llm/v1/") == "https://host.ts.net:8443/llm/v1"


def test_network_guard_allows_only_the_sdk_relay_port(monkeypatch):
    from autopilot_sdk.network_guard.guard import BlockedNetworkCallError, _check_host

    monkeypatch.setenv("AUTOPILOT_SDK_RELAY_PORT", "5000")
    _check_host("127.0.0.1", 5000)
    with pytest.raises(BlockedNetworkCallError):
        _check_host("127.0.0.1", 11434)
    monkeypatch.delenv("AUTOPILOT_SDK_RELAY_PORT")
    with pytest.raises(BlockedNetworkCallError):
        _check_host("127.0.0.1", 5000)


def test_agent_gets_its_budget_and_sees_why_placements_were_refused(proxy_for, backend, capsys):
    proxy = proxy_for(
        'assert state["unitCosts"] == {"0": 250, "1": 200, "2": 300, "3": 150}\n'
        '# 600 opening + 50 for the one enemy killed; the starting shield cost 150.\n'
        'assert state["budget"] == {"remaining": 500.0, "spent": 150.0, "unlocked": 650.0, "total": 1000.0}\n'
        'return [{"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 2}},'
        ' {"defenceId": 2, "defenceGridPosition": {"x": 3, "y": 3}}]'
    )
    assert _post(f"{proxy}/api/team", {"teamID": "BUDGET"})[0] == 200
    snapshot = {**SNAPSHOT, "teamID": "BUDGET", "sessionID": "s-budget",
                "counts": {"activeDefenceCount": 1, "activeEnemyCount": 1,
                           "enemiesQueued": 2, "totalEnemiesInWave": 4}}
    code, body = _post(f"{proxy}/api/reinforcements", snapshot)
    assert code == 200 and body["placeDefences"] is True  # the agent's asserts held
    out = capsys.readouterr().out
    assert "500 to spend" in out
    assert "✗ Archer (3,3): over budget: costs 300, only 100 left" in out
    assert "budget: 100 left · spent 550 of 650 unlocked (match 1000)" in out


def test_the_games_coin_counter_follows_the_budget(proxy_for, backend):
    proxy = proxy_for("return []")
    code, level = _post(f"{proxy}/api/team", {"teamID": "BUDGET"})
    assert code == 200 and "coins" not in level   # the level passes through as the backend sent it
    snapshot = {**SNAPSHOT, "teamID": "BUDGET", "sessionID": "s-budget",
                "counts": {"activeDefenceCount": 1, "activeEnemyCount": 1,
                           "enemiesQueued": 2, "totalEnemiesInWave": 4}}
    # 650 unlocked (one kill) - 150 for the starting shield; a skip still tells the game its coins.
    assert _post(f"{proxy}/api/reinforcements", snapshot) == (
        200, {"placeDefences": False, "defencePlacementData": [], "coins": 500})


def _get(url: str):
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read())


def test_all_test_cases_in_one_run_print_each_score_and_the_average(proxy_for, backend, capsys):
    from autopilot_sdk.local_proxy import fetch_test_cases, new_suite

    suite = new_suite(fetch_test_cases(backend[0]))
    proxy = proxy_for("return []", suite=suite)
    assert _get(f"{proxy}/suite") == {"active": True, "total": 2, "done": 0, "next": "TC01",
                                      "nextName": "Opening", "finished": False, "average": None}
    # The SDK, not the game, picks the test case: the game only presses play.
    level = _post(f"{proxy}/api/team", {"teamID": "0007"})[1]
    assert backend[1][-1][1]["testCase"] == "TC01"
    _post(f"{proxy}/api/results", {**RESULT, "teamID": "0007", "sessionID": level["sessionID"]})  # 71.25
    status = _get(f"{proxy}/suite")
    assert (status["done"], status["next"], status["average"]) == (1, "TC02", 71.25)
    assert "TC01 done: 71.25 · 1/2 test cases" in capsys.readouterr().out
    level = _post(f"{proxy}/api/team", {"teamID": "0007"})[1]
    assert backend[1][-1][1]["testCase"] == "TC02"
    _post(f"{proxy}/api/results", {**RESULT, "teamID": "0007", "sessionID": level["sessionID"]})  # not scored: 0
    status = _get(f"{proxy}/suite")
    assert status["finished"] and status["next"] is None and status["average"] == 35.62
    out = capsys.readouterr().out
    assert "ALL TEST CASES" in out and "Average:   35.62  over 2 test cases" in out
    # Pressing play again starts the round over.
    _post(f"{proxy}/api/team", {"teamID": "0007"})
    assert backend[1][-1][1]["testCase"] == "TC01"


def test_play_one_test_case_sends_it_with_every_game(proxy_for, backend):
    from autopilot_sdk.local_proxy import RunContext

    proxy = proxy_for("return []", run=RunContext(test_case="TC03"))
    for _ in range(2):
        assert _post(f"{proxy}/api/team", {"teamID": "0007"})[1]["sessionID"] == "s-TC03"
    assert [b["testCase"] for p, b in backend[1] if p == "/api/team"] == ["TC03", "TC03"]


def test_game_login_is_remembered_and_sent_with_later_calls(proxy_for, backend, capsys, monkeypatch):
    from autopilot_sdk.local_proxy import RunContext

    monkeypatch.setenv("AUTOPILOT_GATEWAY_KEY", "")  # restored afterwards
    monkeypatch.setenv("AUTOPILOT_GATEWAY_KEY_FROM_LOGIN", "")
    run = RunContext(test_case="TC01")
    proxy = proxy_for("return []", run=run)
    assert _post(f"{proxy}/api/login", {"teamID": "0007", "password": "nope"})[0] == 401
    assert "Login refused for team 0007" in capsys.readouterr().out
    assert _post(f"{proxy}/api/login", {"teamID": "0007", "password": "secret123"})[0] == 200
    assert run.auth["teamID"] == "0007" and "Logged in: team 0007 (Seven)" in capsys.readouterr().out
    import os
    assert os.environ["AUTOPILOT_GATEWAY_KEY"] == "0007.llm"  # the agent's LLM token, from the login
    _post(f"{proxy}/api/team", {"teamID": "whatever"})
    assert backend[2][-1] == ("/api/team", "Bearer tok-7")


def test_eval_plays_each_hidden_slot_once_with_a_frozen_agent_and_uploads_the_log(tmp_path, backend, capsys):
    import base64
    import gzip
    import threading as _t

    from autopilot_sdk.local_proxy import new_run

    agent = tmp_path / "agent.py"
    agent.write_bytes(_source("return []"))
    run = new_run("eval", agent, backend[0], auth={"token": "tok-7", "teamID": "0007"}, log_dir=tmp_path / "logs")
    server = make_proxy("127.0.0.1", 0, agent, backend[0], run=run)
    thread = _t.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proxy = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        agent.write_bytes(_source("return [1]"))  # edits during an eval do not count
        for slot in (1, 2):
            level = _post(f"{proxy}/api/team", {"teamID": "0007"})[1]
            assert level["sessionID"] == f"e-{slot}"
            _post(f"{proxy}/api/results", {**RESULT, "teamID": "0007", "sessionID": level["sessionID"]})
        assert run.finished.wait(5)
        assert _post(f"{proxy}/api/team", {"teamID": "0007"})[0] == 409  # all hidden cases played
    finally:
        server.shutdown()
        server.server_close()
    sent = dict(backend[1])
    assert "return []" in sent["/api/eval/start"]["agentSource"]
    teams = [b for p, b in backend[1] if p == "/api/team"]
    assert [(b["evalId"], b["evalSlot"]) for b in teams[:2]] == [(41, 1), (41, 2)]
    assert len({b["agentSha256"] for b in teams}) == 1
    log = gzip.decompress(base64.b64decode(sent["/api/eval/41/finish"]["log"])).decode()
    kinds = [json.loads(line)["kind"] for line in log.splitlines()]
    assert kinds[0] == "session_start" and "eval_start" in kinds and kinds.count("result") == 2
    out = capsys.readouterr().out
    assert "PUBLISH PRIVATE RESULT" in out and "Total score: 60" in out and "Hidden test case 2" in out
    assert out.count("agent.py changed on disk: this run keeps playing the copy from its start") == 1
    assert sent["/api/eval/start"]["board"] == "private"
    run.log.close()
    folder = run.log.dir
    meta = json.loads((folder / "session.json").read_text())
    assert meta["mode"] == "eval" and [g["score"] for g in meta["games"]] == [50.0, 70.0]
    assert len(list(folder.glob("agent-*.py"))) == 1 and (folder / "console.log").read_text()


def test_eval_without_a_login_asks_for_one(tmp_path, backend):
    from autopilot_sdk.local_proxy import new_run

    agent = tmp_path / "agent.py"
    agent.write_bytes(_source("return []"))
    run = new_run("eval", agent, backend[0], log_dir=tmp_path / "logs")
    server = make_proxy("127.0.0.1", 0, agent, backend[0], run=run)
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        code, body = _post(f"http://127.0.0.1:{server.server_address[1]}/api/team", {"teamID": "0007"})
    finally:
        server.shutdown()
        server.server_close()
        run.log.close()
    assert code == 401 and "Log in" in body["error"]


def test_without_all_the_suite_is_inactive(proxy_for):
    assert _get(f"{proxy_for('return []')}/suite") == {"active": False}


def test_all_needs_test_cases_from_the_backend():
    import pytest

    from autopilot_sdk.local_proxy import SuiteError, fetch_test_cases

    with pytest.raises(SuiteError, match="could not get the test cases"):
        fetch_test_cases("http://127.0.0.1:9")


def test_a_resent_pause_gets_the_first_answer_without_running_the_agent_again(proxy_for, backend):
    # The game resends a pause after 10 s without a reply: answer it once.
    proxy = proxy_for('return [{"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 2}}]')
    snapshot = {**SNAPSHOT, "sessionID": "s-resend", "elapsedSeconds": 6.0}
    first = _post(f"{proxy}/api/reinforcements", snapshot)
    again = _post(f"{proxy}/api/reinforcements", snapshot)
    assert first == again
    assert sum(path == "/api/reinforcements" for path, _ in backend[1]) == 1


def test_an_edit_to_agent_py_plays_at_the_next_pause_and_is_announced(tmp_path, backend, capsys):
    from autopilot_sdk.local_proxy import new_run

    agent = tmp_path / "agent.py"
    agent.write_bytes(_source("return []"))
    run = new_run("play", agent, backend[0], log_dir=tmp_path / "logs")
    server = make_proxy("127.0.0.1", 0, agent, backend[0], run=run)
    thread = _start(server)
    proxy = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert _post(f"{proxy}/api/reinforcements", SNAPSHOT)[1]["placeDefences"] is False
        assert "agent.py changed" not in capsys.readouterr().out
        agent.write_bytes(_source('return [{"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 2}}]'))
        later = {**SNAPSHOT, "elapsedSeconds": 30.0}
        assert _post(f"{proxy}/api/reinforcements", later)[1]["placeDefences"] is True
        out = capsys.readouterr().out
        assert out.count("agent.py changed: this pause uses the new version") == 1
    finally:
        _stop(server, thread)
        run.log.close()


def test_publish_public_asks_the_backend_for_a_public_run(tmp_path, backend, capsys):
    from autopilot_sdk.local_proxy import new_run, start_eval

    agent = tmp_path / "agent.py"
    agent.write_bytes(_source("return []"))
    run = new_run("eval", agent, backend[0], auth={"token": "tok-7", "teamID": "0007"},
                  log_dir=tmp_path / "logs", board="public")
    try:
        assert start_eval(run, backend[0]) is None
    finally:
        run.log.close()
    assert dict(backend[1])["/api/eval/start"]["board"] == "public"
    assert "PUBLISH PUBLIC run #41 started" in capsys.readouterr().out


def test_a_practice_game_says_it_is_not_on_the_leaderboard(proxy_for, backend, capsys):
    proxy = proxy_for("return []")
    _post(f"{proxy}/api/results", RESULT)
    assert "Practice game: saved in your run log, but not on the leaderboard" in capsys.readouterr().out


def _eval_proxy(tmp_path, backend):
    from autopilot_sdk.local_proxy import new_run

    agent = tmp_path / "agent.py"
    agent.write_bytes(_source("return []"))
    run = new_run("eval", agent, backend[0], auth={"token": "tok-7", "teamID": "0007"},
                  log_dir=tmp_path / "logs", board="public")
    server = make_proxy("127.0.0.1", 0, agent, backend[0], run=run)
    return run, server, _start(server), f"http://127.0.0.1:{server.server_address[1]}"


def test_a_published_slot_asked_again_before_it_plays_gets_the_same_level(tmp_path, backend):
    run, server, thread, proxy = _eval_proxy(tmp_path, backend)
    try:
        first = _post(f"{proxy}/api/team", {"teamID": "0007"})
        again = _post(f"{proxy}/api/team", {"teamID": "0007"})  # the game's retry
        assert first == again and first[1]["sessionID"] == "e-1"
        assert [b["evalSlot"] for p, b in backend[1] if p == "/api/team"] == [1]  # started once
    finally:
        _stop(server, thread)
        run.log.close()


def test_a_published_slot_left_mid_game_ends_the_run_as_incomplete(tmp_path, backend, capsys):
    run, server, thread, proxy = _eval_proxy(tmp_path, backend)
    try:
        assert _post(f"{proxy}/api/team", {"teamID": "0007"})[1]["sessionID"] == "e-1"
        _post(f"{proxy}/api/reinforcements", {**SNAPSHOT, "sessionID": "e-1", "elapsedSeconds": 4.0})  # it played
        code, body = _post(f"{proxy}/api/team", {"teamID": "0007"})  # page reloaded: slot 1 again
        assert code == 409 and "already started" in body["error"]
        assert run.finished.wait(5)
        out = capsys.readouterr().out
        assert "was started but not finished, so this run is incomplete" in out
        assert "/api/eval/41/finish" in [p for p, _ in backend[1]]
    finally:
        _stop(server, thread)
        run.log.close()


def test_a_game_refused_as_outdated_stops_the_run_so_the_sdk_updates(proxy_for, backend, capsys):
    from autopilot_sdk import updater
    from autopilot_sdk.local_proxy import RunContext

    run = RunContext(test_case="OLDSDK")
    proxy = proxy_for("return []", run=run)
    code, reply = _post(f"{proxy}/api/team", {"teamID": "0007"})
    assert code == 426 and reply["sdk"] == updater.identity()   # the SDK says which build it is
    assert run.outdated.is_set() and not run.finished.is_set()
    assert "HTTP 426" in capsys.readouterr().out



def test_serving_ends_as_outdated_when_the_backend_wants_a_newer_sdk(tmp_path, backend):
    from autopilot_sdk.local_proxy import RunContext, serve_run

    agent = tmp_path / "agent.py"
    agent.write_bytes(_source("return []"))
    run = RunContext(test_case="TC01")
    run.outdated.set()
    assert serve_run("127.0.0.1", 0, agent, backend[0], run) == "outdated"
