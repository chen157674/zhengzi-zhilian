"""保存回执增长后的查询路径与schema4→5原子索引升级，不重跑业务矩阵。"""
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch
from sqlalchemy import select
from fitness_app import db_schema as s
from fitness_app.database import Database,StorageError
import test_formal_records as shared

QUERY="SELECT max(ended_at_ms) FROM command_receipts WHERE actor_user_id=? AND resource_id=? AND resource_type LIKE 'session.%'"


class SavedReceiptLookupTests(unittest.TestCase):
    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp()
    def tearDown(self):self.fx.tearDown()

    def test_actual_snapshot_filters_actor_resource_and_kind_using_covering_index(self):
        fx=self.fx;sid=fx.running();base=fx.now[0]+1000;count=3000
        rows=[{'command_id':s.new_id(),'actor_user_id':fx.user,'resource_id':sid,'resource_type':'session.posture',
               'request_digest':'a'*64,'status':'succeeded','result_json':'{}','ended_at_ms':base+i} for i in range(count)]
        rows.extend([{'command_id':s.new_id(),'actor_user_id':actor,'resource_id':resource,'resource_type':kind,
                     'request_digest':'b'*64,'status':'succeeded','result_json':'{}','ended_at_ms':base+100000}
                    for actor,resource,kind in [(fx.other,sid,'session.posture'),(fx.user,s.new_id(),'session.posture'),(fx.user,sid,'job.video')]])
        with fx.db.transaction() as c:c.execute(s.command_receipts.insert(),rows)
        self.assertEqual(fx.store.snapshot(fx.user,sid)['last_saved_at_ms'],base+count-1)
        with fx.db.transaction(False) as c:
            plan=[r[3] for r in c.exec_driver_sql('EXPLAIN QUERY PLAN '+QUERY,(fx.user,sid))]
        self.assertTrue(any('COVERING INDEX ix_receipts_resource_saved' in p for p in plan),plan)

    def legacy_four(self,wrong=False):
        from legacy_schema_support import legacy_shape
        legacy_shape(self.fx.db,4)
        with closing(sqlite3.connect(str(self.fx.db.path))) as c,c:
            if wrong:c.execute('CREATE INDEX ix_receipts_resource_saved ON command_receipts(resource_id)')

    def test_schema_four_adds_index_preserving_counts_profile_and_password(self):
        fx=self.fx;sid=fx.running();fx.store.append_event(fx.user,sid,fx.event());profile=fx.client.get('/api/v1/account/profile').json['data']
        self.legacy_four();database=Database(fx.db.path)
        try:
            self.assertEqual(database.initialize(allow_round5_migration=True)['schema_version'],s.SCHEMA_VERSION)
            self.assertEqual(fx.store.snapshot(fx.user,sid)['total_count'],1)
            self.assertEqual(fx.client.get('/api/v1/account/profile').json['data'],profile)
            fx.login(fx.client,'Record_User');self.assertEqual(fx.client.get('/api/v1/auth/me').status_code,200)
        finally:database.dispose()

    def test_failed_index_upgrade_rolls_back_without_bumping_version(self):
        fx=self.fx;self.legacy_four();database=Database(fx.db.path);original=s.receipt_saved_index.create
        def fail(c,**kwargs):original(c,**kwargs);raise RuntimeError('D-FAULT index version commit')
        try:
            with patch.object(s.receipt_saved_index,'create',side_effect=fail):
                with self.assertRaises(StorageError):database.initialize(allow_round5_migration=True)
            with closing(sqlite3.connect(str(fx.db.path))) as c:
                self.assertEqual(c.execute('SELECT schema_version FROM app_meta').fetchone()[0],4)
                self.assertEqual(list(c.execute("PRAGMA index_info('ix_receipts_resource_saved')")),[])
                self.assertEqual(c.execute('SELECT count(*) FROM users').fetchone()[0],3)
        finally:database.dispose()

    def test_incompatible_named_index_is_not_silently_replaced(self):
        fx=self.fx;self.legacy_four(wrong=True);database=Database(fx.db.path)
        try:
            with self.assertRaises(StorageError):database.initialize(allow_round5_migration=True)
            with closing(sqlite3.connect(str(fx.db.path))) as c:
                self.assertEqual(c.execute('SELECT schema_version FROM app_meta').fetchone()[0],4)
                self.assertEqual([r[2] for r in c.execute("PRAGMA index_info('ix_receipts_resource_saved')")],['resource_id'])
        finally:database.dispose()


if __name__=='__main__':unittest.main()
