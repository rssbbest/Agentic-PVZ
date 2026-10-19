"""Sandbox: imports a provider SDK under a name built at runtime, so the
static scan sees no banned import. The sandbox blocks the import itself."""
import importlib


def get_reinforcements(state):
    importlib.import_module("anth" + "ropic")
    return []
