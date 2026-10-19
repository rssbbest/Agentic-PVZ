"""Tool calling helper: schemas from Python functions and the call loop."""
import json
from typing import Literal, Optional

from autopilot_sdk.runtime.reinforcements import run_reinforcements
from autopilot_sdk.tools import run_tool_loop, tool, tool_schema
from test_reinforcements import SNAPSHOT


@tool
def free_cells(row: int, kinds: list[int], mode: Literal["near", "far"] = "near",
               note: Optional[str] = None) -> list[int]:
    """Free columns in a row.

    Longer explanation that is not part of the summary.

    Args:
        row: Board row, 0-5.
        kinds: Defence IDs to consider,
            continued on a second line.
        mode: Which end to prefer.
    """
    return [0, 1] if mode == "near" else [9, 10]


def test_schema_from_signature_and_docstring():
    fn = free_cells.__autopilot_tool__["function"]
    assert fn["name"] == "free_cells"
    assert fn["description"] == "Free columns in a row."
    params = fn["parameters"]
    assert params["required"] == ["row", "kinds"]
    assert params["properties"]["row"] == {"type": "integer", "description": "Board row, 0-5."}
    assert params["properties"]["kinds"]["items"] == {"type": "integer"}
    assert params["properties"]["kinds"]["description"].endswith("continued on a second line.")
    assert params["properties"]["mode"]["enum"] == ["near", "far"]
    assert params["properties"]["note"]["type"] == "string"


def test_undecorated_function_and_overrides():
    def plain(x: float) -> float:
        return x
    assert tool_schema(plain)["function"]["parameters"]["properties"]["x"]["type"] == "number"
    named = tool(name="renamed", description="custom")(plain)
    assert named.__autopilot_tool__["function"]["name"] == "renamed"
    assert named.__autopilot_tool__["function"]["description"] == "custom"


class ScriptedClient:
    """Plays back assistant messages and records what the loop sent."""

    def __init__(self, *messages):
        self.replies = list(messages)
        self.requests = []
        self.chat = self.completions = self  # client.chat.completions.create(...)

    def create(self, *, messages, timeout=None, **kwargs):
        self.requests.append({"messages": [dict(m) for m in messages],
                              "timeout": timeout, **kwargs})
        return {"choices": [{"message": self.replies.pop(0)}]}


def _call(call_id, name, args):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
            "extra_content": {"google": {"thought_signature": "sig-" + call_id}}}


def test_loop_runs_tools_and_returns_final_answer():
    client = ScriptedClient(
        {"role": "assistant", "content": None,
         "tool_calls": [_call("a", "free_cells", {"row": 2, "kinds": [1]}),
                        _call("b", "free_cells", {"row": 2, "kinds": [1], "mode": "far"})]},
        {"role": "assistant", "content": '{"placements": []}'},
    )
    run = run_tool_loop(client, [{"role": "user", "content": "go"}], [free_cells],
                        model="m", deadline_s=10)
    assert run.content == '{"placements": []}' and run.stopped == "answered" and run.rounds == 2
    assert [c.result for c in run.calls] == [[0, 1], [9, 10]]
    first, second = client.requests
    assert first["tools"][0]["function"]["name"] == "free_cells" and first["model"] == "m"
    assert 0 < first["timeout"] <= 10
    sent = second["messages"]
    # The assistant turn goes back verbatim, provider extras included.
    assert sent[1]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "sig-a"
    assert sent[2] == {"role": "tool", "tool_call_id": "a", "name": "free_cells",
                       "content": json.dumps({"result": [0, 1]})}


