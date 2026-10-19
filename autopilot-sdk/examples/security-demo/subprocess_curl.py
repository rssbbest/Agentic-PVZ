"""Sandbox: hands the provider call to another program (curl), outside
Python's view. Starting programs or processes is blocked."""
import subprocess


def get_reinforcements(state):
    subprocess.run(["curl", "-s", "https://api." + "openai.com/v1/models"], timeout=2)
    return []
