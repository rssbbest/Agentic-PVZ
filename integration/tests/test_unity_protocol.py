"""The real Unity job protocol: worker selection, callback validation and retries."""
import time
import pytest
from fastapi.testclient import TestClient
import integration.service as service


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(service, 'jobs', {})
    monkeypatch.setattr(service, 'unity_queue', service.deque())
    monkeypatch.setattr(service, 'unity_seen', 0.)
    monkeypatch.setattr(service, 'unity_web_seen', 0.)
    with TestClient(service.app) as client:
        yield client


FORMATION = [{'defenceId': 1, 'defenceGridPosition': {'x': 0, 'y': 0}}]
PRACTICE = {'enemies': [{'enemyId': 0, 'count': 1}], 'defencePlacementData': FORMATION}
RESULT = {'engine': 'unity-webgl', 'totalEnemies': 1, 'killed': 1, 'escaped': 0,
          'defencesPlaced': 1, 'defencesAlive': 1, 'durationSeconds': 3., 'won': True}


def test_practice_requires_a_connected_unity_player(client):
    assert client.post('/unity/practice', json=PRACTICE).status_code == 503


def test_web_practice_is_not_stolen_by_native_worker(client):
    assert client.get('/unity/jobs/next?worker=web').status_code == 200
    r = client.post('/unity/practice', json=PRACTICE)
    assert r.status_code == 202
    key = r.json()['runId']
    assert client.get('/unity/jobs/next?worker=native').json()['runId'] == 0
    job = client.get('/unity/jobs/next?worker=web').json()
    assert job['runId'] == int(key) and job['timeScale'] == 2
    assert job['defencePlacementData'] == FORMATION
    assert client.get('/unity/jobs/next?worker=web').json()['runId'] == 0


def test_frames_keep_web_player_connected_during_a_long_battle(client, monkeypatch):
    client.get('/unity/jobs/next?worker=web')
    key = client.post('/unity/practice', json=PRACTICE).json()['runId']
    client.get('/unity/jobs/next?worker=web')
    monkeypatch.setattr(service, 'unity_web_seen', time.monotonic() - 60)
    assert client.get('/health').json()['unityWebConnected'] is False
    assert client.post(f'/unity/jobs/{key}/frame', json={'snapshot': {'time': 60}}).status_code == 202
    assert client.get('/health').json()['unityWebConnected'] is True


def test_result_matches_assigned_game_and_duplicate_delivery_is_idempotent(client, monkeypatch):
    client.get('/unity/jobs/next?worker=web')
    key = client.post('/unity/practice', json=PRACTICE).json()['runId']
    client.get('/unity/jobs/next?worker=web')
    wrong = {**RESULT, 'totalEnemies': 2, 'killed': 2}
    assert client.post(f'/unity/jobs/{key}/result', json={'result': wrong}).status_code == 422
    assert service.jobs[key]['status'] == 'running'
    tasks = []
    def capture(coro):
        tasks.append(coro)
    monkeypatch.setattr(service, 'spawn', capture)
    try:
        url = f'/unity/jobs/{key}/result'
        assert client.post(url, json={'result': RESULT}).status_code == 202
        assert client.post(url, json={'result': RESULT}).status_code == 202
        assert len(tasks) == 1
        assert client.post(url, json={'result': wrong}).status_code == 409
    finally:
        for coro in tasks:
            coro.close()


def test_unknown_team_and_unassigned_legacy_result_are_rejected(client):
    assert client.post('/api/team', json={'teamID': 'unknown'}).status_code == 404
    assert client.post('/api/results', json={
        'teamID': '9999', 'durationSeconds': 1, 'enemiesKilled': 0,
        'defencesDestroyed': 0}).status_code == 409
