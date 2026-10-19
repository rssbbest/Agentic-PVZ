"""Which SDK builds may play: the ones in the latest sdk-manifest.json.

The deploy writes the manifest next to the downloads (deploy/package-downloads.sh)
and into the backend image. An SDK sends its build in the X-Autopilot-SDK header
(autopilot_sdk.updater.identity: its program's sha256, or its Python package's
fingerprint). A game or a published run from any other build is refused with
426, and the SDK updates itself on that reply. Without a manifest (a local
quickstart) every SDK may play.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

HEADER = "X-Autopilot-SDK"
OUTDATED_STATUS = 426
OUTDATED_MESSAGE = ("This Autopilot SDK is out of date. A current SDK updates itself and starts again; "
                    "an SDK from before the automatic updates must be installed again: run the install "
                    "line from the guide (it keeps your agent.py).")

_CANDIDATES = (
    os.environ.get("SDK_MANIFEST", ""),
    "/app/sdkinfo/sdk-manifest.json",
    str(Path(__file__).resolve().parents[3] / "autopilot-sdk-binary" / "bin" / "sdk-manifest.json"),
)
_cache: dict = {"path": None, "mtime": None, "accepted": None, "manifest": None}


def manifest() -> dict | None:
    """The current manifest (re-read when the file changes), or None."""
    for candidate in _CANDIDATES:
        if not candidate:
            continue
        path = Path(candidate)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if _cache["path"] != path or _cache["mtime"] != mtime:
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                logger.warning("SDK manifest %s unreadable: %s", path, exc)
                continue
            accepted = {data.get("build")} | set((data.get("programs") or {}).values())
            accepted.discard(None)
            _cache.update(path=path, mtime=mtime, accepted=accepted, manifest=data)
            logger.info("SDK manifest %s: %d accepted builds", path, len(accepted))
        return _cache["manifest"]
    return None


def outdated(sent: str | None) -> bool:
    """True if a manifest exists and ``sent`` (the SDK's header) is not in it."""
    if manifest() is None or not _cache["accepted"]:
        return False
    return (sent or "").strip().lower() not in _cache["accepted"]
