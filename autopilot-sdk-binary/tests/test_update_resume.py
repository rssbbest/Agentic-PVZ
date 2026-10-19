"""An update restart carries the session over: login, agent.py, what was running."""
import json
import os

from autopilot_sdk import interactive, updater


def test_the_restart_gets_the_login_and_the_running_choice(monkeypatch):
    seen = {}

    def fake_update(backend, say=print, force=False):
        seen.update(json.loads(os.environ[interactive.RESUME_ENV]))  # what the new process will read
        return False

    monkeypatch.setattr(updater, "update_if_needed", fake_update)
    auth = {"token": "tok-7", "teamID": "0007", "teamName": "Seven"}
    interactive.update_sdk({"backend": "https://host/backend", "agent": "/a/agent.py"}, auth, "2")
    assert seen == {"auth": auth, "action": "2", "agent": "/a/agent.py"}
    assert interactive.RESUME_ENV not in os.environ          # no restart: nothing left behind


def test_resume_is_read_once(monkeypatch):
    monkeypatch.setenv(interactive.RESUME_ENV, json.dumps({"action": "1", "auth": {"token": "t"}}))
    assert interactive.take_resume()["action"] == "1"
    assert interactive.take_resume() == {}
    monkeypatch.setenv(interactive.RESUME_ENV, "not json")
    assert interactive.take_resume() == {}


def test_without_a_login_nothing_is_carried(monkeypatch):
    monkeypatch.setattr(updater, "update_if_needed", lambda *a, **k: not os.environ.get(interactive.RESUME_ENV))
    interactive.update_sdk({"backend": "https://host/backend"})
    assert interactive.RESUME_ENV not in os.environ
