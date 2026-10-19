"""LLM tool calling for participant agents.

Write ordinary Python functions in agent.py, mark them with ``@tool`` and pass
them to ``run_tool_loop``. The model sees each function as an OpenAI-style
function tool; when it asks for one, the function runs here, inside the
agent's sandboxed process, and the result goes back to the model. The loop
ends when the model answers without asking for a tool.

    from autopilot_sdk.llm import LLMClient
    from autopilot_sdk.tools import run_tool_loop, tool

    def get_reinforcements(state):
        @tool
        def free_cells(row: int) -> list[int]:
            '''Columns in a row with no living defence.

            Args:
                row: Board row, 0-5.
            '''
            taken = {d["position"]["x"] for d in state["defences"]
                     if d["position"]["y"] == row and d["health"] > 0}
            return [x for x in range(11) if x not in taken]

        run = run_tool_loop(LLMClient(), messages, [free_cells], deadline_s=2.8)
        return parse(run.content)

Schemas come from type hints and the docstring: the first paragraph is the
tool description and an ``Args:`` section describes parameters. Supported
hints: int, float, str, bool, list[...], dict, Literal[...], Optional[...].
"""
import inspect
import json
import re
import time
import typing
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_RESULT_CHARS = 20_000
FINAL_PROMPT = "Tools are closed now. Give your final answer in the requested format."
_TYPES = {int: "integer", float: "number", str: "string", bool: "boolean", dict: "object"}


def _json_type(hint: Any) -> dict:
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if hint in _TYPES:
        return {"type": _TYPES[hint]}
    if hint in (list, tuple, set):
        return {"type": "array"}
    if origin in (list, tuple, set):
        return {"type": "array", **({"items": _json_type(args[0])} if args else {})}
    if origin is dict:
        return {"type": "object"}
    if origin is typing.Literal:
        values = list(args)
        schema = {"enum": values}
        kinds = {type(v) for v in values}
        if len(kinds) == 1 and next(iter(kinds)) in _TYPES:
            schema["type"] = _TYPES[next(iter(kinds))]
        return schema
    if origin is typing.Union or type(hint).__name__ == "UnionType":
        options = [a for a in args if a is not type(None)]
        if len(options) == 1:
            return _json_type(options[0])
        return {"anyOf": [_json_type(a) for a in options]}
    return {}


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    doc = inspect.cleandoc(doc or "")
    summary = doc.split("\n\n", 1)[0].replace("\n", " ").strip()
    params: dict[str, str] = {}
    match = re.search(r"^(?:Args|Arguments|Parameters):\s*$(.*?)(?:^\S|\Z)", doc, re.M | re.S)
    if match:
        current = None
        for line in match.group(1).splitlines():
            item = re.match(r"\s+(\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)", line)
            if item:
                current = item.group(1)
                params[current] = item.group(2).strip()
            elif current and line.strip():
                params[current] += " " + line.strip()
    return summary, params


