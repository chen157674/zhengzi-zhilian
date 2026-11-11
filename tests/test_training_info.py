"""训练档案/自述备注的本人隔离、版本、幂等与追加式迁移。"""
from contextlib import closing
import json,sqlite3,unittest
from unittest.mock import patch
from sqlalchemy import select,delete
from fitness_app import db_schema as s
from fitness_app.database import StorageError
import test_formal_records as shared


class TrainingInfoTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp()
    def tearDown(self):self.fx.tearDown()
    def write(self,path,data,method='PATCH',client=None,key=None):
        client=client or self.fx.client
        csrf=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        return client.open(path,method=method,json=data,headers={'X-CSRFToken':csrf,'Idempotency-Key':key or s.new_id()})
    def profile(self,client=None):return (client or self.fx.client).get('/api/v1/me/training-profile').json['data']
    def other(self):
        client=self.fx.app.test_client();self.fx.login(client,'other_user');return client

    def test_optional_profile_is_private_patchable_and_cleared_without_resurrection(self):
        self.assertEqual(self.profile()['version'],0)
        other=self.other();key=s.new_id();data={'expected_version':0,'experience':'beginner','goals':['movement_quality'],
                                             'equipment':['dumbbell','cable'],'available_minutes':45}
        r=self.write('/api/v1/me/training-profile',data,key=key);self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['data']['version'],1);self.assertEqual(self.profile(other)['goals'],[])
        r=self.write('/api/v1/me/training-profile',{'expected_version':1,'available_minutes':None})
        self.assertEqual(r.json['data']['goals'],['movement_quality']);self.assertIsNone(r.json['data']['available_minutes'])
        r=self.write('/api/v1/me/training-profile',{'expected_version':2},method='DELETE')
        self.assertEqual(r.status_code,200);self.assertEqual(r.json['data']['version'],3)
        r=self.write('/api/v1/me/training-profile',data,key=key)
        self.assertEqual(r.status_code,200);self.assertEqual(r.json['data']['version'],3)
        self.assertEqual(self.profile()['goals'],[]);self.assertIsNone(self.profile()['experience'])

    def test_version_idempotency_and_invalid_values_do_not_mutate_profile(self):
        key=s.new_id();data={'expected_version':0,'goals':['training_habit']}
        self.assertEqual(self.write('/api/v1/me/training-profile',data,key=key).status_code,200)
        self.assertEqual(self.write('/api/v1/me/training-profile',data,key=key).json['data']['version'],1)
        for payload,expected in [({'expected_version':0,'experience':'experienced'},409),
                                 ({'expected_version':1,'experience':'experienced'},409)]:
            self.assertEqual(self.write('/api/v1/me/training-profile',payload,key=key if expected==409 and payload['expected_version']==1 else None).status_code,expected)
        for values in [{'available_minutes':True},{'available_minutes':0},{'available_minutes':float('nan')},{'goals':['strength','strength']},
                       {'equipment':['invented']},{'experience':'expert<script>'},{'user_id':self.fx.other}]:
            r=self.write('/api/v1/me/training-profile',{'expected_version':1,**values})
            self.assertIn(r.status_code,(400,422),r.json)
        self.assertEqual(self.profile()['version'],1)

    def test_body_measurements_are_optional_private_validated_and_cleared(self):
        other=self.other()
        payload={'expected_version':0,'height_cm':178.5,'weight_kg':72.4,'age_years':25,'goals':['muscle_gain','fat_loss']}
        response=self.write('/api/v1/me/training-profile',payload)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.profile()['height_cm'],178.5)
        self.assertEqual(self.profile()['weight_kg'],72.4)
        self.assertIsNone(self.profile(other)['height_cm'])
        for fields in ({'height_cm':True},{'height_cm':301},{'weight_kg':0},{'weight_kg':float('nan')},
                       {'weight_kg':float('inf')},{'age_years':25.5},{'age_years':121}):
            result=self.write('/api/v1/me/training-profile',{'expected_version':1,**fields})
            self.assertEqual(result.status_code,422,result.json)
        self.assertEqual(self.profile()['version'],1)
        self.assertEqual(self.write('/api/v1/me/training-profile',{'expected_version':1,'weight_kg':None}).status_code,200)
        self.assertIsNone(self.profile()['weight_kg']);self.assertEqual(self.profile()['height_cm'],178.5)
        self.assertEqual(self.write('/api/v1/me/training-profile',{'expected_version':2},method='DELETE').status_code,200)
        for field in ('height_cm','weight_kg','age_years'):self.assertIsNone(self.profile()[field])

    def test_notes_keep_units_subjectivity_and_detection_unchanged(self):
        sid=self.fx.running();self.fx.finish(sid)
        before=self.fx.store.snapshot(self.fx.user,sid)
        path=f'/api/v1/records/{sid}/training-note';key=s.new_id()
        payload={'expected_version':0,'load_value':12.5,'load_unit':'kg','load_basis':'per_hand','difficulty':'hard','note':'本人填写'}
        r=self.write(path,payload,'PUT',key=key);self.assertEqual(r.status_code,200,r.json)
        self.assertFalse(r.json['data']['inferred_from_video']);self.assertTrue(r.json['data']['difficulty_is_subjective'])
        self.assertEqual(self.write(path,payload,'PUT',key=key).json['data']['version'],1)
        after=self.fx.store.snapshot(self.fx.user,sid)
        self.assertEqual(after,before)
        r=self.write(path,{'expected_version':1,'load_value':5,'load_unit':'plate_index','load_basis':'device_setting'},'PUT')
        self.assertEqual(r.status_code,200,r.json);self.assertEqual(r.json['data']['load_unit'],'plate_index')
        self.assertEqual(r.json['data']['load_value'],5);self.assertIsNone(r.json['data']['difficulty'])
        self.assertEqual(self.write(path,{'expected_version':2},'PUT').status_code,200)
        data=self.fx.client.get(path).json['data'];self.assertIsNone(data['load_value']);self.assertIsNone(data['load_unit'])

    def test_note_authorization_validation_and_delete_cascade(self):
        sid=self.fx.create();path=f'/api/v1/records/{sid}/training-note'
        other=self.other();self.assertEqual(other.get(path).status_code,404)
        self.assertEqual(self.write(path,{'expected_version':0},'PUT',client=other).status_code,404)
        for values in [{'load_value':True},{'load_value':-1,'load_unit':'kg','load_basis':'total'},
                       {'load_value':1.234,'load_unit':'kg','load_basis':'total'},
                       {'load_value':5,'load_unit':'plate_index','load_basis':'total'},
                       {'load_value':float('nan'),'load_unit':'kg','load_basis':'total'},
                       {'load_value':5.5,'load_unit':'plate_index','load_basis':'device_setting'},
                       {'load_unit':'kg'},{'difficulty':'injured'},{'note':'\x00'}]:
            r=self.write(path,{'expected_version':0,**values},'PUT');self.assertEqual(r.status_code,422,r.json)
        self.assertEqual(self.write(path,{'expected_version':0,'difficulty':'moderate'},'PUT').status_code,200)
        with self.fx.db.transaction() as c:c.execute(delete(s.analysis_sessions).where(s.analysis_sessions.c.id==sid))
        with self.fx.db.transaction(False) as c:
            self.assertIsNone(c.execute(select(s.training_notes).where(s.training_notes.c.session_id==sid)).first())

    def legacy5(self):
        self.fx.db.dispose()
        with closing(sqlite3.connect(self.fx.db.path)) as c:
            for table in reversed(s.metadata.sorted_tables):
                if s.TABLE_VERSIONS.get(table.name,1)>5:c.execute('DROP TABLE "'+table.name+'"')
            c.execute('UPDATE app_meta SET schema_version=5');c.commit()

    def test_schema_one_three_four_upgrade_paths(self):
        for version in (1,3,4):
            with self.subTest(version=version):
                self.fx.db.dispose()
                with closing(sqlite3.connect(self.fx.db.path)) as c:
                    for table in reversed(s.metadata.sorted_tables):
                        if s.TABLE_VERSIONS.get(table.name,1)>version:c.execute('DROP TABLE "'+table.name+'"')
                    c.execute('DROP INDEX IF EXISTS ix_receipts_resource_saved')
                    if version==3:c.execute('ALTER TABLE user_profiles DROP COLUMN avatar_key')
                    c.execute('UPDATE app_meta SET schema_version=?',(version,));c.commit()
                self.fx.db.initialize(allow_round5_migration=True)
                self.assertEqual(self.fx.db.inspect_identity()['schema_version'],s.SCHEMA_VERSION)
                self.assertEqual(self.profile()['version'],0)

    def test_schema5_migration_retains_records_and_adds_only_new_tables(self):
        sid=self.fx.running();self.fx.finish(sid);self.legacy5()
        self.assertEqual(self.fx.db.inspect_identity(allow_legacy=True)['schema_version'],5)
        self.fx.db.initialize(allow_round5_migration=True);self.assertEqual(self.fx.db.inspect_identity()['schema_version'],s.SCHEMA_VERSION)
        self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['action_id'],'bodyweight_squat')
        self.assertEqual(self.profile()['version'],0)

    def test_migration_failure_rolls_back_and_unregistered_tables_are_rejected(self):
        self.legacy5()
        with patch.object(s.training_notes,'create',side_effect=RuntimeError('injected')):
            with self.assertRaises(StorageError):self.fx.db.initialize(allow_round5_migration=True)
        with closing(sqlite3.connect(self.fx.db.path)) as c:
            self.assertEqual(c.execute('SELECT schema_version FROM app_meta').fetchone()[0],5)
            self.assertEqual(c.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('training_profiles','training_notes')").fetchone()[0],0)
            c.execute('CREATE TABLE training_profiles (foreign_content TEXT)');c.commit()
        with self.assertRaises(StorageError):self.fx.db.initialize(allow_round5_migration=True)


if __name__=='__main__':unittest.main()
