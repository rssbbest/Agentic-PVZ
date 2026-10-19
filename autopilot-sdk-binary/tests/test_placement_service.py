"""Placement mode + the `autopilot serve` HTTP service the backend calls."""
import base64
from pathlib import Path
import json
import threading
import urllib.error
import urllib.request

import pytest

from autopilot_sdk.contracts.schemas import EnemyEntry
from autopilot_sdk.runtime.loader import load_agent_class
from autopilot_sdk.runtime.placement import run_placement
from autopilot_sdk.server import make_server

ENEMIES = [EnemyEntry(enemyId=0, count=2), EnemyEntry(enemyId=1, count=1)]
HEADER = "from autopilot_sdk import Agent\nclass A(Agent):\n    def place_defences(self, enemies):\n"


def _agent(body: str) -> bytes:
    return (HEADER + "".join(f"        {line}\n" for line in body.splitlines())).encode()


def _violation_types(resp):
    return [v.type for v in resp.sdkStatus.violations]


def test_sample_placement_agent_valid():
    with open(str(Path(__file__).with_name("sample_placement_agent.py")), "rb") as f:
        resp = run_placement(f.read(), ENEMIES, timeout_s=10)
    assert resp.sdkStatus.valid, resp.sdkStatus.violations
    assert len(resp.defencePlacementData) == 4
    assert resp.defencePlacementData[-1].defenceGridPosition.x == 0


def test_placement_only_agent_loads_without_decide():
    assert load_agent_class(str(Path(__file__).with_name("sample_placement_agent.py"))).__name__ == "PlacementAgent"


def test_agent_with_neither_method_rejected(tmp_path):
    p = tmp_path / "agent.py"
    p.write_text("from autopilot_sdk import Agent\nclass A(Agent):\n    pass\n")
    with pytest.raises(ValueError, match="neither"):
        load_agent_class(str(p))


def test_static_scan_violation():
    src = b"import anthropic\n" + _agent("return []")
    resp = run_placement(src, ENEMIES, timeout_s=10)
    assert not resp.sdkStatus.valid
    assert _violation_types(resp) == ["forbidden_api_usage"]


def test_out_of_range_placement():
    src = _agent('return [{"defenceId": 0, "defenceGridPosition": {"x": 11, "y": 0}}]')
    resp = run_placement(src, ENEMIES, timeout_s=10)
    assert _violation_types(resp) == ["invalid_agent_output"]


def test_duplicate_cell():
    cell = '{"defenceId": 0, "defenceGridPosition": {"x": 1, "y": 1}}'
    resp = run_placement(_agent(f"return [{cell}, {cell}]"), ENEMIES, timeout_s=10)
    assert _violation_types(resp) == ["duplicate_placement"]


def test_empty_placement_invalid():
    resp = run_placement(_agent("return []"), ENEMIES, timeout_s=10)
    assert _violation_types(resp) == ["invalid_agent_output"]


def test_exception_and_missing_method():
    resp = run_placement(_agent("raise ValueError('boom')"), ENEMIES, timeout_s=10)
    assert _violation_types(resp) == ["agent_exception"]
    assert "boom" in resp.sdkStatus.violations[0].message

    decide_only = (
        b"from autopilot_sdk import Agent\nclass A(Agent):\n"
        b"    def decide(self, state):\n        return None\n"
    )
    resp = run_placement(decide_only, ENEMIES, timeout_s=10)
    assert _violation_types(resp) == ["agent_exception"]
    assert "place_defences" in resp.sdkStatus.violations[0].message


def test_load_error():
    resp = run_placement(b"this is not python(", ENEMIES, timeout_s=10)
    assert not resp.sdkStatus.valid


def test_timeout():
    resp = run_placement(_agent("import time\ntime.sleep(10)"), ENEMIES, timeout_s=1)
    assert _violation_types(resp) == ["timeout"]


def test_abrupt_process_exit_is_a_violation_and_service_recovers(service):
    src = _agent("import os\nos._exit(0)")
    code, body = _post(f"{service}/run", {
        "agentFileContent": base64.b64encode(src).decode(), "enemies": []})
    assert code == 200
    assert body['sdkStatus']['valid'] is False
    assert body['sdkStatus']['violations'][0]['type'] == 'agent_crashed'
    valid = _agent('return [{"defenceId": 1, "defenceGridPosition": {"x": 0, "y": 0}}]')
    code, body = _post(f"{service}/run", {
        "agentFileContent": base64.b64encode(valid).decode(), "enemies": []})
    assert code == 200 and body['sdkStatus']['valid'] is True


def test_network_guard_blocks_provider():
    body = "import socket\nsocket.create_connection(('api.anthropic.com', 443), timeout=2)"
    resp = run_placement(_agent(body), ENEMIES, timeout_s=10)
    assert _violation_types(resp) == ["forbidden_api_usage"]


@pytest.fixture
def service():
    server = make_server("127.0.0.1", 0, timeout_s=10)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _post(url, body):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_service_backend_contract(service):
    with urllib.request.urlopen(f"{service}/health", timeout=5) as r:
        assert json.loads(r.read()) == {"status": "ok"}

    with open(str(Path(__file__).with_name("sample_placement_agent.py")), "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    code, body = _post(
        f"{service}/run",
        {"agentFileContent": b64, "enemies": [{"enemyId": 0, "count": 5}]},
    )
    assert code == 200
    assert body["sdkStatus"] == {"valid": True, "violations": []}
    assert body["defencePlacementData"][0] == {
        "defenceId": 0,
        "defenceGridPosition": {"x": 0, "y": 4},
    }


def test_service_invalid_agent_omits_placement(service):
    b64 = base64.b64encode(_agent("return []")).decode()
    code, body = _post(f"{service}/run", {"agentFileContent": b64, "enemies": []})
    assert code == 200
    assert body["sdkStatus"]["valid"] is False
    assert "defencePlacementData" not in body


def test_service_bad_request(service):
    code, _ = _post(f"{service}/run", {"agentFileContent": "!!notbase64!!", "enemies": []})
    assert code == 400
    code, _ = _post(f"{service}/run", {"enemies": []})
    assert code == 400
