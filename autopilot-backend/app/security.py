"""
app/security.py  –  shared-secret auth for service-to-service and admin calls
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from app.config import settings


def _check(provided: str, expected: str, detail: str) -> None:
    # compare_digest avoids leaking the secret through timing differences
    if not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail=detail)


async def verify_simulator(
    x_simulator_secret: str = Header(default="", alias="X-Simulator-Secret"),
) -> None:
    """Only the simulator may post run results."""
    _check(x_simulator_secret, settings.SIMULATOR_SECRET, "Unauthorized simulator")


async def verify_admin(
    x_admin_secret: str = Header(default="", alias="X-Admin-Secret"),
) -> None:
    """Organiser-only endpoints."""
    _check(x_admin_secret, settings.ADMIN_SECRET, "Admin secret required")
