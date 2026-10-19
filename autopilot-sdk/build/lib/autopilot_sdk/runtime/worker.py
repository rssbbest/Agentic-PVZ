"""Isolated per-turn worker subprocess entry points (doc §8.3).

Each function here runs INSIDE the child process: install guard first,
then touch participant code.
"""
import pickle
import traceback
from multiprocessing.connection import Connection


def _restore_self_state(agent: object, blob: bytes | None) -> None:
    if blob is None:
        return
    try:
        data = pickle.loads(blob)
        if isinstance(data, dict):
            agent.__dict__.update(data)
    except Exception:
        pass  # corrupted state -> start fresh, don't crash match


def _snapshot_self_state(agent: object) -> bytes | None:
    try:
        return pickle.dumps(agent.__dict__)
    except Exception:
        return None


def agent_worker(
    agent_path: str,
    state_json: bytes,
    prev_self_state: bytes | None,
    conn: Connection,
) -> None:
    from autopilot_sdk.network_guard.guard import install_network_guard
    from autopilot_sdk.runtime.loader import load_agent_class
    from autopilot_sdk.contracts.schemas import TurnDecision, TurnState

    install_network_guard()  # MUST happen before participant import
    try:
        AgentClass = load_agent_class(agent_path)
        agent = AgentClass()
        _restore_self_state(agent, prev_self_state)
        state = TurnState.model_validate_json(state_json)
        decision: TurnDecision = agent.decide(state)
        if not isinstance(decision, TurnDecision):
            # Be lenient: allow dict return, validate it.
            decision = TurnDecision.model_validate(decision)
        conn.send(("ok", decision.model_dump_json().encode(), _snapshot_self_state(agent)))
    except Exception as exc:
        conn.send(("error", repr(exc) + "\n" + traceback.format_exc(), None))
    finally:
        try:
            conn.close()
        except Exception:
            pass


def match_start_worker(
    agent_path: str,
    match_info_json: bytes,
    conn: Connection,
) -> None:
    from autopilot_sdk.network_guard.guard import install_network_guard
    from autopilot_sdk.runtime.loader import load_agent_class
    from autopilot_sdk.contracts.schemas import MatchBootstrap

    install_network_guard()
    try:
        AgentClass = load_agent_class(agent_path)
        agent = AgentClass()
        info = MatchBootstrap.model_validate_json(match_info_json)
        agent.on_match_start(info)
        conn.send(("ok", None, _snapshot_self_state(agent)))
    except Exception as exc:
        conn.send(("error", repr(exc) + "\n" + traceback.format_exc(), None))
    finally:
        try:
            conn.close()
        except Exception:
            pass


def match_end_worker(
    agent_path: str,
    result_json: bytes,
    prev_self_state: bytes | None,
    conn: Connection,
) -> None:
    from autopilot_sdk.network_guard.guard import install_network_guard
    from autopilot_sdk.runtime.loader import load_agent_class
    from autopilot_sdk.contracts.schemas import MatchResult

    install_network_guard()
    try:
        AgentClass = load_agent_class(agent_path)
        agent = AgentClass()
        _restore_self_state(agent, prev_self_state)
        result = MatchResult.model_validate_json(result_json)
        agent.on_match_end(result)
        conn.send(("ok", None, None))
    except Exception as exc:
        conn.send(("error", repr(exc) + "\n" + traceback.format_exc(), None))
    finally:
        try:
            conn.close()
        except Exception:
            pass


def placement_worker(
    agent_path: str,
    enemies_json: bytes,
    conn: Connection,
) -> None:
    """Placement mode: one place_defences() call. Sends (status, payload, None)
    where status is "ok" | "load_error" | "blocked" | "error"."""
    import json

    from autopilot_sdk.network_guard.guard import (
        BlockedNetworkCallError,
        install_network_guard,
    )
    from autopilot_sdk.runtime.loader import load_agent_class
    from autopilot_sdk.contracts.schemas import DefencePlacement, EnemyEntry

    install_network_guard()  # MUST happen before participant import
    try:
        try:
            AgentClass = load_agent_class(agent_path)
            agent = AgentClass()
        except Exception as exc:
            conn.send(("load_error", str(exc), None))
            return
        enemies = [EnemyEntry.model_validate(e) for e in json.loads(enemies_json)]
        placement = agent.place_defences(enemies)
        out = [
            p.model_dump() if isinstance(p, DefencePlacement) else p
            for p in (placement or [])
        ]
        conn.send(("ok", json.dumps(out).encode(), None))
    except BlockedNetworkCallError as exc:
        conn.send(("blocked", str(exc), None))
    except Exception as exc:
        conn.send(("error", repr(exc) + "\n" + traceback.format_exc(), None))
    finally:
        try:
            conn.close()
        except Exception:
            pass


def reinforcement_worker(
    agent_path: str,
    snapshot_json: bytes,
    conn: Connection,
) -> None:
    """Execute one live ``get_reinforcements(state)`` call in isolation."""
    import json
    import os
    from urllib.parse import urlsplit

    from autopilot_sdk.network_guard.guard import (
        BlockedNetworkCallError,
        install_agent_sandbox,
    )
    from autopilot_sdk.runtime.loader import load_reinforcement_function

    def report_and_exit(message: str) -> None:
        # A violation ends the run even if agent code catches the error.
        try:
            conn.send(("blocked", message, None))
            conn.close()
        finally:
            os._exit(0)

    gateways = (os.environ.get("AUTOPILOT_GATEWAY_URL", ""),)
    install_agent_sandbox(
        allowed_hosts=[urlsplit(url).hostname for url in gateways if url],
        on_violation=report_and_exit,
    )
    try:
        try:
            function = load_reinforcement_function(agent_path)
        except Exception as exc:
            conn.send(("load_error", str(exc), None))
            return
        state = json.loads(snapshot_json)
        placements = function(state)
        if placements is None:
            placements = []
        if not isinstance(placements, list) or any(not isinstance(item, dict) for item in placements):
            raise TypeError("get_reinforcements(state) must return a list of placement dictionaries")
        conn.send(("ok", json.dumps(placements, allow_nan=False).encode(), None))
    except BlockedNetworkCallError as exc:
        conn.send(("blocked", str(exc), None))
    except Exception as exc:
        conn.send(("error", repr(exc) + "\n" + traceback.format_exc(), None))
    finally:
        try:
            conn.close()
        except Exception:
            pass
