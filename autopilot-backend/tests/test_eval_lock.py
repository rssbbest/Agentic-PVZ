"""PUBLISH: a private run is locked to one agent.py; the same file resumes it."""
from types import SimpleNamespace as NS

from app.services.competition import eval_start_decision

A, B = "a" * 64, "b" * 64


def run(id, status, sha=A, score=None):
    return NS(id=id, status=status, agent_sha256=sha, score=score)


def test_first_private_run_starts_new():
    assert eval_start_decision("private", [], A)[0] == "new"
    assert eval_start_decision("private", [run(1, "void", B)], A)[0] == "new"  # an organiser lifted the lock


def test_stopping_mid_run_and_restarting_with_the_same_file_resumes():
    action, chosen, _ = eval_start_decision("private", [run(7, "running")], A)
    assert (action, chosen.id) == ("resume", 7)


def test_a_run_the_organisers_closed_is_final():
    action, chosen, why = eval_start_decision("private", [run(7, "incomplete", score=120)], A)
    assert (action, chosen.id) == ("refuse", 7) and "closed" in why


def test_restarting_with_a_different_agent_is_refused():
    action, chosen, why = eval_start_decision("private", [run(7, "running")], B)
    assert action == "refuse" and chosen.id == 7 and A[:12] in why and B[:12] in why


def test_a_finished_private_run_cannot_be_played_again():
    for sha in (A, B):
        action, _, why = eval_start_decision("private", [run(7, "finished", score=412.5)], sha)
        assert action == "refuse"
    assert "complete" in eval_start_decision("private", [run(7, "finished", score=412.5)], A)[2]


def test_an_old_run_with_another_agent_keeps_the_lock_even_after_a_void_one():
    runs = [run(3, "abandoned", B), run(5, "void", A)]
    assert eval_start_decision("private", runs, A)[0] == "refuse"
    assert eval_start_decision("private", runs, B)[0] == "resume"


def test_public_runs_resume_the_same_file_and_otherwise_start_again():
    assert eval_start_decision("public", [run(2, "running")], A)[:2] == ("resume", run(2, "running"))
    assert eval_start_decision("public", [run(2, "running")], B)[0] == "new"
    assert eval_start_decision("public", [run(2, "finished", score=300)], A)[0] == "new"
