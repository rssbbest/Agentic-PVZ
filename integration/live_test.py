"""Create fresh submissions and check the complete running local stack."""
import argparse
import json
import os
import subprocess
import time
import uuid
from collections import Counter
from pathlib import Path
import requests
from .engine_data import ROOT

BASE = 'http://127.0.0.1:8000'


def wait(submission_id, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = requests.get(f'{BASE}/submissions/{submission_id}', timeout=10)
        response.raise_for_status()
        sub = response.json()
        if sub['status'] != 'pending':
            return sub
        time.sleep(.5)
    raise AssertionError(f'Submission {submission_id} did not finish')


def upload(source, label):
    team = requests.post(f'{BASE}/teams', json={'name': label + '-' + uuid.uuid4().hex[:8]}, timeout=10)
    team.raise_for_status()
    response = requests.post(f'{BASE}/submissions', data={'team_id': team.json()['id']},
        files={'agent_file': ('agent.py', source, 'text/x-python')}, timeout=10)
    response.raise_for_status()
    return response.json()['id']


def summarize(sub):
    return {'submission_id': sub['id'], 'status': sub['status'], 'score': sub['total_score'],
            'runs': dict(Counter(r['status'] for r in sub['runs']))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--negative', action='store_true')
    parser.add_argument('--test-id', type=int, help='Reuse an existing baseline submission')
    parser.add_argument('--llm-id', type=int, help='Reuse an existing LLM submission')
    parser.add_argument('--skip-llm', action='store_true', help='Run without provider credentials')
    parser.add_argument('--report', default='artifacts/live-test-report.json')
    args = parser.parse_args()
    report = {}
    for port in (8000, 8001, 8002, 8004):
        assert requests.get(f'http://127.0.0.1:{port}/health', timeout=5).status_code == 200
    args.test_id = args.test_id or upload((ROOT / 'integration/examples/test_agent.py').read_bytes(), 'baseline-smoke')
    submissions = [('test_agent', args.test_id)]
    if not args.skip_llm:
        args.llm_id = args.llm_id or upload((ROOT / 'agent.py').read_bytes(), 'llm-smoke')
        submissions.append(('llm_agent', args.llm_id))
    for label, sid in submissions:
        sub = wait(sid)
        assert sub['status'] == 'completed' and len(sub['runs']) == 50
        assert all(r['status'] == 'completed' for r in sub['runs']), summarize(sub)
        run = requests.get(f'{BASE}/runs/{sub["runs"][0]["id"]}', timeout=10).json()
        result = run['evaluator_output']['result']
        assert result['killed'] + result['escaped'] == result['totalEnemies']
        report[label] = {**summarize(sub), 'engine': result['engine']}
    rows = requests.get(f'{BASE}/leaderboard', timeout=10).json()
    assert {sid for _, sid in submissions}.issubset({r['submission_id'] for r in rows})
    from websockets.sync.client import connect
    rid = wait(args.test_id)['runs'][0]['id']
    with connect(f'ws://127.0.0.1:8000/runs/{rid}/stream', open_timeout=5) as ws:
        report['backend_stream'] = json.loads(ws.recv(timeout=5))['event']
    report['gateway'] = requests.get('http://127.0.0.1:8004/health', timeout=5).json()
    if not args.skip_llm:
        assert report['gateway']['cacheHits'] >= 49
    if args.negative:
        duplicate = b'''from autopilot_sdk import Agent
class DuplicateAgent(Agent):
    def place_defences(self, enemies):
        p = {"defenceId":1,"defenceGridPosition":{"x":0,"y":0}}
        return [p,p]
'''
        sub = wait(upload(duplicate, 'duplicate-check'))
        assert sub['status'] == 'completed' and sub['total_score'] == 0
        assert all(r['status'] == 'invalid' for r in sub['runs'])
        detail = requests.get(f'{BASE}/runs/{sub["runs"][0]["id"]}', timeout=10).json()
        assert detail['sdk_status']['violations'][0]['type'] == 'duplicate_placement'
        report['duplicate_rejection'] = summarize(sub)
        services_file = ROOT / '.runtime/services.json'
        services = json.loads(services_file.read_text())
        old = services['sdk']
        os.kill(old['pid'], 15)
        time.sleep(1)
        try:
            sub = wait(upload((ROOT / 'integration/examples/test_agent.py').read_bytes(), 'retry-check'))
            assert sub['status'] == 'failed' and sub['total_score'] is None
            assert all(r['status'] == 'failed' for r in sub['runs'])
            report['sdk_outage'] = summarize(sub)
        finally:
            log = (ROOT / '.runtime/sdk.log').open('ab')
            from .settings import service_environment
            cfg = json.loads((ROOT / '.runtime/config.json').read_text())
            proc = subprocess.Popen([str(ROOT / '.venv/bin/python'), '-m', 'autopilot_sdk',
                'serve', '--host', '127.0.0.1', '--port', '8001'], cwd=ROOT,
                env=service_environment('sdk', cfg, 'unity'),
                stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
            log.close()
            services['sdk']['pid'] = proc.pid
            services_file.write_text(json.dumps(services))
            for _ in range(50):
                try:
                    if requests.get('http://127.0.0.1:8001/health', timeout=1).status_code == 200: break
                except requests.RequestException: pass
                time.sleep(.2)
        secret = json.loads((ROOT / '.runtime/config.json').read_text())['ADMIN_SECRET']
        response = requests.post(f'{BASE}/submissions/{sub["id"]}/retry',
            headers={'X-Admin-Secret': secret}, timeout=10)
        response.raise_for_status()
        retried = wait(sub['id'])
        assert retried['status'] == 'completed' and all(r['status'] == 'completed' for r in retried['runs'])
        report['sdk_recovery'] = summarize(retried)
    out = ROOT / args.report
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
