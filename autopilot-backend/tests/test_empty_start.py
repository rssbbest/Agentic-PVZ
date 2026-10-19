"""Levels start with an empty board unless an organiser sets starting defences."""
from app.schemas import TeamDataUpsert
from app.services.seed_data import build_default_team_data

ENEMIES = [{"enemyId": 0, "count": 3}]


def test_default_level_has_no_starting_defences():
    assert build_default_team_data(team_id=1).defence_placement_data == []


def test_admin_upsert_accepts_an_empty_or_missing_board():
    assert TeamDataUpsert(defencePlacementData=[], enemies=ENEMIES).defencePlacementData == []
    assert TeamDataUpsert(enemies=ENEMIES).defencePlacementData == []
    placed = TeamDataUpsert(
        defencePlacementData=[{"defenceId": 3, "defenceGridPosition": {"x": 1, "y": 2}}],
        enemies=ENEMIES,
    )
    assert placed.defencePlacementData[0].defenceId == 3
