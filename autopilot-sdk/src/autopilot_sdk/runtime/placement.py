"""Placement mode: validate + run agent.py once against an enemy wave.

This is what the backend's `POST {SDK_URL}/run` triggers (see server.py).
Every agent-side problem becomes a violation in an invalid RunResponse; the
caller only sees an exception for SDK-side bugs.
"""
import json
import os
import tempfile

from pydantic import TypeAdapter, ValidationError

from autopilot_sdk.contracts.schemas import (
    DefencePlacement,
    EnemyEntry,
    RunResponse,
    SdkStatus,
    Violation,
)
from autopilot_sdk.runtime.orchestrator import (
    _log_violation_lazy,
    _receive,
    _spawn_and_join,
)
from autopilot_sdk.runtime.worker import placement_worker
from autopilot_sdk.verifier.static_scan import scan_source

_placements = TypeAdapter(list[DefencePlacement])


def _invalid(*violations: Violation) -> RunResponse:
    for v in violations:
        _log_violation_lazy("placement_" + v.type, detail=v.message[:1000])
    return RunResponse(sdkStatus=SdkStatus(valid=False, violations=list(violations)))


def _check_rules(placement: list[DefencePlacement]) -> list[Violation]:
    violations: list[Violation] = []
    seen: set[tuple[int, int]] = set()
    for p in placement:
        key = (p.defenceGridPosition.x, p.defenceGridPosition.y)
        if key in seen:
            violations.append(
                Violation(type="duplicate_placement", message=f"Defence placed twice at {key}")
            )
        seen.add(key)
    return violations


def run_placement(
    agent_source: bytes, enemies: list[EnemyEntry], timeout_s: float
) -> RunResponse:
    try:
        source = agent_source.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _invalid(Violation(type="invalid_agent", message="agent.py is not valid UTF-8"))

    report = scan_source(source)
    if not report.passed:
        return _invalid(
            *(Violation(type="forbidden_api_usage", message=v) for v in report.violations)
        )

    fd, agent_path = tempfile.mkstemp(prefix="agent_", suffix=".py")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(agent_source)
        timed_out, conn = _spawn_and_join(
            placement_worker,
            (agent_path, json.dumps([e.model_dump() for e in enemies]).encode()),
            timeout_s,
        )
    finally:
        try:
            os.unlink(agent_path)
        except OSError:
            pass

    if timed_out:
        return _invalid(
            Violation(type="timeout", message=f"place_defences() exceeded {timeout_s:g}s")
        )
    assert conn is not None
    message = _receive(conn, 5)
    if message is None:
        return _invalid(
            Violation(type="agent_crashed", message="Agent process exited without output")
        )
    status, payload, _ = message

    if status == "load_error":
        return _invalid(Violation(type="invalid_agent", message=str(payload)[:2000]))
    if status == "blocked":
        return _invalid(Violation(type="forbidden_api_usage", message=str(payload)[:2000]))
    if status == "error":
        return _invalid(Violation(type="agent_exception", message=str(payload)[:2000]))

    try:
        placement = _placements.validate_json(payload)
    except ValidationError as exc:
        return _invalid(Violation(type="invalid_agent_output", message=str(exc)[:2000]))

    rule_violations = _check_rules(placement)
    if rule_violations:
        return _invalid(*rule_violations)
    if not placement:
        return _invalid(
            Violation(type="invalid_agent_output", message="place_defences() returned no defences")
        )
    return RunResponse(sdkStatus=SdkStatus(valid=True), defencePlacementData=placement)
