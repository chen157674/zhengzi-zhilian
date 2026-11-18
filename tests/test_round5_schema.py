"""schema12→13显式门禁、原数据保持、回滚与活动唯一性；仅隔离库。"""
import unittest
from unittest.mock import patch
from sqlalchemy import select
import test_formal_records as shared
from legacy_schema_support import legacy_shape,rows_snapshot
from fitness_app import db_schema as s
from fitness_app.database import StorageError

class Round5SchemaTests(unittest.TestCase):
    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp()
    def tearDown(self):self.fx.tearDown()
    def test_explicit_migration_preserves_old_data_and_is_idempotent(self):
        sid=self.fx.running();self.fx.finish(sid);legacy_shape(self.fx.db,12);before=rows_snapshot(self.fx.db)
        with self.assertRaises(StorageError):self.fx.db.initialize()
        self.assertEqual(self.fx.db.inspect_identity(allow_legacy=True)['schema_version'],12)
        self.assertEqual(self.fx.db.initialize(allow_round5_migration=True)['schema_version'],s.SCHEMA_VERSION)
        self.assertEqual(rows_snapshot(self.fx.db,before),before)
        self.assertEqual(self.fx.db.initialize()['schema_version'],s.SCHEMA_VERSION)
    def test_creation_failure_rolls_back_all_new_tables(self):
        legacy_shape(self.fx.db,12)
        with patch.object(s.training_workouts,'create',side_effect=RuntimeError('isolated migration failure')):
            with self.assertRaises(StorageError):self.fx.db.initialize(allow_round5_migration=True)
        self.assertEqual(self.fx.db.inspect_identity(allow_legacy=True)['schema_version'],12)
        with self.fx.db.transaction(False) as c:
            names=set(c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").scalars())
        self.assertNotIn('training_plans',names);self.assertNotIn('coach_practice_details',names)
    def test_lookalike_partial_index_cannot_weaken_current_plan_constraint(self):
        with self.fx.db.transaction() as c:
            c.exec_driver_sql('DROP INDEX ix_training_one_current')
            c.exec_driver_sql("CREATE UNIQUE INDEX ix_training_one_current ON training_plans(user_id) WHERE status='draft'")
        with self.assertRaises(StorageError):self.fx.db.inspect_identity()

    def test_practice_parent_owner_is_enforced_by_database(self):
        from sqlalchemy.exc import IntegrityError
        tid=s.new_id()
        with self.fx.db.transaction() as c:c.execute(s.coach_saved_retests.insert().values(id=tid,user_id=self.fx.user,origin_json='{}',created_at_ms=self.fx.now[0],expires_at_ms=self.fx.now[0]+100000))
        with self.assertRaises(IntegrityError):
            with self.fx.db.transaction() as c:c.execute(s.coach_practice_details.insert().values(retest_task_id=tid,user_id=self.fx.other,steps_json='[]',knowledge_refs_json='[]'))
        with self.fx.db.transaction() as c:c.execute(s.coach_practice_details.insert().values(retest_task_id=tid,user_id=self.fx.user,steps_json='[]',knowledge_refs_json='[]'))
        self.assertEqual(self.fx.db.inspect_identity()['schema_version'],s.SCHEMA_VERSION)

    def test_owner_and_current_plan_constraints_and_index_audit(self):
        from sqlalchemy.exc import IntegrityError
        values=dict(user_id=self.fx.user,title='隔离计划',goal='general',status='active',start_date='2026-11-09',weeks=1)
        with self.fx.db.transaction() as c:c.execute(s.training_plans.insert().values(id=s.new_id(),**values))
        with self.assertRaises(IntegrityError):
            with self.fx.db.transaction() as c:c.execute(s.training_plans.insert().values(id=s.new_id(),**values))
        with self.assertRaises(IntegrityError):
            with self.fx.db.transaction() as c:c.execute(s.training_plans.insert().values(id=s.new_id(),**{**values,'status':'paused'}))
        with self.fx.db.transaction() as c:c.exec_driver_sql('DROP INDEX ix_training_one_current')
        with self.assertRaises(StorageError):self.fx.db.inspect_identity()

if __name__=='__main__':unittest.main()
