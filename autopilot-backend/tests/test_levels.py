"""Levels are checked before they can reach the game (it trusts every value)."""
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.competition import level_reply, normalise_level

ONE = [{"enemyId": 1, "count": 1}]


@pytest.mark.parametrize("level", [
    {"enemies": [{"enemyId": 9, "count": 1}]},
    {"enemies": [{"enemyId": 1, "count": 1}, {"enemyId": 1, "count": 2}]},
    {"enemies": [{"enemyId": 1, "count": 1, "allowedRows": [7]}]},
    {"enemies": [{"enemyId": "1", "count": 1}]},
    {"enemies": ONE, "spawnSettings": {"interArrivalTime": "fast"}},
    {"enemies": ONE, "gameSettings": {"reinforcementsEnabled": "yes"}},
    {"enemies": ONE, "defencePlacementData": [{"defenceId": 1, "defenceGridPosition": {"x": 1, "y": 1}},
                                              {"defenceId": 2, "defenceGridPosition": {"x": 1, "y": 1}}]},
    {"enemies": ONE, "defencePlacementData": [{"defenceId": 1, "defenceGridPosition": {"x": 11, "y": 0}}]},
    {"enemies": ONE, "defencePlacementData": [{"defenceId": 4, "defenceGridPosition": {"x": 1, "y": 0}}]},
    {"enemies": ONE, "spawnSettings": {"spawnSequence": [1, 1]}},
    {"enemies": ONE, "spawnSettings": {"spawnSequence": [2]}},
])
def test_bad_levels_are_refused(level):
    with pytest.raises(HTTPException) as err:
        normalise_level(level)
    assert err.value.status_code == 422


def test_every_shipped_level_is_valid():
    folder = Path(__file__).resolve().parents[1] / "test_cases"
    for name in ("reinforcement_test_cases.json", "hidden_test_cases.json"):
        data = json.loads((folder / name).read_text())
        for case in data if isinstance(data, list) else next(iter(data.values())):
            normalise_level(case.get("level", case))


def test_reply_sends_a_spawn_sequence_only_when_set_and_any_row_when_rows_missing():
    level = normalise_level({"enemies": [{"enemyId": 1, "count": 2}, {"enemyId": 3, "count": 1}],
                             "spawnSettings": {"spawnSequence": [3, 1]}})
    reply = level_reply("t1", [], level["enemies"], level["spawnSettings"], level["gameSettings"]).model_dump(mode="json")
    assert reply["spawnSettings"]["spawnSequence"] == [3, 1]
    assert reply["enemies"][0]["allowedRows"] == [0, 1, 2, 3, 4, 5]
    plain = level_reply("t2", [], ONE, None, None).model_dump(mode="json")
    assert "spawnSequence" not in plain["spawnSettings"] and isinstance(plain["coins"], int)
