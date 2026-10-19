"""Read the root .env without exporting provider credentials to all processes."""
import os
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    'AUTOPILOT_LLM_BASE_URL': 'https://api.openai.com/v1',
    'AUTOPILOT_LLM_API_KEY': '', 'AUTOPILOT_LLM_MODEL': 'gpt-4o-mini',
    'POSTGRES_PASSWORD': '', 'SIMULATOR_SECRET': '', 'ADMIN_SECRET': '',
    'POSTGRES_CONTAINER': 'autopilot-unified-db',
    'POSTGRES_VOLUME': 'autopilot-unified-pgdata', 'POSTGRES_PORT': '54339',
    'MAX_CONCURRENT_RUNS': '4', 'UNITY_EDITOR_PATH': '',
    'UNITY_PLAYER_PATH': '', 'UNITY_COMPAT_LIBS': '',
}


def settings(path=None):
    values = dotenv_values(path or ROOT / '.env')
    return {key: os.environ.get(key, values.get(key) or default)
            for key, default in DEFAULTS.items()}


def service_environment(name, cfg, mode, values=None):
    values = values or settings()
    env = dict(os.environ)
    for key in ('AUTOPILOT_LLM_API_KEY', 'AUTOPILOT_LLM_BASE_URL',
                'AUTOPILOT_LLM_MODEL', 'OPENAI_API_KEY',
                'ANTHROPIC_API_KEY',
                'POSTGRES_PASSWORD', 'SIMULATOR_SECRET', 'ADMIN_SECRET', 'DATABASE_URL'):
        env.pop(key, None)
    env.update(
        SDK_URL='http://127.0.0.1:8001', SIMULATOR_URL='http://127.0.0.1:8002',
        EVALUATOR_URL='http://127.0.0.1:8002', AUTOPILOT_BACKEND_URL='http://127.0.0.1:8000',
        UPLOAD_DIR=str(ROOT / '.runtime/uploads'), SIMULATOR_MODE=mode,
        AUTOPILOT_GATEWAY_URL='http://127.0.0.1:8004/v1',
        AUTOPILOT_LLM_MODEL=values['AUTOPILOT_LLM_MODEL'],
        PYTHONPATH=str(ROOT), MAX_CONCURRENT_RUNS=values['MAX_CONCURRENT_RUNS'],
        SIMULATION_TIMEOUT_S='1800' if mode == 'unity' else '300',
        UNITY_GATEWAY_URL='http://127.0.0.1:8002')
    if name in ('backend', 'simulator'):
        env['SIMULATOR_SECRET'] = cfg['SIMULATOR_SECRET']
    if name == 'backend':
        from urllib.parse import quote
        env['ADMIN_SECRET'] = cfg['ADMIN_SECRET']
        env['DATABASE_URL'] = (f'postgresql+asyncpg://autopilot:'
            f'{quote(cfg["POSTGRES_PASSWORD"], safe="")}@127.0.0.1:'
            f'{values["POSTGRES_PORT"]}/autopilot')
    if name == 'gateway':
        env['AUTOPILOT_LLM_BASE_URL'] = values['AUTOPILOT_LLM_BASE_URL']
        env['AUTOPILOT_LLM_API_KEY'] = values['AUTOPILOT_LLM_API_KEY']
        env['AUTOPILOT_LLM_MODEL'] = values['AUTOPILOT_LLM_MODEL']
    return env
