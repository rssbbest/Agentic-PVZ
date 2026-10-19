"""Fair play: a game's report is checked against what the backend knows, and
every game of a test case spawns the same way."""
from datetime import timedelta

from app.services import competition

LEVEL = {"enemies": [{"enemyId": 0, "count": 4}],
         "spawnSettings": {"interArrivalTime": 5.0, "timeDecreaseRate": 0.0, "minInterArrivalTime": 1.0},
         "gameSettings": {"reinforcementsEnabled": True, "reinforcementIntervalSeconds": 10.0}}
APPROVED = [{"defenceId": 1, "defenceGridPosition": {"x": 1, "y": 2}},
            {"defenceId": 3, "defenceGridPosition": {"x": 2, "y": 2}}]


def report(success=True, killed=4, duration=40.0, defences=((1, 2),), damage=10.0):
    return {"success": success, "enemiesKilled": killed, "durationSeconds": duration,
            "defences": [{"id": f"d{i}", "position": {"x": x, "y": y}, "damageDealt": damage}
                         for i, (x, y) in enumerate(defences)]}


def problem(result, real_s=60.0, pauses=4, approved=APPROVED, started_before_restart=False):
    started = competition.PROCESS_STARTED + (timedelta(seconds=-3600) if started_before_restart
                                              else timedelta(seconds=1))
    return competition.result_problem(started, LEVEL, result, pauses, approved,
                                      finished_at=started + timedelta(seconds=real_s))


def test_an_honest_game_passes():
    assert problem(report()) is None
    assert problem(report(success=False, killed=1, duration=12.0), pauses=1) is None


def test_a_made_up_win_is_refused():
    assert "started 5 s ago" in problem(report(duration=40.0), real_s=5.0)        # faster than the clock
    assert "wave has 4 enemies" in problem(report(killed=9))
    assert "all 4 enemies killed" in problem(report(killed=3))                    # a win kills the whole wave
    assert "takes at least" in problem(report(duration=4.0))                      # the wave can't spawn that fast
    assert "saw none" in problem(report(), pauses=0)                              # no game reached the backend
    assert "saw 1 pauses" in problem(report(duration=60.0), pauses=1)             # 60 s with 10 s pauses: ~6
    assert "never approved" in problem(report(defences=((5, 5),)))               # a unit the referee didn't place
    assert "never approved" in problem(report(defences=((1, 2), (1, 2))))         # one approval, two units


def test_a_shield_destroyed_before_it_hit_anything_still_counts():
    # build10 logs such a defence at (0, 0): it uses up one approval, never more
    assert problem(report(defences=((1, 2), (0, 0)), damage=0.0)) is None
    assert "more defences" in problem(report(defences=((1, 2), (0, 0), (0, 0)), damage=0.0))
    assert "never approved" in problem(report(defences=((0, 0),), damage=50.0))


def test_after_a_backend_restart_only_the_level_checks_apply():
    assert problem(report(), pauses=0, approved=[], started_before_restart=True) is None
    assert "all 4 enemies" in problem(report(killed=2), started_before_restart=True)


def test_every_game_of_a_test_case_gets_the_same_seed_and_its_own_interval():
    assert competition.case_seed("TC03") == competition.case_seed("tc03 ")
    assert competition.case_seed("TC03") != competition.case_seed("TC04")
    reply = competition.level_reply("cTEST", [], LEVEL["enemies"], LEVEL["spawnSettings"],
                                    LEVEL["gameSettings"], seed=competition.case_seed("TC03"))
    assert reply.spawnSettings.seed == competition.case_seed("TC03")
    assert reply.gameSettings.reinforcementIntervalSeconds == 10.0   # the level's interval, not capped
    own = competition.level_reply("cTEST2", [], LEVEL["enemies"], {**LEVEL["spawnSettings"], "seed": 77},
                                  LEVEL["gameSettings"], seed=5)
    assert own.spawnSettings.seed == 77                              # a level may pin its own seed


def test_a_level_seed_is_validated():
    import pytest
    from fastapi import HTTPException

    level = {**LEVEL, "spawnSettings": {**LEVEL["spawnSettings"], "seed": 12}}
    assert competition.normalise_level(level)["spawnSettings"]["seed"] == 12
    with pytest.raises(HTTPException):
        competition.normalise_level({**LEVEL, "spawnSettings": {"seed": -1}})
