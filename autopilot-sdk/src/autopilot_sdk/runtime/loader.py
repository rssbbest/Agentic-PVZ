"""Dynamic loading of participant Agent class (doc §8)."""
import importlib.util
import inspect
import uuid


def load_agent_class(agent_path: str) -> type:
    """Import agent.py and return its Agent subclass.

    Rules: exactly one concrete subclass of autopilot_sdk Agent defined
    (or imported) in that file. Raises ImportError / ValueError with a
    participant-actionable message otherwise.
    """
    from autopilot_sdk.contracts.agent_base import Agent

    module_name = f"_participant_agent_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, agent_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load agent file: {agent_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:
        raise ImportError(f"agent.py raised on import: {exc!r}") from exc

    candidates: list[type] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        try:
            if issubclass(obj, Agent) and obj is not Agent:
                # Only count classes actually defined in agent.py, not
                # re-exported imports (avoids false "multiple agents").
                if obj.__module__ == module.__name__:
                    candidates.append(obj)
        except Exception:
            continue

    if not candidates:
        raise ValueError(
            "No Agent subclass found in agent.py. "
            "Define `class MyAgent(Agent):` with a place_defences() or decide() method."
        )
    if len(candidates) > 1:
        names = ", ".join(c.__name__ for c in candidates)
        raise ValueError(
            f"Multiple Agent subclasses found ({names}). Keep exactly one."
        )
    cls = candidates[0]
    if inspect.isabstract(cls) or (
        cls.decide is Agent.decide and cls.place_defences is Agent.place_defences
    ):
        raise ValueError(
            f"Agent class '{cls.__name__}' implements neither decide() nor "
            "place_defences() — implement at least one."
        )
    return cls


def load_reinforcement_function(agent_path: str):
    """Import an agent module and return its module-level hook.

    Live Unity reinforcements use ``get_reinforcements(state: dict)`` rather
    than the older Agent class contract used by the submission runner.
    """
    module = _load_agent_module(agent_path)
    function = getattr(module, "get_reinforcements", None)
    if not callable(function) or getattr(function, "__module__", None) != module.__name__:
        raise ValueError(
            "Define a module-level `get_reinforcements(state: dict)` function in agent.py."
        )
    return function


def _load_agent_module(agent_path: str):
    import importlib.util
    import uuid

    module_name = f"_participant_agent_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, agent_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load agent file: {agent_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ImportError(f"agent.py raised on import: {exc!r}") from exc
    return module
