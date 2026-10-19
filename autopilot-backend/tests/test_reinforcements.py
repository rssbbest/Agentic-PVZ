"""
tests/test_reinforcements.py
────────────────────────────
Unit tests for mid-run reinforcement validation logic.
Verifies referee rules:
1. No nudging — invalid or occupied placements are strictly skipped.
2. Boundary and prefab validation (0..10, 0..5, prefabId in 0..3).
3. No heuristic fallback — if no valid placements, placeDefences=False.
4. Valid candidate placements are approved.
"""
import unittest

from app.schemas import (
    DefencePlacement,
    GridPosition,
    ReinforcementCounts,
    ReinforcementDefenceUnit,
    ReinforcementEnemyUnit,
    ReinforcementRequest,
    ReinforcementResponse,
    UnitPosition,
)
from app.services.reinforcement_service import (
    GRID_MAX_X,
    GRID_MAX_Y,
    GRID_MIN_X,
    GRID_MIN_Y,
    validate_reinforcements,
)


class TestReinforcementService(unittest.TestCase):
    def setUp(self):
        # Budget figures in these tests assume 1000 starting coins.
        from unittest import mock
        patcher = mock.patch("app.services.evaluator_service.STARTING_COINS", 1000.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.base_req = ReinforcementRequest(
            teamID="TEAM_TEST",
            sessionID="sess123",
            elapsedSeconds=10.0,
            budget=800.0,
            unlockedBudget=800.0,
            counts=ReinforcementCounts(
                activeDefenceCount=1,
                activeEnemyCount=2,
                enemiesQueued=2,
                totalEnemiesInWave=5,
            ),
            defences=[
                ReinforcementDefenceUnit(
                    id="def_0",
                    type="defenceSoilder",
                    health=100.0,
                    maxHealth=100.0,
                    position=UnitPosition(x=1, y=1),
                )
            ],
            enemies=[
                ReinforcementEnemyUnit(
                    id="enemy_0",
                    type="EnemClown",
                    health=80.0,
                    maxHealth=100.0,
                    position=UnitPosition(x=5, y=1),
                    isAttacking=False,
                    isDead=False,
                )
            ],
        )

    def test_occupied_cell_is_skipped_no_nudging(self):
        """Rule 1: If placement is on occupied cell (1, 1), it must be skipped, NOT nudged."""
        candidate = DefencePlacement(
            defenceId=0,
            defenceGridPosition=GridPosition(x=1, y=1),  # already occupied
        )
        approved = validate_reinforcements([candidate], self.base_req)
        # Must be empty - no nudging to (2, 1) or anywhere else
        self.assertEqual(len(approved), 0)

    def test_boundary_validation_rejects_out_of_bounds(self):
        """Rule 2: Out of bounds (e.g. x=11, y=6, negative) must be skipped."""
        candidates = [
            {"defenceId": 0, "defenceGridPosition": {"x": 11, "y": 2}},
            {"defenceId": 1, "defenceGridPosition": {"x": 3, "y": 6}},
            {"defenceId": 2, "defenceGridPosition": {"x": -1, "y": 2}},
        ]
        approved = validate_reinforcements(candidates, self.base_req)
        self.assertEqual(len(approved), 0)

    def test_prefab_id_validation_rejects_invalid_id(self):
        """Rule 2: Prefab ID must be 0, 1, 2, or 3."""
        candidates = [
            {"defenceId": 4, "defenceGridPosition": {"x": 2, "y": 2}},
            {"defenceId": -1, "defenceGridPosition": {"x": 3, "y": 2}},
        ]
        approved = validate_reinforcements(candidates, self.base_req)
        self.assertEqual(len(approved), 0)

    def test_valid_placement_is_approved(self):
        """A valid placement on an empty tile within bounds is approved."""
        candidate = DefencePlacement(
            defenceId=1,
            defenceGridPosition=GridPosition(x=2, y=2),
        )
        approved = validate_reinforcements([candidate], self.base_req)
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0].defenceId, 1)
        self.assertEqual(approved[0].defenceGridPosition.x, 2)
        self.assertEqual(approved[0].defenceGridPosition.y, 2)

    def test_no_duplicate_placements_in_same_batch(self):
        """If two placements target the same empty cell, only the first is accepted."""
        candidates = [
            DefencePlacement(defenceId=0, defenceGridPosition=GridPosition(x=2, y=2)),
            DefencePlacement(defenceId=1, defenceGridPosition=GridPosition(x=2, y=2)),
        ]
        approved = validate_reinforcements(candidates, self.base_req)
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0].defenceId, 0)

    def test_budget_rejection_when_exceeding_budget(self):
        """Rule 5: Placements exceeding remaining budget must be strictly rejected."""
        req = self.base_req.model_copy(deep=True)
        # Already 1 soldier on board (cost 200)
        req.budget = 300.0
        req.unlockedBudget = 300.0
        req.spent = 200.0
        # Candidate soldier costs 200 (200 + 200 = 400 > 300)
        candidate = DefencePlacement(
            defenceId=1,  # Soldier costs 200
            defenceGridPosition=GridPosition(x=2, y=2),
        )
        approved = validate_reinforcements([candidate], req)
        self.assertEqual(len(approved), 0)

    def test_partial_batch_allowed_until_budget_exhausted(self):
        """Rule 5: First affordable placements approved; subsequent over-budget placements rejected."""
        req = self.base_req.model_copy(deep=True)
        req.budget = 400.0
        req.unlockedBudget = 400.0
        req.spent = 200.0  # Remaining = 200.0
        candidates = [
            DefencePlacement(defenceId=3, defenceGridPosition=GridPosition(x=2, y=2)),  # Shield costs 150 (spent becomes 350)
            DefencePlacement(defenceId=1, defenceGridPosition=GridPosition(x=3, y=3)),  # Soldier costs 200 (350 + 200 = 550 > 400, rejected)
        ]
        approved = validate_reinforcements(candidates, req)
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0].defenceId, 3)
        self.assertEqual(approved[0].defenceGridPosition.x, 2)
        self.assertEqual(approved[0].defenceGridPosition.y, 2)

    def test_income_check_uses_unlocked_budget_when_provided(self):
        """Income check verifies unlockedBudget ceiling even when matchBudget is larger."""
        req = self.base_req.model_copy(deep=True)
        req.matchBudget = 800.0
        req.unlockedBudget = 300.0  # Only 300 unlocked so far
        req.spent = 200.0           # 100 remaining unlocked
        candidate = DefencePlacement(
            defenceId=1,  # Soldier costs 200 (> 100 remaining)
            defenceGridPosition=GridPosition(x=2, y=2),
        )
        approved = validate_reinforcements([candidate], req)
        self.assertEqual(len(approved), 0)

    def test_income_check_approves_when_within_unlocked_budget(self):
        """Income check approves placement when cost fits within unlockedBudget."""
        req = self.base_req.model_copy(deep=True)
        req.matchBudget = 800.0
        req.unlockedBudget = 400.0  # 400 unlocked
        req.spent = 200.0           # 200 remaining unlocked
        candidate = DefencePlacement(
            defenceId=3,  # Shield costs 150 (<= 200 remaining)
            defenceGridPosition=GridPosition(x=2, y=2),
        )
        approved = validate_reinforcements([candidate], req)
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0].defenceId, 3)

    def test_cumulative_session_expenditure_enforcement(self):
        """Referee enforces cumulative spending across pings, preventing dead-unit refunds."""
        import asyncio
        from app.services.reinforcement_service import (
            clear_session_ledger,
            decide_reinforcements,
            record_session_initial_defences,
        )

        sess_id = "test_sess_cumul_1"
        clear_session_ledger(sess_id)

        # Initial turn 0: 4 soldiers and a shield (cost 950) out of the 1000 starting coins
        record_session_initial_defences(sess_id, [{"defenceId": 1}] * 4 + [{"defenceId": 3}])

        # Snapshot with 20 enemies: match budget 400 + 20 * 60 = 1600
        req = self.base_req.model_copy(deep=True)
        req.sessionID = sess_id
        req.counts.totalEnemiesInWave = 20
        req.counts.enemiesQueued = 20
        req.counts.activeEnemyCount = 0
        req.candidatePlacements = [DefencePlacement(defenceId=3, defenceGridPosition=GridPosition(x=3, y=3))]

        async def _run():
            # Ping 1: 0 kills, unlocked = 1000 starting coins. Spent 950 on Turn 0.
            # Candidate Shield costs 150 (950 + 150 = 1100 > 1000) -> rejected
            resp1 = await decide_reinforcements(req)
            self.assertFalse(resp1.placeDefences)

            # Ping 2: 2 enemies killed -> bounty +100 -> unlocked budget is 1100
            # Spent so far is 950. Remaining unlocked = 150.
            # Candidate Shield costs 150 (<= 150) -> approved!
            req.counts.enemiesQueued = 0
            req.counts.activeEnemyCount = 18
            req.candidatePlacements = [DefencePlacement(defenceId=3, defenceGridPosition=GridPosition(x=3, y=3))]
            resp2 = await decide_reinforcements(req)
            self.assertTrue(resp2.placeDefences)
            self.assertEqual(len(resp2.defencePlacementData or []), 1)

            # Ping 3: All previous units on the board die! Snapshot defences is empty []
            # Total spent so far is 950 (turn 0) + 150 (ping 2) = 1100.
            # Unlocked budget is 1100. Remaining budget = 0.
            # Candidate Soldier costs 200 (> 0) -> MUST BE REJECTED even with empty board!
            req.defences = []
            req.candidatePlacements = [DefencePlacement(defenceId=1, defenceGridPosition=GridPosition(x=4, y=4))]
            resp3 = await decide_reinforcements(req)
            self.assertFalse(resp3.placeDefences)

            clear_session_ledger(sess_id)

        asyncio.run(_run())

    def test_every_kill_pays_out_up_to_the_last_enemy(self):
        from app.services.evaluator_service import (
            BOUNTY_PER_KILL, STARTING_COINS, calculate_test_case_budget, calculate_unlocked_budget,
        )

        total = calculate_test_case_budget(None, 12)
        unlocked = [calculate_unlocked_budget(total, kills) for kills in range(13)]
        self.assertEqual(unlocked[0], STARTING_COINS)
        self.assertEqual({b - a for a, b in zip(unlocked, unlocked[1:])}, {BOUNTY_PER_KILL})
        self.assertEqual(unlocked[-1], total)

    def test_budget_scales_with_enemy_count_not_enemy_types(self):
        from app.services.evaluator_service import calculate_test_case_budget, wave_size

        wave = [{"enemyId": 0, "count": 6}, {"enemyId": 1, "count": 4}]
        self.assertEqual(wave_size(wave), 10)
        # Same figure the referee derives from the game's totalEnemiesInWave.
        self.assertEqual(calculate_test_case_budget(None, wave_size(wave)),
                         calculate_test_case_budget(None, 10))

    def test_session_placements_include_turn0_and_every_approved_reinforcement(self):
        import asyncio
        from app.services.reinforcement_service import (
            clear_session_ledger,
            decide_reinforcements,
            record_session_initial_defences,
            session_placements,
        )

        sess_id = "test_sess_ledger_1"
        clear_session_ledger(sess_id)
        record_session_initial_defences(sess_id, [{"defenceId": 1, "defenceGridPosition": {"x": 0, "y": 0}}])
        req = self.base_req.model_copy(deep=True)
        req.sessionID = sess_id
        req.counts.totalEnemiesInWave = 10
        req.counts.enemiesQueued = 10
        req.counts.activeEnemyCount = 0
        req.candidatePlacements = [DefencePlacement(defenceId=3, defenceGridPosition=GridPosition(x=3, y=3))]
        asyncio.run(decide_reinforcements(req))
        placed = session_placements(sess_id)
        self.assertEqual([p["defenceId"] for p in placed], [1, 3])
        clear_session_ledger(sess_id)
        self.assertEqual(session_placements(sess_id), [])


if __name__ == "__main__":
    unittest.main()
