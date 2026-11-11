"""19：仅覆盖连接复用新边界；不重跑账号／页面全矩阵。"""
from contextlib import ExitStack
from time import perf_counter
import unittest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from fitness_app.database import StorageError
import test_formal_database as shared


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.fx = shared.DatabaseTests()
        self.fx.setUp()
        self.db = self.fx.db

    def tearDown(self):
        self.fx.tearDown()

    def test_reused_connection_keeps_constraints_and_rolls_back(self):
        handles = []
        event.listen(self.db.engine, 'checkout', lambda handle, record, proxy: handles.append(handle))
        with self.db.transaction(False) as c:
            self.assertEqual(c.exec_driver_sql('PRAGMA foreign_keys').scalar(), 1)
            self.assertEqual(c.exec_driver_sql('PRAGMA busy_timeout').scalar(), 1000)
        with self.assertRaisesRegex(RuntimeError, 'rollback-probe'):
            with self.db.transaction() as c:
                c.exec_driver_sql('UPDATE users SET username_display=? WHERE id=?', ('临时未提交', self.fx.user))
                raise RuntimeError('rollback-probe')
        with self.db.transaction(False) as c:
            self.assertEqual(c.exec_driver_sql('SELECT username_display FROM users WHERE id=?', (self.fx.user,)).scalar(), 'user1')
        with self.assertRaises(IntegrityError):
            with self.db.transaction() as c:
                c.exec_driver_sql('UPDATE analysis_sessions SET user_id=? WHERE id=?', ('0'*36, self.fx.session))
        with self.db.transaction(False) as c:
            self.assertEqual(c.exec_driver_sql('SELECT user_id FROM analysis_sessions WHERE id=?', (self.fx.session,)).scalar(), self.fx.user)
        self.assertTrue(all(h is handles[0] for h in handles))

    def test_pool_limit_timeout_then_recovery(self):
        with ExitStack() as stack:
            for _ in range(5):
                stack.enter_context(self.db.engine.connect())
            start = perf_counter()
            with self.assertRaisesRegex(StorageError, '连接繁忙'):
                with self.db.transaction(False):
                    self.fail('池耗尽不能开始事务')
            elapsed = perf_counter()-start
            self.assertGreaterEqual(elapsed, .8)
            self.assertLess(elapsed, 3)
        with self.db.transaction(False) as c:
            self.assertEqual(c.exec_driver_sql('SELECT 1').scalar(), 1)
        self.assertEqual(self.db.engine.pool.checkedout(), 0)

    def test_dispose_releases_idle_handles(self):
        with self.db.transaction(False) as c:
            handle = c.connection.driver_connection
        self.db.dispose()
        import sqlite3
        with self.assertRaises(sqlite3.ProgrammingError):
            handle.execute('SELECT 1')
        with self.db.transaction(False) as c:
            self.assertEqual(c.exec_driver_sql('SELECT 1').scalar(), 1)


if __name__ == '__main__':
    unittest.main()
