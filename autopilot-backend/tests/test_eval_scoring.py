"""A run's score: one reading of its games for the participant, the admin and the boards."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from app.services import competition


def game(slot, score, won=True):
    return NS(eval_slot=slot, score=score, success=won)


def test_the_first_game_that_finished_a_test_case_counts():
    slots = competition.slot_results([game(1, 40.5), game(2, 12.25, False), game(1, 99.0)], 3)
    assert slots == [{"slot": 1, "played": True, "score": 40.5, "won": True},
                     {"slot": 2, "played": True, "score": 12.25, "won": False},
                     {"slot": 3, "played": False, "score": None, "won": None}]


def test_a_run_scores_the_sum_and_a_case_not_played_counts_zero():
    assert competition.run_total(competition.slot_results([game(1, 40.5), game(2, 12.25)], 2)) == (52.75, "finished")
    assert competition.run_total(competition.slot_results([game(2, 0.004)], 3)) == (0.0, "incomplete")
    assert competition.run_total(competition.slot_results([], 2)) == (0.0, "incomplete")


class FakeDB:
    """Just enough of an AsyncSession for record_result."""

    def __init__(self, *rows):
        self.rows = {(type(r).__name__, getattr(r, "session_id", None) or r.id): r for r in rows}
        self.commits = 0

    async def get(self, model, key):
        return self.rows.get((model.__name__, key))

    async def commit(self):
        self.commits += 1


def _eval_setup(monkeypatch, played_before):
    from app.models import EvalRun, GameSession

    run = EvalRun(id=7, team_id=1, board="private", status="running", case_codes=["H1", "H2"])
    finished = [GameSession(session_id=f"s{i}", eval_id=7, eval_slot=i, score=10.0 * i, success=True,
                            finished_at=datetime(2026, 10, 17, tzinfo=timezone.utc)) for i in played_before]
    last = GameSession(session_id="new", team_id=1, test_case="H2", mode="eval", eval_id=7, eval_slot=2)

    async def progress(db, r):
        done = [g for g in finished + [last] if g.finished_at is not None]
        return competition.slot_results(done, len(r.case_codes))

    monkeypatch.setattr(competition, "eval_progress", progress)
    return run, last, FakeDB(run, last)


def test_a_runs_last_game_scores_the_run_even_if_the_sdk_never_reports_it(monkeypatch):
    run, last, db = _eval_setup(monkeypatch, played_before=[1])
    asyncio.run(competition.record_result(db, "new", {"success": True}, {}, 35.5, 3, []))
    assert (run.status, run.score) == ("finished", 45.5) and run.finished_at is not None


def test_a_game_that_is_not_the_last_leaves_the_run_running(monkeypatch):
    run, last, db = _eval_setup(monkeypatch, played_before=[])
    asyncio.run(competition.record_result(db, "new", {"success": False}, {}, 20.0, 1, []))
    assert (run.status, run.score) == ("running", None) and last.score == 20.0
