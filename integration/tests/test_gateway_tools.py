import json

import httpx
import pytest
from fastapi.testclient import TestClient

from integration import gateway

TOOLS = [{'type': 'function', 'function': {
    'name': 'free_cells', 'description': 'Free columns',
    'parameters': {'type': 'object', 'properties': {'row': {'type': 'integer'}}, 'required': ['row']}}}]
TOOL_CALL = {'id': 'c1', 'type': 'function',
             'function': {'name': 'free_cells', 'arguments': '{"row": 2}'},
             'extra_content': {'google': {'thought_signature': 'sig'}}}


@pytest.fixture
def upstream(monkeypatch, tmp_path):
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        if any(m.get('role') == 'tool' for m in body['messages']):
            message = {'role': 'assistant', 'content': '{"placements": []}'}
        elif 'tools' not in body:
            message = {'role': 'assistant', 'content': '{"defencePlacementData": []}'}
        else:
            message = {'role': 'assistant', 'content': None, 'tool_calls': [TOOL_CALL]}
        return httpx.Response(200, json={'choices': [{'message': message, 'finish_reason': 'stop'}],
                                         'usage': {'total_tokens': 7}})

    real = httpx.AsyncClient
    monkeypatch.setattr(gateway.httpx, 'AsyncClient',
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    gateway.cache.clear()
    monkeypatch.setenv('AUTOPILOT_LLM_API_KEY', 'test-key')
    monkeypatch.setenv('AUTOPILOT_LLM_BASE_URL', 'https://llm.example/v1')
    gateway.failures.clear()
    return sent


def test_tool_calls_are_forwarded_and_returned_verbatim(upstream):
    client = TestClient(gateway.app)
    first = client.post('/v1/chat/completions', json={
        'model': 'm', 'messages': [{'role': 'user', 'content': 'go'}],
        'tools': TOOLS, 'tool_choice': 'auto', 'ignored_field': 1})
    assert first.status_code == 200
    message = first.json()['choices'][0]['message']
    assert message['tool_calls'] == [TOOL_CALL]
    assert upstream[0]['tools'] == TOOLS and upstream[0]['tool_choice'] == 'auto'
    assert 'ignored_field' not in upstream[0]

    second = client.post('/v1/chat/completions', json={'model': 'm', 'tools': TOOLS, 'messages': [
        {'role': 'user', 'content': 'go'}, message,
        {'role': 'tool', 'tool_call_id': 'c1', 'content': '{"result": [0, 1]}'}]})
    assert second.json()['choices'][0]['message']['content'] == '{"placements": []}'
    assert upstream[1]['messages'][1]['tool_calls'][0]['extra_content'] == TOOL_CALL['extra_content']
    assert not gateway.cache  # tool turns are never cached


def test_plain_json_strategy_is_still_cached(upstream):
    client = TestClient(gateway.app)
    body = {'model': 'm', 'messages': [{'role': 'user', 'content': 'plain'}]}
    first = client.post('/v1/chat/completions', json=body).json()
    second = client.post('/v1/chat/completions', json=body).json()
    assert first['gateway']['cached'] is False and second['gateway']['cached'] is True
    assert len(upstream) == 1 and 'tools' not in upstream[0]
    assert second['choices'][0]['message']['content'] == '{"defencePlacementData": []}'


def test_rate_limited_key_hands_over_to_the_next(monkeypatch, tmp_path):
    used = []

    def handler(request):
        key = request.headers['authorization'].removeprefix('Bearer ')
        used.append(key)
        if key == 'k1':
            return httpx.Response(429, json={'error': 'quota'})
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': '{}'}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(gateway.httpx, 'AsyncClient',
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    gateway.cache.clear()
    monkeypatch.setattr(gateway, 'rotation', iter([0, 0]))
    monkeypatch.setenv('AUTOPILOT_LLM_API_KEY', 'k1, k2')
    monkeypatch.setenv('AUTOPILOT_LLM_BASE_URL', 'https://llm.example/v1')
    gateway.failures.clear()
    resp = TestClient(gateway.app).post('/v1/chat/completions', json={
        'model': 'm', 'tools': TOOLS, 'messages': [{'role': 'user', 'content': 'x'}]})
    assert resp.status_code == 200 and used == ['k1', 'k2']


def test_hosted_gateway_requires_a_token_and_rate_limits_it(upstream, monkeypatch):
    monkeypatch.setenv('AUTOPILOT_GATEWAY_TOKENS', 'team-a, team-b')
    monkeypatch.setattr(gateway, 'RATE_PER_MIN', 2)
    gateway.recent.clear()
    client = TestClient(gateway.app)
    ask = lambda token, n: client.post(
        '/v1/chat/completions', headers={'Authorization': f'Bearer {token}'} if token else {},
        json={'messages': [{'role': 'user', 'content': f'q{n}'}], 'tools': TOOLS})
    assert ask(None, 0).status_code == 401
    assert ask('wrong', 1).status_code == 401
    assert ask('team-a', 2).status_code == 200
    assert ask('team-a', 3).status_code == 200
    assert ask('team-a', 4).status_code == 429
    assert ask('team-b', 5).status_code == 200  # limits are per token


def test_each_team_token_is_accepted_and_rate_limited_on_its_own(upstream, monkeypatch):
    monkeypatch.setenv('AUTOPILOT_GATEWAY_TOKENS', 'organiser')
    monkeypatch.setenv('AUTOPILOT_GATEWAY_TEAM_SECRET', 's3cret')
    monkeypatch.setattr(gateway, 'RATE_PER_MIN', 1)
    gateway.recent.clear()
    client = TestClient(gateway.app)
    ask = lambda token, n: client.post(
        '/v1/chat/completions', headers={'Authorization': f'Bearer {token}'},
        json={'messages': [{'role': 'user', 'content': f't{n}'}], 'tools': TOOLS})
    a, b = gateway.team_token('0004', 's3cret'), gateway.team_token('0005', 's3cret')
    assert a.startswith('0004.') and a != b
    assert ask(a, 0).status_code == 200
    assert ask(a, 1).status_code == 429          # team 0004 used its minute
    assert ask(b, 2).status_code == 200          # team 0005 is unaffected
    assert ask('organiser', 3).status_code == 200
    assert ask('0004.' + 'f' * 20, 4).status_code == 401   # forged signature
    assert ask(gateway.team_token('0004', 'other'), 5).status_code == 401


def test_model_is_pinned_and_output_is_capped(upstream, monkeypatch):
    monkeypatch.setenv('AUTOPILOT_LLM_MODEL', 'cheap-model')
    monkeypatch.setenv('AUTOPILOT_LLM_ALLOWED_MODELS', 'other-ok')
    monkeypatch.setattr(gateway, 'MAX_OUTPUT_TOKENS', 512)
    client = TestClient(gateway.app)
    for model, expected in (('expensive-model', 'cheap-model'), ('other-ok', 'other-ok')):
        client.post('/v1/chat/completions', json={
            'model': model, 'max_tokens': 100000, 'tools': TOOLS,
            'messages': [{'role': 'user', 'content': model}]})
        assert upstream[-1]['model'] == expected
        assert upstream[-1]['max_tokens'] == 512


def test_every_call_is_greedy_so_answers_repeat():
    """Fair play: the same board gets the same answer, whatever the agent asks for."""
    hot = gateway.Chat(messages=[{"role": "user", "content": "hi"}], temperature=1.3, top_p=0.5)
    assert hot.options()["temperature"] == 0 and "top_p" not in hot.options()
    assert gateway.Chat(messages=[{"role": "user", "content": "hi"}]).options()["temperature"] == 0
