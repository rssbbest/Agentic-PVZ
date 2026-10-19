"""Tests for the Unity live-reinforcement SDK contract, without Unity."""
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from autopilot_sdk.runtime.reinforcements import find_agent_file, run_reinforcements
from autopilot_sdk.server import make_server


SNAPSHOT = {
    "teamID": "42",
    "sessionID": "session-test",
    "elapsedSeconds": 20.0,
    "counts": {
        "activeDefenceCount": 1,
        "activeEnemyCount": 1,
        "enemiesQueued": 0,
        "totalEnemiesInWave": 1,
    },
    "defences": [],
    "enemies": [
        {
            "id": "EnemClown_0",
            "type": "EnemClown",
            "health": 45.0,
            "maxHealth": 100.0,
            "position": {"x": 3, "y": 2},
            "isAttacking": False,
            "isDead": False,
        }
    ],
    "candidatePlacements": [],
}


def _source(body: str) -> bytes:
    return ("def get_reinforcements(state):\n" + "".join(
        f"    {line}\n" for line in body.splitlines()
    )).encode()


def test_reinforcement_agent_receives_snapshot_and_returns_candidates():
    result = run_reinforcements(
        _source('return [{"defenceId": 2, "defenceGridPosition": {"x": 10, "y": 5}}]'),
        SNAPSHOT,
        timeout_s=3,
    )
    assert result == [{"defenceId": 2, "defenceGridPosition": {"x": 10, "y": 5}}]


def test_reinforcement_can_skip_and_errors_fail_open():
    assert run_reinforcements(_source("return []"), SNAPSHOT, timeout_s=3) == []
    assert run_reinforcements(_source("raise ValueError('agent error')"), SNAPSHOT, timeout_s=3) == []
    assert run_reinforcements(b"import anthropic\ndef get_reinforcements(state): return []", SNAPSHOT) == []


def test_reinforcement_timeout_returns_skip():
    source = _source("import time\ntime.sleep(5)\nreturn []")
    assert run_reinforcements(source, SNAPSHOT, timeout_s=0.5) == []


def test_find_agent_file_selects_latest_backend_upload(tmp_path):
    older = tmp_path / "team42_old.py"
    newer = tmp_path / "team42_new.py"
    older.write_text("def get_reinforcements(state): return []\n")
    newer.write_text("def get_reinforcements(state): return []\n")
    # Two quick writes can share a timestamp (Windows clock tick ~15 ms).
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))
    assert find_agent_file(tmp_path, "42") == newer
    assert find_agent_file(tmp_path, "TEAM42") == newer
    named = tmp_path / "Friendly Team.py"
    named.write_text("def get_reinforcements(state): return []\n")
    assert find_agent_file(tmp_path, "Friendly Team") == named
    assert find_agent_file(tmp_path, "../42") is None


@pytest.fixture
def reinforcement_service(tmp_path):
    (tmp_path / "team42_latest.py").write_bytes(
        _source('return [{"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 2}}]')
    )
    server = make_server("127.0.0.1", 0, agent_dir=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _post(url: str, body: dict):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_backend_reinforcement_endpoint_contract(reinforcement_service):
    code, body = _post(
        f"{reinforcement_service}/reinforcements",
        {"teamID": "42", "snapshot": SNAPSHOT},
    )
    assert code == 200
    assert body == {
        "defencePlacementData": [
            {"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 2}}
        ]
    }


def test_missing_agent_skips_and_malformed_request_is_rejected(reinforcement_service):
    code, body = _post(
        f"{reinforcement_service}/reinforcements",
        {"teamID": "unknown", "snapshot": {**SNAPSHOT, "teamID": "unknown"}},
    )
    assert code == 200 and body == {"defencePlacementData": []}
    code, _ = _post(
        f"{reinforcement_service}/reinforcements",
        {"teamID": "42", "snapshot": SNAPSHOT},
    )
    assert code == 200
    code, _ = _post(f"{reinforcement_service}/reinforcements", {"teamID": "42"})
    assert code == 400



def test_agent_saved_with_utf8_bom_runs():
    # Windows PowerShell 5.1 and some editors save UTF-8 with a byte-order mark.
    source = b"\xef\xbb\xbf" + _source(
        'return [{"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 2}}]'
    )
    assert run_reinforcements(source, SNAPSHOT, timeout_s=10) == [
        {"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 2}}
    ]
