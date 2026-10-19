"""The organisers' LLM, called the standard (OpenAI) chat completions way.

    from autopilot_sdk.llm import LLMClient

    llm = LLMClient()
    reply = llm.chat.completions.create(
        messages=[{"role": "user", "content": "Which row is in danger?"}],
        timeout=5,
    )
    text = reply.choices[0].message.content   # reply["choices"][0]["message"]["content"] works too

No key or URL to set: under the SDK the calls go through the organisers'
gateway with your team's token, which comes with your login. Extra keyword
arguments (``tools``, ``tool_choice``, ``response_format``, ``temperature``,
``max_tokens`` ...) are sent as they are; ``model`` is optional, since the
gateway picks the model. For the tool loop see ``autopilot_sdk.tools``.
"""
from __future__ import annotations

import os
from typing import Any

import requests

try:
    from autopilot_sdk.telemetry.logger import log_event
except Exception:  # pragma: no cover - telemetry optional at import time

    def log_event(*args: Any, **kwargs: Any) -> None:  # type: ignore[no-redef]
        return None


class Response(dict):
    """A chat completion reply: a plain dict that also allows attribute access
    (``reply.choices[0].message.content``), as the OpenAI client does."""

    def __getattr__(self, name: str) -> Any:
        try:
            return _wrap(self[name])
        except KeyError:
            if name in ("content", "tool_calls", "refusal", "usage"):
                return None  # optional fields of the standard reply
            raise AttributeError(name) from None


def _wrap(value: Any) -> Any:
    if isinstance(value, dict) and not isinstance(value, Response):
        return Response(value)
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


class Completions:
    def __init__(self, client: "LLMClient"):
        self._client = client

    def create(self, *, messages: list[dict], timeout: float | None = None, **kwargs: Any) -> Response:
        """POST /chat/completions. ``timeout`` (seconds) overrides the client's
        for this call; zero or less (no time left in the pause) raises
        ``requests.Timeout`` at once."""
        return self._client._post(messages, timeout, kwargs)


class Chat:
    def __init__(self, client: "LLMClient"):
        self.completions = Completions(client)


class LLMClient:
    """Client for the organisers' LLM gateway (OpenAI chat completions format)."""

    def __init__(self, api_key: str = "", base_url: str | None = None, timeout: float = 30.0):
        # Under `autopilot play` the SDK sets both for the agent: the gateway
        # (relayed through the SDK) and the team token from the login.
        self.api_key = api_key or os.environ.get("AUTOPILOT_GATEWAY_KEY", "")
        self.base_url = (base_url or os.environ.get("AUTOPILOT_GATEWAY_URL") or "").rstrip("/")
        self.timeout = timeout
        self.chat = Chat(self)
        self._session = requests.Session()

    def _report(self, text: str) -> None:
        """Show an LLM failure in the participant's terminal, in red. Calls
        relayed by `autopilot play` are reported by the relay instead."""
        relay = os.environ.get("AUTOPILOT_SDK_RELAY_PORT")
        if relay and self.base_url.startswith((f"http://127.0.0.1:{relay}/", f"http://localhost:{relay}/")):
            return
        try:
            from autopilot_sdk.console import fail

            fail(text)
        except Exception:
            pass

    def _post(self, messages: list[dict], timeout: float | None, body: dict) -> Response:
        if not self.base_url:
            self._report("LLM call failed: no gateway (run the agent with the SDK, logged in)")
            raise requests.ConnectionError("no LLM gateway: run the agent with the autopilot SDK")
        timeout = self.timeout if timeout is None else timeout
        if timeout <= 0:
            self._report("LLM call skipped: no time left in this pause")
            raise requests.Timeout("no time left for an LLM call in this pause")
        try:
            resp = self._session.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                json={"messages": messages, **body},
                timeout=timeout,
            )
        except requests.Timeout:
            self._report("LLM call timed out (the agent's own timeout)")
            raise
        except requests.RequestException as exc:
            self._report(f"LLM call failed: cannot reach {self.base_url} ({type(exc).__name__})")
            raise
        if getattr(resp, "status_code", 200) >= 400:
            self._report(f"LLM call failed: HTTP {resp.status_code} {resp.text[:200]}")
        resp.raise_for_status()
        data = resp.json()
        try:
            log_event("llm_call", model=body.get("model"),
                      tokens=(data.get("usage") if isinstance(data, dict) else None))
        except Exception:
            pass
        return Response(data) if isinstance(data, dict) else data


__all__ = ["LLMClient", "Response"]
