"""Team accounts for the competition: passwords, login tokens, the team code.

A team is known to players by its 4-digit code (team id 42 -> "0042"), which
it types in the game's login screen with its password. A successful login
returns a signed token; the SDK sends it as `Authorization: Bearer <token>`
on the calls that need a team (every `eval` call, and `play` with
REQUIRE_LOGIN).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from fastapi import Header, HTTPException

from app.config import settings

TOKEN_TTL_S = 24 * 3600
# PBKDF2 rounds for new passwords; each hash keeps its own count. 100k is ~50 ms
# of CPU on the event server, so a burst of first logins stays short.
_ITERATIONS = 100_000
# Checks that already passed, so the game's repeated logins cost nothing:
# sha256(stored hash + password) -> True. Changing a password changes the key.
_VERIFIED: dict[str, bool] = {}
_VERIFIED_MAX = 20_000


def team_code(team_id: int) -> str:
    return f"{team_id:04d}"


_PASSWORD_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i: easy to read out


def new_password() -> str:
    """A random password for an imported team, e.g. "k7mxq-p2wde" (about 54 bits)."""
    pick = lambda n: "".join(secrets.choice(_PASSWORD_LETTERS) for _ in range(n))  # noqa: E731
    return f"{pick(5)}-{pick(5)}"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def check_password(password: str, stored: str | None) -> bool:
    key = hashlib.sha256(f"{stored}\0{password}".encode()).hexdigest()
    if key in _VERIFIED:
        return True
    if _check_password(password, stored):
        if len(_VERIFIED) >= _VERIFIED_MAX:
            _VERIFIED.clear()
        _VERIFIED[key] = True
        return True
    return False


def _check_password(password: str, stored: str | None) -> bool:
    try:
        _, iterations, salt, digest = (stored or "").split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _key() -> bytes:
    secret = settings.TEAM_TOKEN_SECRET or ("team-token:" + settings.ADMIN_SECRET)
    return hashlib.sha256(secret.encode()).digest()


def issue_token(team_id: int, now: float | None = None) -> str:
    expires = int((now or time.time()) + TOKEN_TTL_S)
    body = f"{team_id}.{expires}"
    sig = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def token_team(token: str, now: float | None = None) -> int | None:
    """The team id a valid, unexpired token belongs to, else None."""
    try:
        team_id, expires, sig = token.strip().split(".")
        body = f"{int(team_id)}.{int(expires)}"
    except (ValueError, AttributeError):
        return None
    expected = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected) or int(expires) < (now or time.time()):
        return None
    return int(team_id)


def llm_token(team_id: int) -> str | None:
    """The team's LLM gateway token (integration/gateway.py team_token)."""
    secret = settings.AUTOPILOT_GATEWAY_TEAM_SECRET
    if not secret:
        return None
    code = team_code(team_id)
    return f"{code}.{hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()[:20]}"


def bearer_team(authorization: str | None) -> int | None:
    token = (authorization or "").removeprefix("Bearer ").strip()
    return token_team(token) if token else None


async def require_team(authorization: str = Header(default="")) -> int:
    """FastAPI dependency: the logged-in team's id."""
    team_id = bearer_team(authorization)
    if team_id is None:
        raise HTTPException(status_code=401, detail="Log in first (team ID and password in the game)")
    return team_id
