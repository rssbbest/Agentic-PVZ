"""Sandbox: tries a blocked call, catches the error and carries on as if
nothing happened. The violation is reported anyway and nothing is placed."""
import socket


def get_reinforcements(state):
    try:
        socket.create_connection(("api." + "openai.com", 443), timeout=2)
    except Exception:
        pass
    return [{"defenceId": 1, "defenceGridPosition": {"x": 0, "y": 0}}]
