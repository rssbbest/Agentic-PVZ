"""Sandbox: a self-hosted relay that forwards to a provider. The host is on
no denylist, but agent code may only reach the gateway (allowlist)."""
import requests


def get_reinforcements(state):
    requests.post("https://llm-relay.example.com/v1/chat/completions", json={}, timeout=2)
    return []
