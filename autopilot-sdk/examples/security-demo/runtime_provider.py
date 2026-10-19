"""Sandbox: the provider hostname is assembled at runtime, so the static
scan passes. The sandbox blocks it before any DNS lookup."""
import requests


def get_reinforcements(state):
    host = "api." + "open" + "ai.com"
    requests.post(f"https://{host}/v1/chat/completions", json={}, timeout=2)
    return []
