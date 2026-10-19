"""Double-click on Linux: reopen in a terminal only when there is no terminal."""
import os
import subprocess
import sys

from autopilot_sdk import launcher


def test_piped_input_never_opens_a_terminal(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("AUTOPILOT_IN_TERMINAL", raising=False)
    code = "from autopilot_sdk import launcher; print(launcher.started_without_terminal())"
    piped = subprocess.run([sys.executable, "-c", code], input="", capture_output=True, text=True,
                           env={**os.environ, "DISPLAY": ":0"})
    assert piped.stdout.strip() == "False"


def test_a_file_manager_start_opens_a_terminal_once():
    code = "from autopilot_sdk import launcher; print(launcher.started_without_terminal())"
    env = {**os.environ, "DISPLAY": ":0"}
    env.pop("AUTOPILOT_IN_TERMINAL", None)
    with open(os.devnull) as devnull:
        out = subprocess.run([sys.executable, "-c", code], stdin=devnull, capture_output=True, text=True, env=env)
    assert out.stdout.strip() == ("True" if sys.platform.startswith("linux") else "False")
    with open(os.devnull) as devnull:
        again = subprocess.run([sys.executable, "-c", code], stdin=devnull, capture_output=True, text=True,
                               env={**env, "AUTOPILOT_IN_TERMINAL": "1"})
    assert again.stdout.strip() == "False"


def test_the_first_installed_terminal_is_used(monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which",
                        lambda name: f"/usr/bin/{name}" if name in ("konsole", "xterm") else None)
    assert launcher.terminal_command(["/opt/autopilot"]) == ["/usr/bin/konsole", "-e", "/opt/autopilot"]
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    assert launcher.terminal_command(["/opt/autopilot"]) is None
