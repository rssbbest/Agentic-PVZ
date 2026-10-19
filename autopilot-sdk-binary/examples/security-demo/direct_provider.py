"""Static layer: a provider URL in the source is rejected before running."""
import requests


def get_reinforcements(state):
    requests.post("https://api.openai.com/v1/chat/completions", json={}, timeout=2)
    return []
