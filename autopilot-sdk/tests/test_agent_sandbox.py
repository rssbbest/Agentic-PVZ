"""The live reinforcement sandbox: every bypass ends the run as 'blocked'."""
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from autopilot_sdk.runtime.reinforcements import run_reinforcements
from test_reinforcements import SNAPSHOT, _source

PLACE = 'return [{"defenceId": 1, "defenceGridPosition": {"x": 0, "y": 0}}]'


def _run(body: str, caplog) -> tuple[list, str]:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="autopilot.reinforcements"):
        result = run_reinforcements(_source(body), SNAPSHOT, timeout_s=5)
    return result, caplog.text


@pytest.mark.parametrize("body, reason", [
    ('import socket\nsocket.create_connection(("api." + "openai.com", 443), timeout=2)',
     "Blocked outbound call to 'api.openai.com'"),
    ('import socket\nsocket.create_connection(("127.0.0.1", 11434), timeout=2)',
     "local LLM server at 127.0.0.1:11434"),
    ('import socket\nsocket.create_connection(("0.0.0.0", 11434), timeout=2)',
     "local LLM server at 0.0.0.0:11434"),
    ('import socket\nsocket.getaddrinfo("llm-relay.example.com", 443)',
     "Blocked connection to 'llm-relay.example.com'"),
    ('import _socket\ns = _socket.socket()\ns.settimeout(1)\ns.connect(("203.0.113.10", 443))',
     "Blocked connection to 203.0.113.10:443"),
    ('import socket\ns = socket.socket(socket.AF_INET6)\ns.settimeout(1)\n'
     's.connect(("::ffff:203.0.113.10", 443))',
     "Blocked connection to ::ffff:203.0.113.10:443"),
    ('import socket\nsocket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("203.0.113.10", 53))',
     "Blocked connection to 203.0.113.10:53"),
    ('import subprocess\nsubprocess.run(["true"])', "Blocked subprocess.Popen"),
    ('import os\nos.system("true")', "Blocked os.system"),
    pytest.param('import os\nif os.fork() == 0:\n    os._exit(0)', "Blocked os.fork",
                 marks=pytest.mark.skipif(not hasattr(os, "fork"), reason="no fork on Windows")),
    ('import ctypes, os\nctypes.CDLL("ws2_32" if os.name == "nt" else None).socket(2, 1, 0)',
     "Blocked ctypes.dlsym"),
    ('import gc\ngc.get_objects()', "Blocked gc.get_objects"),
    ('import socket, _socket\nfrom autopilot_sdk.network_guard import uninstall_network_guard\n'
     'uninstall_network_guard()\nsocket.getaddrinfo = _socket.getaddrinfo\n'
     'socket.create_connection(("api." + "anthropic.com", 443), timeout=2)',
     "Blocked outbound call to 'api.anthropic.com'"),
])
def test_bypass_attempts_are_blocked(body, reason, caplog):
    result, log = _run(body + "\n" + PLACE, caplog)
    assert result == []
    assert "agent blocked" in log and reason in log


def test_caught_violation_is_still_reported(caplog):
    result, log = _run(
        'import socket\ntry:\n    socket.create_connection(("api." + "openai.com", 443), timeout=2)\n'
        'except Exception:\n    pass\n' + PLACE, caplog)
    assert result == [] and "agent blocked" in log


def test_provider_sdk_import_is_blocked_however_written(tmp_path, monkeypatch, caplog):
    (tmp_path / "anthropic").mkdir()
    (tmp_path / "anthropic" / "__init__.py").write_text("")
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])  # spawned worker inherits it
    result, log = _run('import importlib\nimportlib.import_module("anth" + "ropic")\n' + PLACE, caplog)
    assert result == [] and "Blocked import of LLM provider SDK 'anthropic'" in log