def tool_schema(func: Callable, name: str | None = None, description: str | None = None) -> dict:
    """Build an OpenAI-style function-tool schema from a Python function."""
    summary, param_docs = _parse_docstring(func.__doc__ or "")
    hints = typing.get_type_hints(func)
    properties, required = {}, []
    for param in inspect.signature(func).parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        prop = _json_type(hints.get(param.name, Any))
        if param.name in param_docs:
            prop["description"] = param_docs[param.name]
        properties[param.name] = prop
        hint = hints.get(param.name)
        optional = hint is not None and type(None) in typing.get_args(hint)
        if param.default is param.empty and not optional:
            required.append(param.name)
    return {
        "type": "function",
        "function": {
            "name": name or func.__name__,
            "description": description or summary or func.__name__,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def tool(func: Callable | None = None, *, name: str | None = None, description: str | None = None):
    """Mark a function as a model-callable tool. Usable as @tool or @tool(name=...)."""
    def wrap(f: Callable) -> Callable:
        f.__autopilot_tool__ = tool_schema(f, name, description)
        return f
    return wrap(func) if func is not None else wrap


def _schema(func: Callable) -> dict:
    return getattr(func, "__autopilot_tool__", None) or tool_schema(func)


@dataclass
class ToolCall:
    name: str
    arguments: dict
    result: Any = None
    error: str | None = None


@dataclass
class ToolRun:
    content: str | None                 # the model's final answer (None if stopped early)
    messages: list[dict]                # full conversation, including tool turns
    calls: list[ToolCall] = field(default_factory=list)
    rounds: int = 0
    stopped: str = "answered"           # answered | max_rounds | deadline
    response: dict | None = None        # last raw chat response


def _run_one(registry: dict[str, Callable], name: str, raw_args: Any) -> ToolCall:
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args or {})
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be a JSON object")
    except ValueError as exc:
        return ToolCall(name, {}, error=f"invalid arguments: {exc}")
    func = registry.get(name)
    if func is None:
        return ToolCall(name, args, error=f"unknown tool '{name}'")
    try:
        return ToolCall(name, args, result=func(**args))
    except Exception as exc:  # the model sees the error and can recover
        return ToolCall(name, args, error=f"{type(exc).__name__}: {exc}")


def run_tool_loop(
    client: Any,
    messages: list[dict],
    tools: list[Callable],
    *,
    model: str | None = None,
    max_rounds: int = 4,
    deadline_s: float | None = None,
    final_prompt: str | None = FINAL_PROMPT,
    **chat_kwargs: Any,
) -> ToolRun:
    """Chat with tools until the model answers, max_rounds is hit or time runs out.

    Each round is one model call. The last round sends no tools and adds
    ``final_prompt`` as a user message, so the model answers with what it has
    (with tools visible, some models write a tool call as text even under
    tool_choice "none"). Pass ``final_prompt=None`` to skip the message.

    ``client`` is an ``LLMClient`` (or any client with the standard
    ``chat.completions.create``, e.g. ``openai.OpenAI``).
    ``deadline_s`` bounds the whole loop; keep it under the agent time limit
    (state["timeBudgetSeconds"], 10 s with `autopilot play`) so the agent can still return something.
    """
    started = time.monotonic()
    registry = {_schema(f)["function"]["name"]: f for f in tools}
    schemas = [_schema(f) for f in tools]
    history = list(messages)
    run = ToolRun(content=None, messages=history)
    if model is not None:
        chat_kwargs["model"] = model
    while run.rounds < max_rounds:
        timeout = None
        if deadline_s is not None:
            timeout = deadline_s - (time.monotonic() - started)
            if timeout < 0.2:
                run.stopped = "deadline"
                return run
        options, sent = dict(chat_kwargs), history
        if run.rounds == max_rounds - 1:
            # Last round: send no tools so the model can only answer. Some
            # models ignore tool_choice "none" and write a tool call as text.
            options.pop("tool_choice", None)
            if final_prompt:
                sent = history + [{"role": "user", "content": final_prompt}]
        else:
            options["tools"] = schemas
        response = client.chat.completions.create(messages=sent, timeout=timeout, **options)
        if not isinstance(response, dict) and hasattr(response, "model_dump"):
            response = response.model_dump()  # an openai.OpenAI reply object
        run.rounds += 1
        run.response = response
        message = dict(response["choices"][0]["message"])
        history.append(message)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            run.content = message.get("content")
            return run
        for call in tool_calls:
            fn = call.get("function") or {}
            outcome = _run_one(registry, fn.get("name", ""), fn.get("arguments"))
            run.calls.append(outcome)
            payload = {"error": outcome.error} if outcome.error else {"result": outcome.result}
            history.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "name": outcome.name,
                "content": json.dumps(payload, default=str)[:MAX_RESULT_CHARS],
            })
    run.stopped = "max_rounds"
    return run


__all__ = ["tool", "tool_schema", "run_tool_loop", "ToolRun", "ToolCall"]
