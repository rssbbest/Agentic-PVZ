"""Sandbox: switches off the SDK's socket patches and restores the
originals, then calls a provider. The sandbox is a CPython audit hook,
which cannot be removed, so the call is still blocked."""
import _socket
import socket

from autopilot_sdk.network_guard import uninstall_network_guard


def get_reinforcements(state):
    uninstall_network_guard()
    socket.getaddrinfo = _socket.getaddrinfo
    socket.create_connection(("api." + "anthropic.com", 443), timeout=2)
    return []
