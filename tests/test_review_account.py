"""评审账号预置的事务、隔离、重复执行和实际登录检查；不调用供应商。"""
import datetime as dt
import json
import unittest
from unittest.mock import patch

from sqlalchemy import func, select, update
import test_formal_records as shared
from fitness_app import db_schema as s
from scripts import seed_review_account as review


class ReviewAccountTests(unittest.TestCase):
    def setUp(self):
        self.fx = shared.RecordTests()
        self.fx.setUp()
        self.path = self.fx.db.path
        self.now = int(dt.datetime(2026, 11, 15, 22, 30, tzinfo=review.ZONE).timestamp() * 1000)

    def tearDown(self):
        for hook in self.fx.app.extensions.get("stop_hooks", []):
            hook()
        self.fx.tearDown()

    def user_count(self):
        with self.fx.db.transaction(False) as c:
            return c.scalar(select(func.count()).select_from(s.users))

    def test_login_complete_graph_and_repeated_run_preserves_changes(self):
        before = self.user_count()
        self.assertFalse(review.seed(self.path)["created"])
        self.assertEqual(self.user_count(), before)
        result = review.seed(self.path, True, shared.PASSWORD, self.now)
        self.assertTrue(result["created"])
        self.assertEqual(self.user_count(), before + 1)
        self.assertEqual(result["counts"]["analysis_sessions"], 10)
        self.assertEqual(result["counts"]["training_plan_sessions"], 12)
        self.assertEqual(result["counts"]["training_workouts"], 5)
        self.assertEqual(result["counts"]["training_workout_sets"], 38)
        self.assertEqual(result["counts"]["nutrition_journal"], 27)
        client = self.fx.app.test_client()
        response = self.fx.login(client, review.USERNAME)
        self.assertEqual(response.status_code, 200, response.json)
        uid = review.stable_id("user")
        with client.session_transaction() as state:
            auth = state["_user_id"]
        with self.fx.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(s.users.c.role).where(s.users.c.id == uid)), "user")
            self.assertEqual(c.scalar(select(func.count()).select_from(s.analysis_sessions).where(s.analysis_sessions.c.user_id == self.fx.user)), 0)
            configs = c.scalars(select(s.analysis_sessions.c.config_json).where(s.analysis_sessions.c.user_id == uid)).all()
            self.assertTrue(all(json.loads(x)["synthetic_data"] for x in configs))
            self.assertTrue(all(not json.loads(x)["model_detected"] for x in configs))
            self.assertFalse(c.exec_driver_sql("PRAGMA foreign_key_check").first())
            old_hash = c.scalar(select(s.users.c.password_hash).where(s.users.c.id == uid))
        records = self.fx.store.list_records(uid)
        self.assertEqual(records["total"], 10)
        for row in records["records"]:
            self.assertIn(self.fx.store.snapshot(uid, row["session_id"])["status"], {"completed", "ended_early"})
        with self.assertRaises(Exception):
            self.fx.store.snapshot(self.fx.other, records["records"][0]["session_id"])
        for page in ("/training/plan", "/records?view=list", "/training/review", "/coach/meals", "/coach", "/account"):
            self.assertEqual(client.get(page).status_code, 200, page)
        facts = self.fx.app.extensions["training_reviews"].facts(uid, auth, "2026-11-09")
        self.assertEqual(facts["plan_progress"]["completed_sessions"], 2)
        self.assertEqual(facts["actual_training"]["execution_session_count"], 2)
        self.assertEqual(facts["actual_training"]["analysis_record_count"], 4)
        with self.fx.db.transaction() as c:
            c.execute(update(s.user_profiles).where(s.user_profiles.c.user_id == uid).values(nickname="评审现场"))
        again = review.seed(self.path, True, "different-password-123", self.now)
        self.assertFalse(again["created"])
        self.assertEqual(again["counts"], result["counts"])
        with self.fx.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(s.users.c.password_hash).where(s.users.c.id == uid)), old_hash)
            self.assertEqual(c.scalar(select(s.user_profiles.c.nickname).where(s.user_profiles.c.user_id == uid)), "评审现场")

    def test_failure_rolls_back_entire_account(self):
        before = self.user_count()
        with patch.object(review, "seed_meals", side_effect=RuntimeError("fixture interruption")):
            with self.assertRaisesRegex(RuntimeError, "fixture interruption"):
                review.seed(self.path, True, shared.PASSWORD, self.now)
        self.assertEqual(self.user_count(), before)
        with self.fx.db.transaction(False) as c:
            self.assertFalse(c.scalar(select(s.users.c.id).where(s.users.c.username_key == review.USERNAME)))
            self.assertEqual(c.scalar(select(func.count()).select_from(s.training_plans)), 0)

    def test_existing_unowned_username_is_never_overwritten(self):
        with self.fx.db.transaction() as c:
            c.execute(update(s.users).where(s.users.c.id == self.fx.other).values(username_key=review.USERNAME))
        before = self.user_count()
        with self.assertRaisesRegex(ValueError, "已被其他用户占用"):
            review.seed(self.path, True, shared.PASSWORD, self.now)
        self.assertEqual(self.user_count(), before)


if __name__ == "__main__":
    unittest.main()
