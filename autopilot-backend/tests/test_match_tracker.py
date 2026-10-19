"""The game's report is completed from the snapshots, and scoring uses the true lists."""
import unittest

from app.services import match_tracker
from app.services.evaluator_service import calculate_score


def snapshot(sid, t, defences, enemies):
    return {"sessionID": sid, "elapsedSeconds": t, "defences": defences, "enemies": enemies}


def defence(uid, kind, x, y, health=100):
    return {"id": uid, "type": kind, "position": {"x": x, "y": y}, "health": health}


def enemy(uid, x, y, health=50, dead=False):
    return {"id": uid, "type": "enemyGrunt", "position": {"x": x, "y": y}, "health": health, "isDead": dead}


class MatchTrackerTests(unittest.TestCase):
    def tearDown(self):
        match_tracker._SESSIONS.clear()

    def test_units_missing_from_report_are_added_from_snapshots(self):
        sid = "s1"
        match_tracker.record_snapshot(snapshot(sid, 3.0, [defence("d1", "defenceSoilder", 2, 1),
                                                          defence("d2", "defenceArcher", 3, 4)],
                                               [enemy("e1", 8, 1), enemy("e2", 9, 4)]))
        match_tracker.record_snapshot(snapshot(sid, 9.0, [defence("d1", "defenceSoilder", 2, 1)],
                                               [enemy("e1", 4, 1, health=0, dead=True), enemy("e2", 6, 4)]))
        # The game reported only the defence that fought and the enemy it killed.
        report = {"sessionID": sid, "enemiesKilled": 1, "defencesDestroyed": 0,
                  "defences": [{"id": "d1", "type": "defenceSoilder", "position": {"x": 2, "y": 1},
                                "damageDealt": 50.0, "destroyed": False}],
                  "enemies": [{"id": "e1", "type": "enemyGrunt", "damageDealt": 0.0, "killed": True}],
                  "defenceTypeStats": [{"type": "defenceSoilder", "unitCount": 1}]}
        approved = [{"defenceId": 1, "defenceGridPosition": {"x": 2, "y": 1}},   # = d1
                    {"defenceId": 3, "defenceGridPosition": {"x": 5, "y": 2}}]   # placed after the last snapshot
        done, note = match_tracker.complete_results(sid, report, approved)

        self.assertEqual(sorted(d["id"] for d in done["defences"]), ["d1", "d2", "placed_5_2"])
        self.assertEqual({e["id"]: e["killed"] for e in done["enemies"]}, {"e1": True, "e2": False})
        self.assertEqual(done["enemiesKilled"], 1)
        self.assertNotIn("defenceTypeStats", done)
        self.assertEqual(note, {"reportedDefences": 1, "reportedEnemies": 1,
                                "addedDefences": 2, "addedEnemies": 1})

    def test_enemy_seen_dead_counts_as_killed(self):
        match_tracker.record_snapshot(snapshot("s2", 5.0, [], [enemy("e1", 3, 3, health=0)]))
        match_tracker.record_snapshot(snapshot("s2", 6.0, [], []))
        done, _ = match_tracker.complete_results("s2", {"enemies": [], "defences": []})
        self.assertEqual(done["enemiesKilled"], 1)

    def test_opening_call_and_unknown_sessions_are_ignored(self):
        match_tracker.record_snapshot(snapshot("s3", 0, [defence("d1", "defenceSoilder", 1, 1)], []))
        match_tracker.record_snapshot(snapshot("", 4.0, [defence("d1", "defenceSoilder", 1, 1)], []))
        report = {"defences": [], "enemies": []}
        done, note = match_tracker.complete_results("s3", report)
        self.assertEqual(done["defences"], [])
        self.assertEqual(note["addedDefences"], 0)

    def test_forget_drops_the_session(self):
        match_tracker.record_snapshot(snapshot("s4", 2.0, [defence("d1", "defenceSoilder", 1, 1)], []))
        match_tracker.forget("s4")
        self.assertNotIn("s4", match_tracker._SESSIONS)


class ClearedWaveScoringTests(unittest.TestCase):
    def _result(self, killed, total, success, duration):
        return {"success": success, "enemiesKilled": killed, "durationSeconds": duration,
                "defencesDestroyed": 0,
                "defences": [{"id": f"d{i}", "type": "defenceSoilder", "destroyed": False} for i in range(3)],
                "enemies": [{"id": f"e{i}", "type": "enemyGrunt", "killed": i < killed} for i in range(total)]}

    def test_a_loss_without_kills_scores_zero(self):
        score, breakdown, _ = calculate_score(self._result(0, 19, False, 6.0),
                                              test_case_budget=1500, expected_total_enemies=19)
        self.assertEqual(score, 0.0)
        self.assertEqual(breakdown["outcome_score"], 0.0)

    def test_a_loss_scores_only_its_kills(self):
        score, half, _ = calculate_score(self._result(10, 20, False, 60.0),
                                         test_case_budget=1500, expected_total_enemies=20)
        self.assertEqual(half["kill_score"], 12.5)
        self.assertEqual({k: v for k, v in half.items() if k != "kill_score"},
                         {"outcome_score": 0.0, "survival_score": 0.0, "speed_bonus": 0.0, "cost_score": 0.0})
        self.assertEqual(score, 12.5)
        _, win, _ = calculate_score(self._result(20, 20, True, 60.0),
                                    test_case_budget=1500, expected_total_enemies=20)
        self.assertEqual(win["outcome_score"], 25.0)
        self.assertGreater(win["survival_score"], 0)


if __name__ == "__main__":
    unittest.main()


def test_session_and_team_numbers_beyond_int32_are_not_database_ids():
    from app.services.reinforcement_service import db_id

    assert db_id("0004") == 4 and db_id(" 12 ") == 12
    # A free-play session such as "79439564601" used to reach Postgres as an
    # int32 run id and fail every pause of that game with HTTP 500.
    assert db_id("79439564601") is None
    assert db_id("p1a2b3c4d5e6") is None and db_id("") is None and db_id("0") is None
