"""Offline-core tests — all runnable without backend / gateway."""
import socket
from pathlib import Path
import textwrap

from autopilot_sdk.cli import _demo_state
from autopilot_sdk.contracts.schemas import (
    MatchBootstrap,
    MatchResult,
    ResourceState,
)
from autopilot_sdk.network_guard.guard import (
    BlockedNetworkCallError,
    install_network_guard,
    uninstall_network_guard,
    _check_host,
)
from autopilot_sdk.runtime.loader import load_agent_class
from autopilot_sdk.runtime.orchestrator import TurnOrchestrator
from autopilot_sdk.verifier.static_scan import scan_source


def test_clean_source_passes():
    r = scan_source("import requests\nx = 1\n")
    assert r.passed and not r.violations


def test_banned_import_fails():
    r = scan_source("import anthropic\n")
    assert not r.passed
    assert any("anthropic" in v for v in r.violations)


def test_banned_url_literal_fails():
    r = scan_source('requests.post("https://api.groq.com/openai/v1")')
    assert not r.passed


def test_openai_import_is_warn_only_per_s10_4():
    # §10.4: OpenAI-wire-compatible client pointed at the organisers' gateway is legal —
    # the host check, not the import, is authoritative.
    r = scan_source("import openai\n")
    assert r.passed
    assert r.warnings


def test_obfuscation_flagged_as_warning():
    r = scan_source("import base64\nbase64.b64decode(x)")
    assert r.passed and r.warnings


def test_guard_blocks_known_provider():
    install_network_guard()
    try:
        try:
            socket.create_connection(("api.openai.com", 443), timeout=2)
            raise AssertionError("should have been blocked")
        except BlockedNetworkCallError:
            pass
    finally:
        uninstall_network_guard()


def test_guard_allows_open_internet():
    _check_host("pypi.org", 443)  # must not raise
    _check_host("api.autopilot.gdg.dev", 443)  # allowlisted


def test_guard_blocks_local_llm_port():
    install_network_guard()
    try:
        try:
            socket.create_connection(("127.0.0.1", 11434), timeout=1)
            raise AssertionError("should have been blocked")
        except BlockedNetworkCallError:
            pass
    finally:
        uninstall_network_guard()


def test_loader_finds_sample_agent():
    cls = load_agent_class(str(Path(__file__).with_name("sample_agent.py")))
    assert cls.__name__ == "MyAgent"


def test_turn_memory_persists(tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text(
        textwrap.dedent(
            """
            from autopilot_sdk import Agent
            from autopilot_sdk.contracts.schemas import TurnDecision
            class MyAgent(Agent):
                def decide(self, state):
                    self.count = getattr(self, "count", 0) + 1
                    return TurnDecision(actions=[], metadata={"count": self.count})
            """
        )
    )
    orch = TurnOrchestrator(str(agent), default_timeout_s=10)
    d1 = orch.run_turn(_demo_state(1))
    d2 = orch.run_turn(_demo_state(2))
    assert d2.metadata["count"] == 2


def test_turn_timeout_returns_noop(tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text(
        "from autopilot_sdk import Agent\n"
        "import time\n"
        "class MyAgent(Agent):\n"
        "    def decide(self, state):\n"
        "        time.sleep(10)\n"
    )
    orch = TurnOrchestrator(str(agent), default_timeout_s=10)
    state = _demo_state(1)
    state.time_limit_seconds = 1.0
    d = orch.run_turn(state)
    assert d.metadata.get("timeout") is True
    assert d.actions == []


def test_agent_exception_does_not_hang_match(tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text(
        "from autopilot_sdk import Agent\n"
        "class MyAgent(Agent):\n"
        "    def decide(self, state):\n"
        "        raise ValueError('boom')\n"
    )
    orch = TurnOrchestrator(str(agent), default_timeout_s=10)
    d = orch.run_turn(_demo_state(1))
    assert "error" in d.metadata


def test_guard_active_inside_worker(tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text(
        textwrap.dedent(
            """
            from autopilot_sdk import Agent
            import socket
            class MyAgent(Agent):
                def decide(self, state):
                    socket.create_connection(("api.anthropic.com", 443), timeout=2)
            """
        )
    )
    orch = TurnOrchestrator(str(agent), default_timeout_s=10)
    d = orch.run_turn(_demo_state(1))
    assert "Blocked" in str(d.metadata.get("error", ""))


def test_match_start_end_lifecycle(tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text(
        textwrap.dedent(
            """
            from autopilot_sdk import Agent
            class MyAgent(Agent):
                def on_match_start(self, info):
                    self.seen = info.match_id
                def decide(self, state):
                    from autopilot_sdk.contracts.schemas import TurnDecision
                    return TurnDecision(actions=[])
                def on_match_end(self, result):
                    self.done = True
            """
        )
    )
    orch = TurnOrchestrator(str(agent), default_timeout_s=10)
    mb = MatchBootstrap(
        match_id="m1",
        seed=1,
        map=_demo_state().map,
        max_turns=10,
        time_limit_seconds=5,
        starting_resources=ResourceState(currency=1, energy=1),
    )
    assert orch.run_match_start(mb) is True
    assert orch.has_memory
    assert (
        orch.run_match_end(
            MatchResult(match_id="m1", outcome="won", final_score=1.0, turns_survived=2)
        )
        is True
    )
