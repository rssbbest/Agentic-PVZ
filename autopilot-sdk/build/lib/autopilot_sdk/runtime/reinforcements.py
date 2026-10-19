"""Run a participant's live reinforcement hook against one board snapshot."""
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from autopilot_sdk.runtime.orchestrator import _receive, _spawn_and_join
from autopilot_sdk.runtime.worker import reinforcement_worker
from autopilot_sdk.verifier.static_scan import scan_source

logger = logging.getLogger("autopilot.reinforcements")
_TEAM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,119}$")


def find_agent_file(agent_dir: Path, team_id: str) -> Path | None:
    """Find the newest backend upload for a team in a shared upload directory.

    The backend stores submissions as ``team{numeric_id}_{uuid}.py``. The
    canonical ``{teamID}.py`` form is also supported for direct deployments.
    """
    if not _TEAM_ID.fullmatch(team_id):
        return None
    root = agent_dir.expanduser().resolve()
    normalized_id = team_id.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", normalized_id).strip("_")
    suffix = slug[4:].lstrip("_") if slug.startswith("team") else slug
    direct_names = {normalized_id, slug, suffix, f"team{suffix}"}
    upload_prefixes = {
        f"{slug}_",
        f"team{suffix}_",
    }
    candidates = []
    for candidate in root.glob("*.py"):
        stem = candidate.stem.lower()
        if stem in direct_names or any(
            stem.startswith(prefix) for prefix in upload_prefixes
        ):
            candidates.append(candidate)
    candidates = [
        candidate for candidate in candidates
        if candidate.is_file() and candidate.resolve().is_relative_to(root)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))


def run_reinforcements(
    agent_source: bytes,
    snapshot: dict[str, Any],
    timeout_s: float = 3.5,
) -> list[dict[str, Any]]:
    """Execute ``get_reinforcements(snapshot)`` or return an empty candidate list.

    Returning no candidates matches the backend's safe skip behavior for agent
    errors, policy violations, timeouts, invalid output, and process crashes.
    """
    try:
        source = agent_source.decode("utf-8-sig")
    except UnicodeDecodeError:
        logger.warning("No placements this pause: agent.py is not UTF-8 text (save it as UTF-8)")
        return []
    report = scan_source(source)
    if not report.passed:
        logger.warning("No placements this pause: agent.py failed the safety scan: %s", "; ".join(map(str, report.violations)))
        return []

    fd, agent_path = tempfile.mkstemp(prefix="agent_", suffix=".py")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(agent_source)
        timed_out, conn = _spawn_and_join(
            reinforcement_worker,
            (agent_path, json.dumps(snapshot).encode()),
            timeout_s,
        )
    finally:
        try:
            os.unlink(agent_path)
        except OSError:
            pass

    if timed_out:
        logger.warning("No placements this pause: get_reinforcements took longer than its %.1f s limit "
                       "(state['timeBudgetSeconds'])", timeout_s)
        return []
    if conn is None:
        logger.warning("No placements this pause: the agent process could not start")
        return []
    message = _receive(conn, 0.25)
    if message is None:
        logger.warning("No placements this pause: the agent process exited without returning anything")
        return []
    status, payload, _ = message

    if status != "ok":
        logger.warning("No placements this pause: agent %s: %s", status, str(payload)[:1500].rstrip())
        return []
    try:
        placements = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        logger.warning("No placements this pause: get_reinforcements must return JSON-serialisable data")
        return []
    if not isinstance(placements, list):
        logger.warning("No placements this pause: get_reinforcements must return a list of placements")
        return []
    # The backend referee owns placement validation and drops invalid candidates
    # individually. Keep only JSON objects so the wire response stays well-formed.
    return [item for item in placements if isinstance(item, dict)]
