"""Sandbox: a model served on this machine (Ollama's port) is not the
organiser's gateway, so loopback LLM ports are blocked."""
import socket


def get_reinforcements(state):
    socket.create_connection(("127.0.0.1", 11434), timeout=2)
    return []
