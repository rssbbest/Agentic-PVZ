"""
End-to-end contract test: Unity-style request -> backend /api/* -> real SDK
HTTP service -> participant agent file stored as ``team{id}_<uuid>.py``.
"""
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("autopilot_sdk")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from autopilot_sdk.server import make_server  # noqa: E402

AGENT = (
    "def get_reinforcements(state):\n"
    "    return [\n"
    "        {'defenceId': 1, 'defenceGridPosition': {'x': 4, 'y': 2}},\n"
    "        {'defenceId': 1, 'defenceGridPosition': {'x': 1, 'y': 1}},\n"  # occupied
    "        {'defenceId': 9, 'defenceGridPosition': {'x': 5, 'y': 2}},\n"  # bad id
    "    ]\n"
)

SNAPSHOT = {
    "teamID": "Alpha",
    "sessionID": "s1",
    "elapsedSeconds": 12.0,
    "counts": {
        "activeDefenceCount": 1,
        "activeEnemyCount": 1,
        "enemiesQueued": 8,
        "totalEnemiesInWave": 10,  # match budget 1000
    },
    "defences": [
        {"id": "defenceSoilder_0", "type": "defenceSoilder", "health": 90.0,
         "maxHealth": 100.0, "position": {"x": 1, "y": 1}}
    ],
    # fractional position mid-move must not 422 the whole snapshot
    "enemies": [
        {"id": "EnemClown_0", "type": "EnemClown", "health": 50.0, "maxHealth": 100.0,
         "position": {"x": 6.4, "y": 1.0}, "isAttacking": False, "isDead": False}
    ],
}


class _FakeDB:
    """Answers the Team lookup: name 'Alpha' -> Team(id=7)."""

    async def rollback(self):
        pass

    async def execute(self, stmt):
        # Team lookups find Alpha; a live game has no submission run.
        found = None if "runs" in str(stmt) else SimpleNamespace(id=7, name="Alpha")
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: found))


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "team7_deadbeef.py").write_text(AGENT)
    server = make_server("127.0.0.1", 0, agent_dir=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings, "SDK_URL", f"http://127.0.0.1:{server.server_address[1]}")
    # Budget figures below assume 1000 starting coins, whatever the deployed default.
    monkeypatch.setattr("app.services.evaluator_service.STARTING_COINS", 1000.0)

    async def _db():
        yield _FakeDB()

    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.clear()
    server.shutdown()
    server.server_close()


def test_reinforcements_resolve_agent_by_team_name_and_apply_referee(client):
    resp = client.post("/api/reinforcements", json=SNAPSHOT)
    assert resp.status_code == 200
    body = resp.json()
    assert body["placeDefences"] is True
    assert body["defencePlacementData"] == [{"defenceId": 1, "defenceGridPosition": {"x": 4, "y": 2}}]
    # The referee says why each other candidate was dropped, and what money is left.
    reasons = [r["reason"] for r in body["rejected"]]
    assert reasons == ["cell (1,1) already has a defence", "unknown defenceId 9 (use 0-3)"]
    # 1000 starting coins + 50 for the one kill, out of 1000 + 10 enemies x 50;
    # 200 on the board + 200 approved.
    assert body["budget"] == {"unlocked": 1050.0, "spent": 400.0, "remaining": 650.0, "total": 1500.0}


def test_state_and_screenshot_endpoints_ack(client):
    assert client.post("/api/state", json=SNAPSHOT).status_code == 200
    resp = client.post(
        "/api/screenshot",
        files={"screenshot": ("s.jpg", b"\xff\xd8\xff", "image/jpeg")},
        data={"teamID": "Alpha", "sessionID": "s1"},
    )
    assert resp.status_code == 200 and resp.json()["bytes"] == 3
