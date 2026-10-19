"""Limits: never returns. The worker is killed at the time limit and the
game resumes."""
import time


def get_reinforcements(state):
    while True:
        time.sleep(1)
