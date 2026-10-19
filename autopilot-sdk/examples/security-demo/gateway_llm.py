"""Control: the sanctioned LLM path. Calls the organiser's gateway through
LLMClient; the sandbox lets the gateway host through."""
import json
import os

from autopilot_sdk.llm import LLMClient


def get_reinforcements(state):
    client = LLMClient(api_key="", base_url=os.environ["AUTOPILOT_GATEWAY_URL"])
    reply = client.chat.completions.create(messages=[{"role": "user", "content": json.dumps(state["enemies"])}], timeout=1.5)
    return json.loads(reply["choices"][0]["message"]["content"])["placements"]
