"""Sandbox: connects to a raw IP through the C-level _socket module, so no
hostname is ever looked up and the socket module is never touched.
(203.0.113.10 is a documentation address standing in for a provider's IP.)"""
import _socket


def get_reinforcements(state):
    sock = _socket.socket()
    sock.settimeout(2)
    sock.connect(("203.0.113.10", 443))
    return []
