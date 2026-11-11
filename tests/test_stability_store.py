"""运动摘要存储/归属/策略与AI引用；使用已标明的合成标量。"""
import copy,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
from sqlalchemy import select,func,update
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.database import StorageError,ConflictError
from fitness_app.auth import APIError
from fitness_app.stability_store import StabilityStore,StabilityCapture
from fitness_app.ai_context import validate_answer
import test_formal_records as fixtures
import test_movement_stability as metrics


class StabilityStoreTests(unittest.TestCase):
    def setUp(self):self.fx=fixtures.RecordTests();self.fx.setUp();self.store=self.fx.app.extensions['stability_store']
    def tearDown(self):self.fx.app.extensions['preview_manager'].stop();self.fx.tearDown()
    def make(self,enrolled=True):
        sid=self.fx.store.create(self.fx.user,self.fx.auth,{'action_id':'dumbbell_shoulder_press','capability_version':get_action('dumbbell_shoulder_press')['capability_version'],'source':'camera','mode':'free','target':None},s.new_id(),stability=enrolled)['session_id']
        self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());return sid
    def populate(self,sid):
        for i,window in enumerate(metrics.windows()):self.store.append(self.fx.user,sid,i,window)
        self.store.finish(self.fx.user,sid);self.fx.finish(sid)

    def test_default_business_summary_has_no_coordinates_and_append_replay_is_idempotent(self):
        sid=self.make();windows=metrics.windows()
        self.store.append(self.fx.user,sid,0,windows[0]);self.store.append(self.fx.user,sid,0,windows[0]);changed=copy.deepcopy(windows[0]);changed['view']='side'
        with self.assertRaises(ConflictError):self.store.append(self.fx.user,sid,0,changed)
        for i,window in enumerate(windows[1:],1):self.store.append(self.fx.user,sid,i,window)
        self.store.finish(self.fx.user,sid);value=self.store.snapshot(self.fx.user,sid,True)
        self.assertEqual(value['window_count'],6);self.assertEqual(value['assessment_state'],'compared');self.assertNotIn('landmarks',str(value));self.assertNotIn('joints',str(value))
        record=self.fx.client.get('/api/v1/records/'+sid);self.assertEqual(record.status_code,200,record.json);self.assertEqual(record.json['data']['movement_stability']['window_count'],6)
        self.assertEqual(self.fx.app.extensions['skeleton_store'].summary(self.fx.user,sid)['status'],'not_saved')
        old=self.make(False);self.assertEqual(self.store.append(self.fx.user,old,0,windows[0]),'not_recorded');self.assertEqual(self.store.snapshot(self.fx.user,old)['status'],'not_recorded')

    def test_permissions_and_record_cascade_do_not_leave_private_summaries(self):
        sid=self.make();self.populate(sid);other=self.fx.app.test_client();self.fx.login(other,'other_user')
        self.assertEqual(other.get('/api/v1/records/'+sid+'/stability').status_code,404)
        before=self.fx.store.snapshot(self.fx.user,sid);self.fx.store.delete_records(self.fx.user,self.fx.auth,[sid],{sid:before['state_version']},s.new_id())
        with self.fx.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.stability_runs)),0);self.assertEqual(c.scalar(select(func.count()).select_from(s.stability_windows)),0)
        self.assertEqual(self.fx.client.get('/api/v1/records/'+sid+'/stability').status_code,404)

    def test_caps_and_optional_failure_keep_saved_summary_and_base_training(self):
        sid=self.make();values=metrics.windows()
        with patch('fitness_app.stability_store.MAX_WINDOWS',2):
            self.store.append(self.fx.user,sid,0,values[0]);self.store.append(self.fx.user,sid,1,values[1]);self.assertEqual(self.store.append(self.fx.user,sid,2,values[2]),'limited')
        self.assertEqual(self.store.snapshot(self.fx.user,sid)['window_count'],2)
        other=self.make();capture=StabilityCapture(self.store,self.fx.user,'dumbbell_shoulder_press',other)
        with patch.object(self.store,'append',side_effect=OSError('optional statistics fixture')):
            for t in range(0,3101,100):capture.push(metrics.frame(t),metrics.output())
        self.assertEqual(capture.status,'partial');self.fx.finish(other);self.assertEqual(self.fx.store.snapshot(self.fx.user,other)['status'],'ended_early')

    def test_policy_and_integrity_failures_do_not_silently_reinterpret_data(self):
        sid=self.make();self.populate(sid)
        with self.fx.db.transaction() as c:c.execute(update(s.stability_runs).where(s.stability_runs.c.id==sid).values(policy_version='unknown-old-policy',state_version=99))
        self.assertEqual(self.store.snapshot(self.fx.user,sid)['status'],'unsupported_policy')
        with self.fx.db.transaction() as c:
            c.execute(update(s.stability_runs).where(s.stability_runs.c.id==sid).values(policy_version='movement-stability-1',state_version=100))
            c.execute(update(s.stability_windows).where(s.stability_windows.c.session_id==sid).values(sha256='0'*64))
        self.assertEqual(self.fx.client.get('/api/v1/records/'+sid+'/stability').status_code,503)
        base=self.fx.client.get('/api/v1/records/'+sid);self.assertEqual(base.status_code,200,base.json);self.assertEqual(base.json['data']['movement_stability']['status'],'unavailable')

    def test_ai_refs_measurement_not_error_and_source_deletion_invalidates_answer(self):
        sid=self.make();self.populate(sid);builder=self.fx.app.extensions['ai_jobs'].builder
        bundle=builder.build(self.fx.user,self.fx.auth,{'context_kind':'record','context_id':sid,'history_scope':'none','text':'这组前后有什么变化？'})
        self.assertEqual(bundle['read_scope']['movement_summary_records'],1);fact=next(v for v in bundle['facts']['evidence'] if v['kind']=='movement_stability_summary' and v['status']=='observation')
        answer={'summary':'这里只比较已保存的运动观察。','explanations':[{'evidence_id':fact['id'],'explanation':'本项呈现了所示投影的前后变化。','adjustment':'按可比条件复查。'}],'next_focus':[],'limitations':['观察不能证明技术对错。']}
        result=validate_answer(answer,bundle);self.assertEqual(result['explanations'][0]['adjustment_source'],'measurement_reference')
        for field in ('summary','explanation','focus'):
            invalid=copy.deepcopy(answer)
            if field=='summary':invalid['summary']='这些变化说明动作不标准。'
            elif field=='explanation':invalid['explanations'][0]['explanation']='肌肉已经疲劳了。'
            else:invalid['next_focus']=[{'text':'你的动作变差了。','evidence_ids':[fact['id']]}]
            with self.assertRaises(ValueError):validate_answer(invalid,bundle)
        bilateral=next(v for v in bundle['facts']['evidence'] if v.get('code')=='stability.wrist_difference')
        invalid=copy.deepcopy(answer);invalid['explanations'][0].update(evidence_id=bilateral['id'],explanation='你的左腕高于右腕。')
        with self.assertRaises(ValueError):validate_answer(invalid,bundle)
        before=self.fx.store.snapshot(self.fx.user,sid);self.fx.store.delete_records(self.fx.user,self.fx.auth,[sid],{sid:before['state_version']},s.new_id());self.assertFalse(builder.current(self.fx.user,self.fx.auth,bundle['reference']))

    def test_schema9_to10_is_atomic_and_preserves_previous_records(self):
        sid=self.make(False);self.fx.db.dispose()
        with closing(sqlite3.connect(self.fx.db.path)) as c:
            for table in reversed(s.metadata.sorted_tables):
                if s.TABLE_VERSIONS.get(table.name,1)>9:c.execute('DROP TABLE "'+table.name+'"')
            c.execute('UPDATE app_meta SET schema_version=9');c.commit()
        with patch.object(s.stability_windows,'create',side_effect=RuntimeError('migration fixture')):
            with self.assertRaises(StorageError):self.fx.db.initialize(allow_round5_migration=True)
        with closing(sqlite3.connect(self.fx.db.path)) as c:
            self.assertEqual(c.execute('SELECT schema_version FROM app_meta').fetchone()[0],9);self.assertEqual(c.execute("SELECT count(*) FROM sqlite_master WHERE name='stability_runs'").fetchone()[0],0)
        self.fx.db.initialize(allow_round5_migration=True);self.assertEqual(self.fx.db.inspect_identity()['schema_version'],s.SCHEMA_VERSION);self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['session_id'],sid)
        with self.fx.db.transaction() as c:c.exec_driver_sql('DROP INDEX ix_stability_window_order')
        with self.assertRaises(StorageError):self.fx.db.inspect_identity()

    def test_pre_record_limit_keeps_initial_windows_when_binding_later(self):
        capture=StabilityCapture(self.store,self.fx.user,'dumbbell_shoulder_press')
        for t in range(0,94001,100):capture.push(metrics.frame(t),metrics.output())
        self.assertEqual(capture.status,'limited');self.assertEqual(len(capture.pending),30)
        sid=self.make();capture.bind(sid);self.assertEqual(self.store.snapshot(self.fx.user,sid)['status'],'limited');self.assertEqual(self.store.snapshot(self.fx.user,sid)['window_count'],30)


if __name__=='__main__':unittest.main()
