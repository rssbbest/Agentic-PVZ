"""Bundled config — NOT participant-editable (doc §4, §7).

Backend URL defaults to a local autopilot-backend (`uvicorn` on :8010;
:8000 is taken by the game's build7/server.py);
organisers override it with AUTOPILOT_BACKEND_URL until the production URL
is baked in.
The LLM gateway is the backend's own /llm/v1 (see local_proxy.gateway_upstream).
"""
import os
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class SdkConfig:
    backend_base_url: str = "http://localhost:8010"
    default_time_limit_s: float = 45.0
    sdk_version: str = "0.3.0"
    # Offered as the server address the first time the interactive console starts.
    event_backend_url: str = "https://13-200-183-134.sslip.io/backend"
    telemetry_batch_every_turns: int = 1
    max_backend_retries: int = 5
    # `autopilot serve` — the backend's SDK_URL points here
    service_host: str = "0.0.0.0"
    service_port: int = 8001
    # Time limit for get_reinforcements under `autopilot play`. The game (build12)
    # waits 30 s for a reply before it resends a pause, so agent (15 s) + referee
    # (2 s) + start-up stay well under that. `autopilot serve` clamps it to 4 s.
    reinforcement_timeout_s: float = 15.0
    agent_dir: Path = Path("./agents")


DEFAULT_CONFIG = SdkConfig()
MAX_AGENT_TIME_S = 15.0


def load_config() -> SdkConfig:
    """Return bundled config. Future: load signed file + heartbeat override."""
    updates = {}
    backend_url = os.environ.get("AUTOPILOT_BACKEND_URL")
    if backend_url:
        updates["backend_base_url"] = backend_url
    agent_dir = os.environ.get("AUTOPILOT_AGENT_DIR") or os.environ.get("UPLOAD_DIR")
    if agent_dir:
        updates["agent_dir"] = Path(agent_dir)
    timeout = os.environ.get("AUTOPILOT_REINFORCEMENT_TIMEOUT_SECONDS")
    if timeout:
        try:
            value = float(timeout)
            if value > 0:
                updates["reinforcement_timeout_s"] = min(value, MAX_AGENT_TIME_S)
        except ValueError:
            pass
    return replace(DEFAULT_CONFIG, **updates)
