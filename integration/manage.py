"""Start/stop the complete local stack without exposing credentials in argv."""
import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
import urllib.request
from .settings import settings, service_environment

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
PYTHON = ROOT / '.venv/bin/python'
SERVICES = RUNTIME / 'services.json'
CONFIG = RUNTIME / 'config.json'


def config():
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    RUNTIME.chmod(0o700)
    values = settings()
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    for key in ('SIMULATOR_SECRET', 'ADMIN_SECRET', 'POSTGRES_PASSWORD'):
        cfg[key] = values[key] or cfg.get(key) or secrets.token_urlsafe(24)
    CONFIG.write_text(json.dumps(cfg))
    CONFIG.chmod(0o600)
    return cfg


def healthy(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def wait_for_exit(pid, marker):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            command = Path(f'/proc/{pid}/cmdline').read_bytes()
        except OSError:
            return
        if marker.encode() not in command:
            return
        time.sleep(.05)


def stop():
    player_file = RUNTIME / 'unity-players.json'
    if player_file.exists():
        for item in json.loads(player_file.read_text()):
            try:
                command = Path(f'/proc/{item["pid"]}/cmdline').read_bytes()
                if item['path'].encode() in command:
                    os.kill(item['pid'], 15)
                    wait_for_exit(item['pid'], item['path'])
                    print('Stopped Unity player')
            except (OSError, ProcessLookupError):
                pass
        player_file.unlink()
    if SERVICES.exists():
        for name, item in json.loads(SERVICES.read_text()).items():
            try:
                # Guard against a stale PID accidentally targeting an unrelated process.
                command = Path(f'/proc/{item["pid"]}/cmdline').read_bytes()
                if item['marker'].encode() in command:
                    os.kill(item['pid'], 15)
                    wait_for_exit(item['pid'], item['marker'])
                    print(f'Stopped {name}')
            except (OSError, ProcessLookupError):
                pass
        SERVICES.unlink()


def start_player():
    from .unity import PLAYER, compatibility_environment
    player_file = RUNTIME / 'unity-players.json'
    existing = json.loads(player_file.read_text()) if player_file.exists() else []
    for item in existing:
        try:
            if item['path'].encode() in Path(f'/proc/{item["pid"]}/cmdline').read_bytes():
                return
        except OSError:
            pass
    if not PLAYER.exists():
        print('Build the Unity player: python -m integration.unity build')
        return
    env = service_environment('player', config(), 'unity')
    env['UNITY_TIME_SCALE'] = '20'
    env = compatibility_environment(env)
    log = RUNTIME / 'unity-player.log'
    proc = subprocess.Popen([str(PLAYER), '--autopilotGateway', env['UNITY_GATEWAY_URL'],
        '-screen-fullscreen', '0', '-screen-width', '1280', '-screen-height', '720',
        '-logFile', str(log)], cwd=PLAYER.parent, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    player_file.write_text(json.dumps([{'pid': proc.pid, 'path': str(PLAYER), 'log': str(log)}]))
    print(f'Unity player started (PID {proc.pid})')


def start(mode):
    cfg = config()
    values = settings()
    if not PYTHON.exists():
        raise RuntimeError('Create .venv and install requirements.txt first')
    existing = json.loads(SERVICES.read_text()) if SERVICES.exists() else {}
    if existing and all(healthy(item['port']) for item in existing.values()):
        if mode == 'unity':
            start_player()
        print('Stack already running. Use stop first to change simulator mode.')
        return
    stop()
    for port in (8000, 8001, 8002, 8004):
        import socket
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('127.0.0.1', port))
            except OSError:
                raise RuntimeError(f'Port {port} is already in use; stop the other stack first')
    container = values['POSTGRES_CONTAINER']
    probe = subprocess.run(['docker', 'container', 'inspect', container],
        capture_output=True)
    if probe.returncode:
        db_env = RUNTIME / 'db.env'
        db_env.write_text('POSTGRES_USER=autopilot\nPOSTGRES_DB=autopilot\nPOSTGRES_PASSWORD=' + cfg['POSTGRES_PASSWORD'] + '\n')
        db_env.chmod(0o600)
        subprocess.run(['docker', 'run', '-d', '--name', container,
            '--env-file', str(db_env), '-p', f'127.0.0.1:{values["POSTGRES_PORT"]}:5432',
            '-v', values['POSTGRES_VOLUME'] + ':/var/lib/postgresql/data', 'postgres:16-alpine'], check=True, stdout=subprocess.DEVNULL)
    else:
        subprocess.run(['docker', 'start', container], check=True, stdout=subprocess.DEVNULL)
    for _ in range(30):
        result = subprocess.run(['docker', 'exec', container, 'pg_isready', '-U', 'autopilot'], capture_output=True)
        if result.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError('Postgres did not become ready')
    entries = [
        ('gateway', 8004, ROOT, ['-m', 'uvicorn', 'integration.gateway:app', '--host', '127.0.0.1', '--port', '8004'], 'integration.gateway:app'),
        ('simulator', 8002, ROOT, ['-m', 'uvicorn', 'integration.service:app', '--host', '127.0.0.1', '--port', '8002'], 'integration.service:app'),
        ('sdk', 8001, ROOT, ['-m', 'autopilot_sdk', 'serve', '--host', '127.0.0.1', '--port', '8001'], 'autopilot_sdk'),
        ('backend', 8000, ROOT / 'autopilot-backend', ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000'], 'app.main:app'),
    ]
    running = {}
    for name, port, cwd, args, marker in entries:
        if healthy(port):
            raise RuntimeError(f'Port {port} already belongs to another service')
        env = service_environment(name, cfg, mode, values)
        log = (RUNTIME / f'{name}.log').open('ab')
        proc = subprocess.Popen([str(PYTHON), *args], cwd=cwd, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        log.close()
        running[name] = {'pid': proc.pid, 'port': port, 'marker': marker}
        SERVICES.write_text(json.dumps(running))
        for _ in range(40):
            if healthy(port):
                print(f'{name}: http://127.0.0.1:{port}')
                break
            if proc.poll() is not None:
                raise RuntimeError(f'{name} exited; inspect .runtime/{name}.log')
            time.sleep(.5)
        else:
            raise RuntimeError(f'{name} failed health check; inspect its log')
    print('Play: http://localhost:8002')
    if mode == 'unity':
        start_player()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['start', 'stop', 'status'])
    parser.add_argument('--mode', choices=['portable', 'unity'], default='unity')
    args = parser.parse_args()
    if args.command == 'start': start(args.mode)
    elif args.command == 'stop': stop()
    else:
        for name, port in [('backend', 8000), ('sdk', 8001), ('simulator', 8002), ('gateway', 8004)]:
            print(f'{name}: {"healthy" if healthy(port) else "down"}')


if __name__ == '__main__':
    main()
