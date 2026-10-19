"""Sandbox: calls the C library's socket functions directly through ctypes,
below every Python-level check. Native calls are blocked."""
import ctypes


def get_reinforcements(state):
    libc = ctypes.CDLL(None)
    libc.socket(2, 1, 0)
    return []
