"""Build and run the real engine using the editor configured in root .env."""
import argparse
import subprocess
from pathlib import Path
from .engine_data import ROOT, ENGINE
from .settings import settings, service_environment

VALUES = settings()
EDITOR = Path(VALUES['UNITY_EDITOR_PATH'] or
              Path.home() / '.local/opt/unity/6000.1.17f1/Editor/Unity').expanduser()
PROJECT = ENGINE
PLAYER = Path(VALUES['UNITY_PLAYER_PATH'] or
              PROJECT / 'Builds/Linux/Autopilot.x86_64').expanduser()


def compatibility_environment(env):
    env = dict(env)
    compat = VALUES['UNITY_COMPAT_LIBS']
    if compat:
        env['LD_LIBRARY_PATH'] = str(Path(compat).expanduser()) + ':' + env.get('LD_LIBRARY_PATH', '')
    return env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['build', 'run', 'editor'])
    parser.add_argument('--speed', type=int, default=8)
    parser.add_argument('--target', choices=['linux', 'webgl'], default='linux')
    args = parser.parse_args()
    runtime = ROOT / '.runtime'
    runtime.mkdir(mode=0o700, exist_ok=True)
    env = compatibility_environment(service_environment('player', {}, 'unity'))
    if args.command in ('build', 'editor') and not EDITOR.is_file():
        raise SystemExit('Set UNITY_EDITOR_PATH in .env to the Unity 6000.1.17f1 editor executable.')
    if args.command == 'build':
        output = PLAYER if args.target == 'linux' else runtime / 'managed-webgl'
        env['AUTOPILOT_UNITY_BUILD'] = str(output)
        env.setdefault('IL2CPP_MAX_THREADS', '4')
        raise SystemExit(subprocess.call([str(EDITOR), '-batchmode', '-nographics', '-quit',
            '-projectPath', str(PROJECT), '-executeMethod',
            'AutopilotBuild.Linux' if args.target == 'linux' else 'AutopilotBuild.Web',
            '-logFile', str(runtime / f'unity-{args.target}-build.log')], env=env))
    env['UNITY_TIME_SCALE'] = str(args.speed)
    if args.command == 'editor':
        command = [str(EDITOR), '-projectPath', str(PROJECT)]
    else:
        if not PLAYER.is_file():
            raise SystemExit('Build the player first: python -m integration.unity build')
        command = [str(PLAYER), '--autopilotGateway', env['UNITY_GATEWAY_URL'],
                   '-logFile', str(runtime / 'unity-player.log')]
    raise SystemExit(subprocess.call(command, env=env))


if __name__ == '__main__':
    main()