class _Gateway(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


def test_loopback_gateway_and_plain_code_are_allowed(monkeypatch, caplog):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Gateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        monkeypatch.setenv("AUTOPILOT_GATEWAY_URL", url + "v1")  # a gateway on this machine
        result, log = _run(
            f'import ctypes, json, requests\nassert requests.get({json.dumps(url)}, timeout=2).ok\n'
            + PLACE, caplog)
    finally:
        server.shutdown()
        server.server_close()
    assert result == [{"defenceId": 1, "defenceGridPosition": {"x": 0, "y": 0}}], log


def test_sdk_llm_relay_port_is_the_only_local_llm_port_allowed(monkeypatch, caplog):
    from autopilot_sdk.network_guard.domains import LOCAL_LLM_PORTS

    server = None
    for port in sorted(LOCAL_LLM_PORTS):  # the relay sits on the SDK's port, e.g. 5000
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), _Gateway)
            break
        except OSError:
            continue
    if server is None:
        pytest.skip("every local-LLM port is busy")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    body = (f'import requests\nassert requests.get("http://127.0.0.1:{port}/", timeout=2).ok\n'
            + PLACE)
    try:
        monkeypatch.delenv("AUTOPILOT_SDK_RELAY_PORT", raising=False)
        result, log = _run(body, caplog)
        assert result == [] and f"local LLM server at 127.0.0.1:{port}" in log
        monkeypatch.setenv("AUTOPILOT_SDK_RELAY_PORT", str(port))
        result, log = _run(body, caplog)
        assert result == [{"defenceId": 1, "defenceGridPosition": {"x": 0, "y": 0}}], log
    finally:
        server.shutdown()
        server.server_close()


def test_other_programs_on_this_machine_are_unreachable(monkeypatch, caplog):
    """Only the gateway: a frozen agent.py must not ask a local program what to play."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Gateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        monkeypatch.delenv("AUTOPILOT_GATEWAY_URL", raising=False)
        monkeypatch.delenv("AUTOPILOT_SDK_RELAY_PORT", raising=False)
        result, log = _run(f'import socket\nsocket.create_connection(("127.0.0.1", {port}), timeout=2)\n' + PLACE,
                           caplog)
        assert result == [] and f"Blocked connection to 127.0.0.1:{port}" in log, log
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(not hasattr(__import__("socket"), "AF_UNIX"), reason="no AF_UNIX")
def test_local_unix_sockets_are_blocked(tmp_path, caplog):
    body = (f'import socket\ns = socket.socket(socket.AF_UNIX)\ns.connect({str(tmp_path / "ctl.sock")!r})\n' + PLACE)
    result, log = _run(body, caplog)
    assert result == [] and "local (AF_UNIX) socket" in log, log


def _run_frozen(body: str, caplog) -> tuple[list, str]:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="autopilot.reinforcements"):
        result = run_reinforcements(_source(body), SNAPSHOT, timeout_s=10, frozen=True)
    return result, caplog.text


def test_a_frozen_agent_still_runs_plain_code_and_the_standard_library(caplog):
    body = "import statistics, fractions, json, random\nassert statistics.mean([1, 3]) == 2\n" + PLACE
    result, log = _run_frozen(body, caplog)
    assert result == [{"defenceId": 1, "defenceGridPosition": {"x": 0, "y": 0}}], log


@pytest.mark.parametrize("make, body, reason", [
    (lambda d: (d / "plan.json").write_text("[]"), "open({p!r} + '/plan.json').read()", "plan.json"),
    (lambda d: None, "import os\nos.listdir({p!r})", "Blocked os.listdir of"),
    (lambda d: (d / "helper_mod_x.py").write_text("PLAN = []"),
     "import sys\nsys.path.insert(0, {p!r})\nimport helper_mod_x", "Blocked"),  # the folder listing or the file
])
def test_a_frozen_agent_reads_nothing_the_participant_can_edit(tmp_path, caplog, make, body, reason):
    make(tmp_path)
    code = body.format(p=str(tmp_path)) + "\n" + PLACE
    result, log = _run_frozen(code, caplog)
    assert result == [] and reason in log and str(tmp_path) in log, log
    if "listdir" not in body:  # the same code runs fine in PLAY, where agent.py is live anyway
        assert _run(code, caplog)[0] == [{"defenceId": 1, "defenceGridPosition": {"x": 0, "y": 0}}]
