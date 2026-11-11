import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing

from sqlalchemy import select, update, delete, func
from sqlalchemy.exc import IntegrityError

from fitness_app.paths import ROOT, runtime_path
from fitness_app.database import (Database, StorageError, ConflictError, maintenance_lock,
                                  compare_and_set, idempotent, json_text)
from fitness_app import db_schema as s


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.test_root = ROOT / ".tmp/tests/database"
        self.test_root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=self.test_root)
        self.path = Path(self.directory.name) / "app.sqlite3"
        self.db = Database(self.path)
        self.db.initialize(allow_round5_migration=True)
        self.user, self.other, self.auth = s.new_id(), s.new_id(), s.new_id()
        self.session, self.second, self.group, self.other_group = [s.new_id() for _ in range(4)]
        with self.db.transaction() as c:
            c.execute(s.users.insert(), [dict(id=self.user,username_display="user1",username_key="user1",password_hash="test-hash"),
                                        dict(id=self.other,username_display="user2",username_key="user2",password_hash="test-hash")])
            c.execute(s.auth_sessions.insert().values(id=self.auth,user_id=self.user,token_hash="test-token-digest",expires_at_ms=s.now_ms()+100000))
            c.execute(s.action_capabilities.insert().values(action_id="test",capability_version="1",definition_json="{}",definition_hash="a"*64))
            for session_id in (self.session,self.second):
                c.execute(s.analysis_sessions.insert().values(id=session_id,user_id=self.user,owner_auth_session_id=self.auth,
                    action_id="test",capability_version="1",source="camera",mode="free",config_json="{}"))
            c.execute(s.session_sets.insert(), [dict(id=self.group,session_id=self.session),dict(id=self.other_group,session_id=self.second)])

    def tearDown(self):
        self.db.dispose()
        self.directory.cleanup()
        if self.test_root.exists() and not any(self.test_root.iterdir()):
            self.test_root.rmdir()

    def event(self, sequence=1, **overrides):
        values=dict(session_id=self.session,set_id=self.group,event_seq=sequence,start_ms=0,end_ms=1000,
                    algorithm_version="test",feature_schema_version="test",assessment_json="[]")
        values.update(overrides)
        return values

    def assert_rejected(self, statement):
        with self.assertRaises(IntegrityError), self.db.transaction() as c:
            c.execute(statement)

    def test_foreign_keys_json_range_and_immutability(self):
        self.assert_rejected(s.action_events.insert().values(**self.event(set_id=self.other_group)))
        self.assert_rejected(s.action_events.insert().values(**self.event(end_ms=-1)))
        self.assert_rejected(s.action_events.insert().values(**self.event(assessment_json='{}')))
        self.assert_rejected(s.users.update().where(s.users.c.id==self.user).values(enabled=2))
        self.assert_rejected(s.action_events.insert().values(**self.event(start_ms=1.5)))
        self.assert_rejected(s.action_capabilities.update().values(definition_json='{"changed":true}'))
        self.assert_rejected(s.jobs.insert().values(owner_id=self.other,kind="video",session_id=self.session))
        with self.db.transaction() as c:
            c.execute(s.action_events.insert().values(**self.event()))
        self.assert_rejected(s.action_events.insert().values(**self.event()))

    def test_atomic_failure_and_version(self):
        with self.assertRaises(RuntimeError), self.db.transaction() as c:
            c.execute(s.action_events.insert().values(**self.event()))
            c.execute(s.analysis_sessions.update().where(s.analysis_sessions.c.id==self.session).values(last_event_seq=1))
            raise RuntimeError("injected precommit failure")
        with self.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.action_events)),0)
            self.assertEqual(c.scalar(select(s.analysis_sessions.c.last_event_seq).where(s.analysis_sessions.c.id==self.session)),0)
        with self.db.transaction() as c:
            compare_and_set(c,s.users,self.user,0,{"enabled":0})
        with self.assertRaises(ConflictError), self.db.transaction() as c:
            compare_and_set(c,s.users,self.user,0,{"enabled":1})

    def test_idempotency_context_and_secret_rejection(self):
        command=s.new_id()
        def change(c):
            compare_and_set(c,s.users,self.user,0,{"enabled":0})
            return {"id":self.user,"version":1}
        with self.db.transaction() as c:
            first=idempotent(c,self.user,command,{"expected":0},"disable",lambda:change(c),self.user)
        with self.db.transaction() as c:
            replay=idempotent(c,self.user,command,{"expected":0},"disable",lambda:self.fail("must not repeat"),self.user)
        self.assertEqual(first,replay)
        for actor,payload,kind in [(self.other,{"expected":0},"disable"),(self.user,{"expected":1},"disable"),(self.user,{"expected":0},"reset")]:
            with self.assertRaises(ConflictError), self.db.transaction() as c:
                idempotent(c,actor,command,payload,kind,lambda:{},self.user)
        with self.assertRaises(ValueError), self.db.transaction() as c:
            idempotent(c,self.user,s.new_id(),{},"reset",lambda:{"data":{"temporary_password":"not-for-storage"}})
        with self.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.command_receipts)),1)

    def test_intervals_and_delete_cascade(self):
        finding=dict(session_id=self.session,set_id=self.group,code='posture.test',side='none',
                     start_ms=0,end_ms=1000,rule_version='reference-test',data_json='{}')
        self.assert_rejected(s.posture_observations.insert().values(**{**finding,'set_id':self.other_group}))
        with self.db.transaction() as c:
            c.execute(s.observation_intervals.insert().values(session_id=self.session,set_id=self.group,kind="hold",start_ms=0,end_ms=1000,algorithm_version="1"))
            c.execute(s.action_events.insert().values(**self.event()))
            c.execute(s.posture_observations.insert().values(**finding))
        self.assert_rejected(s.observation_intervals.insert().values(session_id=self.session,set_id=self.group,kind="hold",start_ms=500,end_ms=1500,algorithm_version="1"))
        with self.db.transaction() as c:
            c.execute(delete(s.analysis_sessions).where(s.analysis_sessions.c.id==self.session))
        with self.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.action_events)),0)
            self.assertEqual(c.scalar(select(func.count()).select_from(s.observation_intervals)),0)
            self.assertEqual(c.scalar(select(func.count()).select_from(s.posture_observations)),0)
            self.assertEqual(c.scalar(select(func.count()).select_from(s.users)),2)

    def test_schema_identity_version_and_maintenance_lock(self):
        original=self.path.read_bytes()
        with maintenance_lock(self.path):
            with self.assertRaises(StorageError):
                self.db.initialize(allow_round5_migration=True)
        unknown=Path(self.directory.name)/"unknown.sqlite3"
        with closing(sqlite3.connect(unknown)) as c:
            c.execute("CREATE TABLE original (data TEXT)")
        before=unknown.read_bytes()
        d=Database(unknown)
        try:
            with self.assertRaises(StorageError): d.initialize(allow_round5_migration=True)
            self.assertEqual(unknown.read_bytes(),before)
        finally: d.dispose()
        with self.db.transaction() as c:
            c.execute(update(s.app_meta).values(schema_version=999))
        high=self.path.read_bytes()
        with self.assertRaises(StorageError): self.db.initialize(allow_round5_migration=True)
        self.assertEqual(self.path.read_bytes(),high)

    def test_maintenance_ddl_and_data_rollback(self):
        def failing(c):
            c.exec_driver_sql("CREATE TABLE rollback_probe (value INTEGER)")
            c.execute(update(s.users).where(s.users.c.id==self.user).values(username_display="changed"))
            raise RuntimeError("injected migration failure")
        with self.assertRaises(RuntimeError): self.db.maintenance_change(failing,target_version=s.SCHEMA_VERSION+1)
        with self.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(s.app_meta.c.schema_version)),s.SCHEMA_VERSION)
            self.assertEqual(c.scalar(select(s.users.c.username_display).where(s.users.c.id==self.user)),"user1")
            self.assertEqual(c.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE name='rollback_probe'").scalar(),0)
        with maintenance_lock(runtime_path()/"service.lock",exact=True):
            with self.assertRaises(StorageError): self.db.maintenance_change(lambda c:None)
        self.db.maintenance_change(lambda c:c.execute(update(s.users).where(s.users.c.id==self.user).values(username_display="updated")))
        self.assertEqual(self.db.inspect_identity()["schema_version"],s.SCHEMA_VERSION)


if __name__=="__main__":
    unittest.main()
