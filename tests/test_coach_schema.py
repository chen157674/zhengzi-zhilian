"""schema11迁移、原数据保留、新版边界；仅隔离库。"""
import sqlite3,unittest
from contextlib import closing
from sqlalchemy import select
import test_formal_records as shared
from legacy_schema_support import legacy_shape,rows_snapshot
from fitness_app import db_schema as s
from fitness_app.database import StorageError

class CoachSchemaTests(unittest.TestCase):
    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp()
    def tearDown(self):self.fx.tearDown()
    def test_schema11_upgrade_preserves_old_rows_and_is_idempotent(self):
        sid=self.fx.running();self.fx.finish(sid);legacy_shape(self.fx.db,11)
        before=rows_snapshot(self.fx.db);self.assertEqual(self.fx.db.initialize(allow_round5_migration=True)['schema_version'],s.SCHEMA_VERSION)
        self.assertEqual(rows_snapshot(self.fx.db,before),before)
        self.assertEqual(self.fx.db.initialize(allow_round5_migration=True)['schema_version'],s.SCHEMA_VERSION)
        self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['session_id'],sid)
    def test_missing_owner_index_rejected(self):
        with self.fx.db.transaction() as c:c.exec_driver_sql('DROP INDEX ix_coach_retest_owner_expiry')
        with self.assertRaises(StorageError):self.fx.db.inspect_identity()

if __name__=='__main__':unittest.main()
