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
        conn.send(("error", explain_agent_error(exc, agent_path, "in get_reinforcements"), None))
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
        conn.send(("error", explain_agent_error(exc, agent_path, "in get_reinforcements"), None))
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
        conn.send(("error", explain_agent_error(exc, agent_path, "in get_reinforcements"), None))
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
            conn.send(("load_error", explain_agent_error(exc.__cause__ or exc, agent_path, "while loading agent.py"), None))
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
        conn.send(("error", explain_agent_error(exc, agent_path, "in get_reinforcements"), None))
    finally:
        try:
            conn.close()
        except Exception:
            pass


def explain_agent_error(exc: BaseException, agent_path: str, when: str) -> str:
    """A participant-readable error: the line of agent.py that failed and why.

        agent.py line 12, in get_reinforcements: KeyError: 'budgt'
            coins = state["budgt"]["remaining"]
    """
    import traceback as tb

    what = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    if isinstance(exc, SyntaxError) and exc.lineno:
        code = (exc.text or "").rstrip()
        return f"agent.py line {exc.lineno}: {exc.msg}" + (f"\n    {code.strip()}" if code else "")
    frames = [f for f in tb.extract_tb(exc.__traceback__) if f.filename == agent_path]
    if not frames:
        return f"agent.py {when}: {what}"
    last = frames[-1]
    where = f"agent.py line {last.lineno}, in {last.name}" if last.name != "<module>" else f"agent.py line {last.lineno}"
    hint = ""
    if isinstance(exc, KeyError):
        hint = "\n    (that key is not in the dictionary: see the state table in the guide)"
    return f"{where}: {what}" + (f"\n    {last.line}" if last.line else "") + hint


def reinforcement_worker(
    agent_path: str,
    snapshot_json: bytes,
    frozen: bool,
    conn: Connection,
) -> None:
    """Execute one live ``get_reinforcements(state)`` call in isolation.
    ``frozen``: a published run's frozen agent.py, which may read no other file."""
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

    if frozen:
        # The audit hook terminates on forbidden opens before an exception can
        # be caught. SDK telemetry must therefore never attempt a file write.
        from autopilot_sdk.telemetry.logger import disable_file_logging
        disable_file_logging()

    gateways = [urlsplit(url) for url in (os.environ.get("AUTOPILOT_GATEWAY_URL", ""),) if url]
    install_agent_sandbox(
        allowed_hosts=[g.hostname for g in gateways if g.hostname],
        on_violation=report_and_exit,
        # A gateway on this machine (development) is reachable on its own port only.
        loopback_ports=[g.port or (443 if g.scheme == "https" else 80) for g in gateways],
        frozen_agent=agent_path if frozen else None,
    )
    try:
        try:
            function = load_reinforcement_function(agent_path)
        except Exception as exc:
            conn.send(("load_error", explain_agent_error(exc.__cause__ or exc, agent_path, "while loading agent.py"), None))
            return
        state = json.loads(snapshot_json)
        placements = function(state)
        if placements is None:
            placements = []
        if not isinstance(placements, list) or any(not isinstance(item, dict) for item in placements):
            got = type(placements).__name__ if not isinstance(placements, list) else "a list with non-dictionary items"
            conn.send(("error", f"agent.py: get_reinforcements(state) returned {got}; it must return a list of "
                                'placements like [{"defenceId": 1, "defenceGridPosition": {"x": 1, "y": 2}}] '
                                "([] to place nothing)", None))
            return
        conn.send(("ok", json.dumps(placements, allow_nan=False).encode(), None))
    except BlockedNetworkCallError as exc:
        conn.send(("blocked", str(exc), None))
    except Exception as exc:
        conn.send(("error", explain_agent_error(exc, agent_path, "in get_reinforcements"), None))
    finally:
        try:
            conn.close()
        except Exception:
            pass
