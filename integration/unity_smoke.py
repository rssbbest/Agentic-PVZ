"""Run SDK-generated formations through a connected real Unity practice worker."""
import argparse
import base64
import json
import time
from pathlib import Path
import requests
from .engine_data import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--llm', action='store_true')
    args = parser.parse_args()
    base = 'http://127.0.0.1:8002'
    health = requests.get(base + '/health', timeout=5).json()
    if not (health['unityConnected'] or health['unityWebConnected']):
        raise SystemExit('Connect a native Unity player or open /engine/?autopilot=1 first.')
    wave = requests.get('http://127.0.0.1:8000/enemy_configs', timeout=5).json()[0]
    agents = [('baseline', ROOT / 'integration/examples/test_agent.py')]
    if args.llm:
        agents.append(('llm', ROOT / 'agent.py'))
    report = {}
    for name, path in agents:
        response = requests.post('http://127.0.0.1:8001/run', timeout=60, json={
            'agentFileContent': base64.b64encode(path.read_bytes()).decode(),
            'enemies': wave['enemies']})
        response.raise_for_status()
        sdk = response.json()
        assert sdk['sdkStatus']['valid'], sdk['sdkStatus']
        response = requests.post(base + '/unity/practice', timeout=5, json={
            'enemies': wave['enemies'], 'defencePlacementData': sdk['defencePlacementData'],
            'timeScale': 20})
        response.raise_for_status()
        key = response.json()['runId']
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            battle = requests.get(base + f'/battles/{key}', timeout=5).json()
            if battle['status'] == 'completed':
                break
            assert battle['status'] not in ('failed', 'callback_failed'), battle
            time.sleep(.5)
        else:
            raise AssertionError(f'Unity practice {key} did not complete')
        result = battle['result']
        assert result['engine'] in ('unity', 'unity-webgl'), result
        assert result['killed'] + result['escaped'] == sum(e['count'] for e in wave['enemies'])
        assert result['defencesPlaced'] == len(sdk['defencePlacementData'])
        frames = requests.get(base + f'/battles/{key}/replay', timeout=5).json()
        assert frames, 'Missing real Unity frames'
        evaluated = requests.post(base + '/evaluate', json={'result': result}, timeout=5)
        evaluated.raise_for_status()
        report[name] = {'sdk_valid': True, 'run_id': key, 'result': result,
                        'score': evaluated.json()['score'], 'replay_frames': len(frames)}
    out = ROOT / 'artifacts/unity-smoke-report.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
