"""Autopilot SDK.

- Live reinforcement agents define `get_reinforcements(state)` in agent.py.
- Legacy submissions can subclass `Agent` and implement place_defences().
- Organisers run `autopilot serve`; the backend calls POST /reinforcements.

LLM calls: autopilot_sdk.llm.LLMClient (standard chat completions), tools: autopilot_sdk.tools.
"""

from autopilot_sdk.contracts.agent_base import Agent

__version__ = "0.3.0"
__all__ = ["Agent", "__version__"]
