"""A broken agent.py is explained plainly: which line, what is wrong."""
import pytest

from autopilot_sdk.checks import verify_agent
from autopilot_sdk.local_proxy import AgentFileError, check_agent_file


def _first_failure(tmp_path, source):
    agent = tmp_path / "agent.py"
    agent.write_text(source)
    failed = [c for c in verify_agent(agent).checks if not c.ok]
    assert failed, "a broken agent must fail verify"
    return failed[0].detail


def test_a_syntax_error_names_the_line_and_shows_it(tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text("def get_reinforcements(state)\n    return []\n")
    with pytest.raises(AgentFileError) as err:
        check_agent_file(agent)
    assert "agent.py line 1: expected ':'" in str(err.value) and "def get_reinforcements(state)" in str(err.value)


def test_a_crash_names_the_agent_line(tmp_path):
    detail = _first_failure(tmp_path, "def helper(state):\n    return state['budgt']\n\n"
                                      "def get_reinforcements(state):\n    helper(state)\n    return []\n")
    assert "agent.py line 2, in helper: KeyError: 'budgt'" in detail and "state['budgt']" in detail


def test_an_import_error_names_the_line(tmp_path):
    detail = _first_failure(tmp_path, "import not_a_module\ndef get_reinforcements(state):\n    return []\n")
    assert "agent.py line 1" in detail and "not_a_module" in detail


def test_wrong_return_types_say_what_to_return(tmp_path):
    detail = _first_failure(tmp_path, "def get_reinforcements(state):\n    return {'defenceId': 1}\n")
    assert "returned dict" in detail and "[] to place nothing" in detail
    detail = _first_failure(tmp_path, "def get_reinforcements(state):\n    return [{'id': 1}]\n")
    assert "can't use" in detail and "defenceGridPosition" in detail