def test_tool_errors_are_reported_to_the_model():
    def boom() -> int:
        """Always fails."""
        raise RuntimeError("no luck")
    client = ScriptedClient(
        {"role": "assistant", "tool_calls": [
            _call("a", "boom", {}), _call("b", "missing", {}),
            {"id": "c", "function": {"name": "boom", "arguments": "not json"}}]},
        {"role": "assistant", "content": "done"},
    )
    run = run_tool_loop(client, [{"role": "user", "content": "go"}], [boom])
    assert [c.error for c in run.calls] == [
        "RuntimeError: no luck", "unknown tool 'missing'",
        "invalid arguments: Expecting value: line 1 column 1 (char 0)"]
    tool_messages = [m for m in client.requests[1]["messages"] if m["role"] == "tool"]
    assert json.loads(tool_messages[0]["content"]) == {"error": "RuntimeError: no luck"}


def test_loop_stops_at_max_rounds_and_deadline():
    looping = {"role": "assistant", "tool_calls": [_call("a", "free_cells", {"row": 1, "kinds": []})]}
    run = run_tool_loop(ScriptedClient(looping, looping), [{"role": "user", "content": "go"}],
                        [free_cells], max_rounds=2)
    assert run.stopped == "max_rounds" and run.content is None and run.rounds == 2
    client = ScriptedClient(looping, {"role": "assistant", "content": "final"})
    run = run_tool_loop(client, [{"role": "user", "content": "go"}], [free_cells], max_rounds=2)
    assert run.content == "final"
    assert client.requests[0]["tools"] and "tools" not in client.requests[1]
    assert client.requests[1]["messages"][-1]["content"].startswith("Tools are closed")
    assert run.messages[-2]["role"] == "tool"  # the nudge is not kept in history
    run = run_tool_loop(ScriptedClient(), [{"role": "user", "content": "go"}], [free_cells],
                        deadline_s=0.1)
    assert run.stopped == "deadline" and run.rounds == 0


def test_tool_agent_runs_inside_the_sandbox_without_a_gateway(monkeypatch):
    # No gateway on this port: the agent must fall back, never crash or hang.
    monkeypatch.setenv("AUTOPILOT_GATEWAY_URL", "http://127.0.0.1:9/v1")
    from pathlib import Path
    source = (Path(__file__).parents[1] / "examples" / "llm_tool_agent.py").read_bytes()
    placements = run_reinforcements(source, SNAPSHOT, timeout_s=10)
    assert placements and all(p["defenceId"] == 1 for p in placements)


def test_client_without_key_sends_no_authorization_header(monkeypatch):
    from autopilot_sdk.llm import LLMClient
    seen = {}

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": "{}"}}]}

    def post(url, headers, json, timeout):
        seen.update(headers=headers, timeout=timeout)
        return Resp()

    client = LLMClient(api_key="", base_url="http://gateway/v1", timeout=9)
    monkeypatch.setattr(client._session, "post", post)
    reply = client.chat.completions.create(messages=[{"role": "user", "content": "x"}], timeout=1.5)
    assert "Authorization" not in seen["headers"] and seen["timeout"] == 1.5
    # The standard reply, with attribute access as in the OpenAI client.
    assert reply.choices[0].message.content == "{}" == reply["choices"][0]["message"]["content"]
    assert reply.choices[0].message.tool_calls is None and reply.usage is None


def test_chat_with_no_time_left_raises_a_timeout_without_calling(monkeypatch):
    import pytest
    import requests

    from autopilot_sdk.llm import LLMClient

    client = LLMClient(api_key="", base_url="http://gateway/v1")
    monkeypatch.setattr(client._session, "post", lambda *a, **k: pytest.fail("no call expected"))
    for left in (0, -0.3):
        with pytest.raises(requests.Timeout, match="no time left"):
            client.chat.completions.create(messages=[{"role": "user", "content": "x"}], timeout=left)


def test_client_outside_the_sdk_says_there_is_no_gateway(monkeypatch):
    import pytest
    import requests

    from autopilot_sdk.llm import LLMClient

    monkeypatch.delenv("AUTOPILOT_GATEWAY_URL", raising=False)
    with pytest.raises(requests.ConnectionError, match="no LLM gateway"):
        LLMClient().chat.completions.create(messages=[{"role": "user", "content": "x"}])
