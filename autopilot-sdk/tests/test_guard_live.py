"""Live HTTP-layer guard tests — prove the socket patch stops real
client libraries before any packet is sent (doc §10.3).

All blocked-host tests assert BEFORE-network failure: if the guard
regresses, these would attempt real DNS/TLS, so timeouts are kept
short and uninstall is guaranteed via fixture.
"""
import http.client
import socket
import textwrap
import urllib.request

import pytest
import requests

from autopilot_sdk.cli import _demo_state
from autopilot_sdk.network_guard.guard import (
    BlockedNetworkCallError,
    _check_host,
    install_network_guard,
    uninstall_network_guard,
)
from autopilot_sdk.runtime.orchestrator import TurnOrchestrator


@pytest.fixture
def guard():
    install_network_guard()
    try:
        yield
    finally:
        uninstall_network_guard()


def test_requests_blocked_openai(guard):
    with pytest.raises(BlockedNetworkCallError, match="api.openai.com"):
        requests.post("https://api.openai.com/v1/chat/completions", timeout=3)


def test_requests_blocked_anthropic(guard):
    with pytest.raises(BlockedNetworkCallError, match="api.anthropic.com"):
        requests.post("https://api.anthropic.com/v1/messages", timeout=3)


def test_requests_blocked_aggregator(guard):
    with pytest.raises(BlockedNetworkCallError, match="api.groq.com"):
        requests.post("https://api.groq.com/openai/v1/chat/completions", timeout=3)


def test_urllib_blocked(guard):
    with pytest.raises(BlockedNetworkCallError, match="api.anthropic.com"):
        urllib.request.urlopen("https://api.anthropic.com/v1/x", timeout=3)


def test_http_client_blocked(guard):
    with pytest.raises(BlockedNetworkCallError, match="api.groq.com"):
        conn = http.client.HTTPSConnection("api.groq.com", timeout=3)
        conn.request("GET", "/")


def test_httpx_blocked(guard):
    httpx = pytest.importorskip("httpx", reason="httpx not installed")
    with pytest.raises(BlockedNetworkCallError, match="api.openai.com"):
        httpx.post("https://api.openai.com/v1/x", timeout=3)


def test_getaddrinfo_blocked_before_dns(guard):
    # Must raise before any real DNS lookup happens.
    with pytest.raises(BlockedNetworkCallError, match="api.openai.com"):
        socket.getaddrinfo("api.openai.com", 443)


@pytest.mark.parametrize(
    "host",
    [
        "us.api.openai.com",  # subdomain of blocked suffix
        "API.OPENAI.COM",  # case-insensitive
        "api.openai.com.",  # trailing dot
        "foo.openai.azure.com",
        "x.bedrock-runtime.amazonaws.com",  # any region
        "api-inference.huggingface.co",
    ],
)
def test_evasion_variants_blocked(host):
    with pytest.raises(BlockedNetworkCallError):
        _check_host(host, 443)


@pytest.mark.parametrize(
    "host,port",
    [
        ("api.autopilot.gdg.dev", 443),  # allowlisted backend
        ("pypi.org", 443),  # open internet stays usable
        ("openai.com.evil.com", 443),  # looks similar, not a subdomain
        ("127.0.0.1", 9999),  # loopback but not a known LLM port
    ],
)
def test_allowed_hosts_pass(host, port):
    _check_host(host, port)  # must not raise


def test_worker_blocks_requests_to_openai(tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text(
        textwrap.dedent(
            """
            from autopilot_sdk import Agent
            import requests
            class MyAgent(Agent):
                def decide(self, state):
                    requests.post(
                        "https://api.openai.com/v1/chat/completions",
                        timeout=3,
                    )
            """
        )
    )
    orch = TurnOrchestrator(str(agent), default_timeout_s=10)
    decision = orch.run_turn(_demo_state(1))
    assert "Blocked" in str(decision.metadata.get("error", ""))
    assert "api.openai.com" in str(decision.metadata.get("error", ""))
