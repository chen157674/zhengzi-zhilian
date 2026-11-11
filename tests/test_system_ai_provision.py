"""系统密钥导入的实际加密、预算保留和错误库边界。"""
import tempfile
import unittest
from pathlib import Path
from sqlalchemy import select
from fitness_app.paths import runtime_path
from fitness_app.database import Database, StorageError
from fitness_app.ai_secrets import SecretVault
from fitness_app import db_schema as s
from scripts.configure_system_ai import provision,configure_limits

KEY = 'sk-fixture-system-provision-12345678'


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='system-key-test-', dir=runtime_path())
        self.path = Path(self.tmp.name)/'app.sqlite3'
        self.db = Database(self.path)
        self.db.initialize()

    def tearDown(self):
        self.db.engine.dispose()
        self.tmp.cleanup()

    def policy(self):
        with self.db.transaction(False) as c:
            return dict(c.execute(select(s.ai_system_policy)).mappings().one())

    def test_new_policy_encrypted_disabled_and_idempotent(self):
        result = provision(self.path, KEY)
        self.assertTrue(result['system_key_configured'])
        self.assertFalse(result['enabled'])
        self.assertEqual(result['budget_micro_cny'], 0)
        self.assertEqual(result['per_user_daily_requests'], 0)
        self.assertNotIn(KEY, str(result))
        row = self.policy()
        self.assertNotIn(KEY, row['key_ciphertext'])
        vault = SecretVault(self.path.parent/'private/ai-master.key')
        self.assertEqual(vault.open('system', row['key_ciphertext']), KEY)
        master = vault.path.read_bytes()
        self.assertFalse(provision(self.path, KEY)['changed'])
        self.assertEqual(self.policy()['version'], row['version'])
        self.assertEqual(vault.path.read_bytes(), master)

    def test_rotation_preserves_limits_spend_and_master(self):
        provision(self.path, KEY)
        with self.db.transaction() as c:
            c.execute(s.ai_system_policy.update().values(enabled=1, budget_micro_cny=2000000,
                spent_micro_cny=1000, reserved_micro_cny=2000, per_user_daily_requests=3))
        old = self.policy()
        master_path = self.path.parent/'private/ai-master.key'
        master = master_path.read_bytes()
        result = provision(self.path, KEY+'9')
        row = self.policy()
        for field in ('enabled','budget_micro_cny','spent_micro_cny','reserved_micro_cny','per_user_daily_requests'):
            self.assertEqual(old[field], row[field])
        self.assertEqual(row['version'], old['version']+1)
        self.assertTrue(result['changed'])
        self.assertEqual(master_path.read_bytes(), master)

    def test_missing_or_foreign_database_never_created(self):
        target = self.path.parent/'missing.sqlite3'
        with self.assertRaises(ValueError): provision(target, KEY)
        self.assertFalse(target.exists())
        foreign = self.path.parent/'foreign.sqlite3'
        foreign.write_bytes(b'not a sqlite database')
        with self.assertRaises(StorageError): provision(foreign, KEY)
        self.assertEqual(foreign.read_bytes(), b'not a sqlite database')

    def test_invalid_key_or_missing_existing_master_preserves_policy(self):
        with self.assertRaises(ValueError): provision(self.path, 'short')
        self.assertFalse((self.path.parent/'private').exists())
        provision(self.path, KEY)
        row = self.policy()
        (self.path.parent/'private/ai-master.key').unlink()
        with self.assertRaises(StorageError): provision(self.path, KEY+'9')
        self.assertEqual(self.policy(), row)
        self.assertFalse((self.path.parent/'private/ai-master.key').exists())

    def test_explicit_limits_and_enable_are_bounded_and_idempotent(self):
        provision(self.path,KEY)
        result=configure_limits(self.path,'10',10,True)
        self.assertTrue(result['enabled']);self.assertEqual(result['budget_micro_cny'],10000000)
        self.assertEqual(result['per_user_daily_requests'],10)
        self.assertEqual(configure_limits(self.path,'10',10,True)['version'],result['version'])
        unlimited=configure_limits(self.path,'0',0,True)
        self.assertTrue(unlimited['enabled'])
        self.assertEqual(unlimited['budget_micro_cny'],0)
        self.assertEqual(unlimited['per_user_daily_requests'],0)


if __name__ == '__main__':
    unittest.main()
