import os
os.environ.setdefault('SIMULATOR_SECRET', 'test-only-secret')

import pytest
from fastapi.testclient import TestClient
from integration.combat import Battle
from integration.engine_data import catalog
from integration.service import app


def finish(battle):
    for _ in range(4000):
        battle.step()
        if battle.done:
            return battle.result()
    raise AssertionError('Combat did not terminate')


def test_upstream_scene_mapping():
    data = catalog()
    assert [d['name'] for d in data['defences']] == [
        'defencePenguin', 'defenceSoilder', 'defenceArcher', 'defenceShield']
    assert [d['name'] for d in data['enemies']] == [
        'EnemClown', 'EnemyMonk', 'EnemyWizard', 'EnemyPirate']


def test_undefended_wave_breaches_and_is_scored_from_combat():
    result = finish(Battle([{'enemyId': 1, 'count': 3}], []))
    assert result['killed'] == 0 and result['escaped'] == 3
    response = TestClient(app).post('/evaluate', json={'result': result})
    assert response.status_code == 200 and response.json()['score'] == 0


def test_zero_enemies_terminates():
    result = finish(Battle([], []))
    assert result['totalEnemies'] == result['killed'] == result['escaped'] == 0


def test_replay_seed_is_reproducible():
    enemies = [{'enemyId': i, 'count': 2} for i in range(4)]
    formation = [{'defenceId': 1, 'defenceGridPosition': {'x': 0, 'y': y}} for y in range(5)]
    assert finish(Battle(enemies, formation)) == finish(Battle(enemies, formation))


@pytest.mark.parametrize('result', [
    {},
    {'totalEnemies': 1, 'killed': 2, 'escaped': 0, 'defencesPlaced': 1, 'defencesAlive': 1},
    {'totalEnemies': 1, 'killed': 1, 'escaped': 0, 'defencesPlaced': 1, 'defencesAlive': 2},
    {'totalEnemies': 1, 'killed': True, 'escaped': 0, 'defencesPlaced': 1, 'defencesAlive': 1},
    {'totalEnemies': -1, 'killed': 0, 'escaped': 0, 'defencesPlaced': 1, 'defencesAlive': 1},
])
def test_evaluator_rejects_impossible_or_malformed_results(result):
    assert TestClient(app).post('/evaluate', json={'result': result}).status_code == 422


def test_frontend_and_agent_downloads_exist():
    client = TestClient(app)
    assert client.get('/').status_code == 200
    assert client.get('/static/app.js').status_code == 200
    assert client.get('/examples/test').status_code == 200
    assert client.get('/examples/llm').status_code == 200


def test_duplicate_placement_rejected_before_start():
    cell = {'defenceId': 1, 'defenceGridPosition': {'x': 0, 'y': 0}}
    response = TestClient(app).post('/practice', json={
        'enemies': [{'enemyId': 0, 'count': 1}], 'defencePlacementData': [cell, cell]})
    assert response.status_code == 422
