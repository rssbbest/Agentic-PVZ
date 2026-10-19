"""Exercise the supplied Unity WebGL player through its actual canvas controls."""
import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
from .engine_data import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--team-id', type=int, required=True)
    args = parser.parse_args()
    logs, exchanges = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=[
            '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
            '--autoplay-policy=no-user-gesture-required'])
        context = browser.new_context(viewport={'width': 1400, 'height': 850},
            record_video_dir=str(ROOT / 'artifacts/original-video'))
        page = context.new_page()
        page.on('console', lambda msg: logs.append({'type': msg.type, 'text': msg.text}))
        page.on('pageerror', lambda exc: logs.append({'type': 'pageerror', 'text': str(exc)}))
        def response(r):
            if '/api/team' in r.url or '/api/results' in r.url:
                try:
                    entry = {'url': r.url, 'status': r.status,
                             'request': r.request.post_data, 'response': r.json()}
                    exchanges.append(entry)
                    print(json.dumps(entry), flush=True)
                except Exception as exc:
                    print(type(exc).__name__, flush=True)
        page.on('response', response)
        page.goto('http://127.0.0.1:8002/original/', wait_until='domcontentloaded')
        page.wait_for_function('Boolean(window.autopilotUnity)', timeout=90000)
        page.wait_for_timeout(2000)
        # Invoke the same public Unity receivers used by the menu's controls.
        # The CRT effect warps their visible position away from the UI hit boxes.
        page.evaluate('''team => {
            const u = window.autopilotUnity;
            u.SendMessage('Controller', 'ChangeTeamId', team);
            u.SendMessage('Controller', 'DEBUG_TEAM_DATA_URL', 'http://127.0.0.1:8002/api/team');
            u.SendMessage('Controller', 'DEBUG_RESULTS_URL', 'http://127.0.0.1:8002/api/results');
            u.SendMessage('Controller', 'Submit');
        }''', str(args.team_id).zfill(4))
        started = time.monotonic()
        while time.monotonic() - started < 110:
            page.wait_for_timeout(500)
            if any('/api/team' in e['url'] and e['status'] == 200 for e in exchanges):
                if not (ROOT / 'artifacts/build4-original-combat.png').exists():
                    page.wait_for_timeout(2500)
                    page.screenshot(path=str(ROOT / 'artifacts/build4-original-combat.png'))
            if any('/api/results' in e['url'] for e in exchanges):
                break
        page.screenshot(path=str(ROOT / 'artifacts/build4-original-final.png'))
        (ROOT / 'artifacts/build4-original-exchanges.json').write_text(json.dumps(exchanges, indent=2))
        (ROOT / 'artifacts/build4-original-console.json').write_text(json.dumps(logs, indent=2))
        print('Original player exchanges:', len(exchanges), flush=True)
        context.close()
        browser.close()


if __name__ == '__main__':
    main()
