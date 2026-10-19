"""Turn orchestrator: process-per-turn isolation + timeout + memory (doc §8.3)."""
import multiprocessing as mp

from autopilot_sdk.contracts.schemas import (
    MatchBootstrap,
    MatchResult,
    TurnDecision,
    TurnState,
)
from autopilot_sdk.runtime.worker import (
    agent_worker,
    match_end_worker,
    match_start_worker,
)


def _log_violation_lazy(event: str, **fields: object) -> None:
    try:
        from autopilot_sdk.telemetry.logger import log_violation

        log_violation(event, **fields)
    except Exception:
        pass


def _receive(conn: object, wait_s: float) -> tuple | None:
    """Read the worker's (status, payload, state) message, or None if it died.

    A worker that exits without sending closes its pipe end. Linux reports that
    as EOFError from recv(); Windows raises BrokenPipeError from poll() itself.
    """
    try:
        if not conn.poll(wait_s):
            return None
        return conn.recv()
    except (EOFError, OSError):
        return None
    finally:
        conn.close()


def _spawn_and_join(target: object, args: tuple, timeout_s: float) -> tuple[bool, object]:
    """Spawn daemon child, join with timeout. Returns (timed_out, conn)."""
    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=target, args=(*args, child_conn), daemon=True)
    proc.start()
    try:
        child_conn.close()  # parent doesn't need the child end
    except Exception:
        pass
    proc.join(timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        if proc.is_alive():  # pragma: no cover - last resort
            try:
                proc.kill()
            except Exception:
                pass
            proc.join(5)
        parent_conn.close()
        return True, None
    return False, parent_conn


class TurnOrchestrator:
    def __init__(self, agent_path: str, default_timeout_s: float = 45.0):
        self.agent_path = agent_path
        self.default_timeout_s = default_timeout_s
        self._pickled_self_state: bytes | None = None

    @property
    def has_memory(self) -> bool:
        return self._pickled_self_state is not None

    def run_match_start(
        self, match_info: MatchBootstrap, timeout_s: float | None = None
    ) -> bool:
        timeout = timeout_s or self.default_timeout_s
        timed_out, conn = _spawn_and_join(
            match_start_worker,
            (self.agent_path, match_info.model_dump_json().encode()),
            timeout,
        )
        if timed_out:
            _log_violation_lazy("match_start_timeout", limit_s=timeout)
            return False
        assert conn is not None
        message = _receive(conn, 5)
        if message is None:
            _log_violation_lazy("match_start_no_output")
            return False
        status, _, new_state = message
        if status == "error":
            return False
        self._pickled_self_state = new_state
        return True

    def run_turn(self, state: TurnState) -> TurnDecision:
        timeout_s = state.time_limit_seconds or self.default_timeout_s
        timed_out, conn = _spawn_and_join(
            agent_worker,
            (
                self.agent_path,
                state.model_dump_json().encode(),
                self._pickled_self_state,
            ),
            timeout_s,
        )
        if timed_out:
            _log_violation_lazy(
                "turn_timeout", turn=state.turn_number, limit_s=timeout_s
            )
            return TurnDecision(actions=[], metadata={"timeout": True})

        assert conn is not None
        message = _receive(conn, 5)
        if message is None:
            _log_violation_lazy("worker_crashed_no_output", turn=state.turn_number)
            return TurnDecision(actions=[], metadata={"crashed": True})
        status, payload, new_state = message

        if status == "error":
            _log_violation_lazy(
                "agent_exception", turn=state.turn_number, detail=str(payload)[:2000]
            )
            return TurnDecision(actions=[], metadata={"error": str(payload)[:2000]})

        assert isinstance(payload, (bytes, str))
        raw = payload.decode() if isinstance(payload, bytes) else payload
        try:
            decision = TurnDecision.model_validate_json(raw)
        except Exception as exc:
            _log_violation_lazy(
                "agent_bad_output", turn=state.turn_number, detail=repr(exc)[:1000]
            )
            return TurnDecision(actions=[], metadata={"error": f"bad output: {exc!r}"})

        self._pickled_self_state = new_state
        if new_state is None:
            _log_violation_lazy(
                "unpicklable_memory", turn=state.turn_number
            )
        return decision

    def run_match_end(
        self, result: MatchResult, timeout_s: float | None = None
    ) -> bool:
        timeout = timeout_s or self.default_timeout_s
        timed_out, conn = _spawn_and_join(
            match_end_worker,
            (
                self.agent_path,
                result.model_dump_json().encode(),
                self._pickled_self_state,
            ),
            timeout,
        )
        if timed_out:
            _log_violation_lazy("match_end_timeout")
            return False
        assert conn is not None
        return _receive(conn, 5) is not None
