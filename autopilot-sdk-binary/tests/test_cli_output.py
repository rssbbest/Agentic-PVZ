"""CLI output must survive non-UTF-8 redirected streams (Windows cp1252)."""
import os
import subprocess
import sys

AGENT_SOURCE = "def get_reinforcements(state):\n    return []\n"


def test_verify_output_survives_cp1252_pipe(tmp_path):
    AGENT = tmp_path / "agent.py"
    AGENT.write_text(AGENT_SOURCE)
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    result = subprocess.run(
        [sys.executable, "-m", "autopilot_sdk", "verify", "--agent", str(AGENT)],
        capture_output=True, env=env, timeout=60,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert "✓".encode() in result.stdout
