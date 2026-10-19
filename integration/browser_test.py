"""Live browser checks against the corrected Unity player and backend services."""
import argparse
import json
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright
from .engine_data import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--submission-id', type=int)
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    errors, unity_logs, claimed, result_urls = [], [], [], []
    report = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless, args=[
            '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
            '--autoplay-policy=no-user-gesture-required'])
        context = browser.new_context(viewport={'width': 1400, 'height': 950},
            record_video_dir=str(ROOT / 'artifacts/final-browser-video'))
        control = context.new_page()
        control.on('pageerror', lambda e: errors.append(str(e)))
        suffix = f'?submission={args.submission_id}' if args.submission_id else ''
        control.goto('http://127.0.0.1:8002/' + suffix)
        control.wait_for_function('placement.length > 0')
        if args.submission_id:
            control.wait_for_selector('#runs button')
        engine = context.new_page()
        engine.on('pageerror', lambda e: errors.append(str(e)))
        engine.on('console', lambda msg: unity_logs.append({'type': msg.type, 'text': msg.text}))
        def response(r):
            if '/unity/jobs/next' in r.url and r.status == 200:
                try:
                    job = r.json()
                    if job['runId']:
                        claimed.append(job['runId'])
                        print('Browser Unity claimed run:', job['runId'], flush=True)
                except Exception:
                    pass
            elif '/result' in r.url and '/unity/jobs/' in r.url:
                result_urls.append({'url': r.url, 'status': r.status})
                print('Browser Unity result:', r.status, flush=True)
        engine.on('response', response)
        engine.goto('http://127.0.0.1:8002/engine/?autopilot=1', wait_until='domcontentloaded')
        engine.wait_for_function("document.querySelector('#unity-loading-bar').style.display === 'none'", timeout=120000)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if requests.get('http://127.0.0.1:8002/health').json()['unityWebConnected']:
                break
            engine.wait_for_timeout(500)
        else:
            raise AssertionError('WebGL player did not connect to the Unity queue')
        report['webgl_connected'] = True
        # Use the actual product control to play a formation in browser Unity.
        control.bring_to_front()
        control.locator('#unity-play').click()
        control.wait_for_function('lastBattle !== null', timeout=10000)
        key = control.evaluate('lastBattle')
        print('Unity practice:', key, flush=True)
        engine.bring_to_front()
        deadline = time.monotonic() + 180
        took_screenshot = False
        while time.monotonic() < deadline:
            engine.wait_for_timeout(500)
            battle = requests.get(f'http://127.0.0.1:8002/battles/{key}', timeout=5).json()
            if battle.get('snapshot', {}).get('enemies') and not took_screenshot:
                engine.screenshot(path=str(ROOT / 'artifacts/final-unity-webgl-combat.png'))
                took_screenshot = True
            if battle['status'] == 'completed':
                break
        else:
            raise AssertionError('Unity practice did not complete')
        assert battle['result']['engine'] == 'unity-webgl', battle
        result = battle['result']
        assert result['killed'] + result['escaped'] == result['totalEnemies']
        report['unity_practice'] = {'run_id': key, 'result': result}
        frames = requests.get(f'http://127.0.0.1:8002/battles/{key}/replay').json()
        assert len(frames) > 5
        report['unity_replay_frames'] = len(frames)
        control.bring_to_front()
        control.wait_for_function("document.querySelector('#battle-message').textContent.includes('Battle complete')", timeout=10000)
        control.locator('#speed').select_option('5')
        control.locator('#replay').click()
        control.wait_for_function("document.querySelector('#battle-message').textContent.includes('Replay complete')", timeout=45000)
        report['control_replay'] = 'passed'
        control.screenshot(path=str(ROOT / 'artifacts/final-browser-control.png'), full_page=True)
        assert not errors, errors
        game_errors = [e for e in unity_logs if any(s in e['text'] for s in (
            'NullReferenceException', 'MissingReferenceException', 'SendMessage: object',
            'IndexOutOfRangeException', 'RuntimeError:', 'An error occurred running'))]
        assert not game_errors, game_errors
        report['page_errors'] = errors
        report['game_errors'] = game_errors
        report['claimed_runs'] = claimed
        report['result_callbacks'] = result_urls
        (ROOT / 'artifacts/final-browser-report.json').write_text(json.dumps(report, indent=2))
        (ROOT / 'artifacts/final-unity-webgl-console.json').write_text(json.dumps(unity_logs, indent=2))
        print(json.dumps(report, indent=2), flush=True)
        context.close()
        browser.close()


if __name__ == '__main__':
    main()
