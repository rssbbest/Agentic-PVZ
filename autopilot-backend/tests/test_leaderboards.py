"""Public and private leaderboards: ranking rules and the change signal (no database needed)."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from app.services import leaderboards

T0 = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
TEAMS = {i: NS(name=f"Team {i}") for i in (1, 2, 3, 4)}


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def run(id, team, score, minutes, status="finished", codes=("H1", "H2")):
    return NS(id=id, team_id=team, score=score, status=status, finished_at=at(minutes), case_codes=list(codes))


RUNS = [run(1, 1, 180, 1), run(2, 1, 120, 5),          # team 1: best 180, latest 120
        run(3, 2, 150, 2, "incomplete"),                 # incomplete never counts: only full runs
        run(4, 3, 199, 3, "abandoned"), run(5, 3, 140, 4)]  # abandoned never counts


def test_public_board_takes_each_teams_best_published_run_with_its_case_scores():
    runs = [run(1, 1, 170, 9, codes=("TC01", "TC02")), run(2, 1, 120, 12, codes=("TC01", "TC02")),
            run(3, 2, 170, 4, codes=("TC01", "TC02")),     # same total, reached earlier: ranks first
            run(4, 3, 100, 1, codes=("TC01", "TC02")),
            run(5, 9, 200, 1, codes=("TC01", "TC02"))]     # not an eligible team: left out
    scores = {1: {"TC01": 90.0, "TC02": 80.0}, 3: {"TC01": 85.0, "TC02": 85.0}, 4: {"TC01": 100.0}}
    rows = leaderboards.rank_runs(runs, {1: 2, 2: 2, 3: 2, 4: 1}, TEAMS, "best", scores)
    assert [(r["teamID"], r["rank"], r["score"], r["evalId"]) for r in rows] == [
        ("0002", 1, 170.0, 3), ("0001", 1, 170.0, 1), ("0003", 3, 100.0, 4)]
    assert rows[1]["evals"] == 2 and rows[2]["played"] == 1 and rows[2]["totalCases"] == 2
    assert rows[2]["cases"] == {"TC01": 100.0, "TC02": None}


def test_private_rule_latest_takes_each_teams_latest_run():
    rows = leaderboards.rank_runs(RUNS, played={2: 2, 5: 2}, teams=TEAMS, rule="latest")
    assert [(r["teamID"], r["score"], r["evalId"]) for r in rows] == [("0003", 140.0, 5), ("0001", 120.0, 2)]
    assert rows[1]["evals"] == 2 and rows[0]["played"] == 2 and rows[0]["status"] == "finished"
    assert "cases" not in rows[0]


def test_private_rule_best_takes_each_teams_best_run():
    rows = leaderboards.rank_runs(RUNS, played={}, teams=TEAMS, rule="best")
    assert [(r["teamID"], r["score"], r["rank"]) for r in rows] == [("0001", 180.0, 1), ("0003", 140.0, 2)]


def test_hidden_evals_stay_closed_and_the_best_run_counts_until_the_organisers_say_otherwise():
    assert leaderboards.DEFAULT_SETTINGS["evalOpen"] is False
    assert leaderboards.DEFAULT_SETTINGS["privateRule"] == "best"
    assert leaderboards.COUNTED_EVALS == ("finished",)


def test_viewers_wake_on_a_change_and_never_miss_one():
    async def scenario():
        since = leaderboards.version()
        waiter = asyncio.create_task(leaderboards.wait_for_change(since, timeout=5))
        await asyncio.sleep(0)
        leaderboards.changed()
        await asyncio.wait_for(waiter, 1)
        # A change between two waits is not lost: the next wait returns at once.
        since = leaderboards.version()
        leaderboards.changed()
        await asyncio.wait_for(leaderboards.wait_for_change(since, timeout=5), 1)

    asyncio.run(scenario())


def test_a_settings_change_is_never_hidden_by_the_debounce():
    import time

    leaderboards.changed()
    fresh = (leaderboards.version(), time.monotonic(), {"published": False})
    assert leaderboards._fresh(fresh)                       # just built: served
    leaderboards.changed()
    assert leaderboards._fresh(fresh)                       # a game finished: debounced for a second
    leaderboards.changed(force=True)
    assert not leaderboards._fresh(fresh)                   # published/unpublished: rebuilt at once
