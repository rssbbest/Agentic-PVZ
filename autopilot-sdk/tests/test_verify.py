"""`verify`: a broken agent must fail the dry run, not pass it."""
from autopilot_sdk.checks import verify_agent

GOOD = "def get_reinforcements(state):\n    return [{'defenceId': 1, 'defenceGridPosition': {'x': 1, 'y': 2}}]\n"


def test_a_working_agent_passes(tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text(GOOD)
    report = verify_agent(agent)
    assert report.passed, [(c.name, c.detail) for c in report.checks]


def test_an_agent_that_cannot_import_fails_with_the_reason(tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text("from autopilot_sdk.no_such_module import thing\n" + GOOD)
    report = verify_agent(agent)
    assert not report.passed
    failed = [c for c in report.checks if not c.ok]
    assert failed and "no_such_module" in failed[0].detail


def test_an_agent_that_crashes_fails(tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text("def get_reinforcements(state):\n    return 1 / 0\n")
    report = verify_agent(agent)
    assert not report.passed and any("ZeroDivisionError" in c.detail for c in report.checks if not c.ok)
