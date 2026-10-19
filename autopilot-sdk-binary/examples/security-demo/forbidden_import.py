"""Static layer: imports a provider SDK. `autopilot verify` and
`autopilot play` reject the file before it ever runs."""
import anthropic


def get_reinforcements(state):
    anthropic.Anthropic().messages.create(model="x", max_tokens=1, messages=[])
    return []
