"""视频父任务与修订事务；使用合成存储夹具，不作为真实视频/姿态准确率。"""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import json,sqlite3,unittest
from unittest.mock import patch
from sqlalchemy import select,update,func
from sqlalchemy.exc import IntegrityError
from fitness_app import db_schema as s
from fitness_app.auth import APIError
from fitness_app.database import ConflictError,StorageError,json_text
from fitness_app.video_analysis_store import VideoAnalysisStore
from fitness_app.records import effective_records
import test_formal_records as fixtures


class VideoAnalysisStoreTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.RecordTests();self.fx.setUp();self.store=VideoAnalysisStore(self.fx.store,self.fx.app.extensions['media_inputs'])
        self.uid=self.fx.user;self.auth=self.fx.auth
    def tearDown(self):self.fx.tearDown()
    def source(self,sha='a'*64):
        iid=s.new_id()
        with self.fx.db.transaction() as c:c.execute(s.media_inputs.insert().values(id=iid,user_id=self.uid,status='ready',sha256=sha,bytes=128,duration_ms=10000,
            encoding_json=json_text({'width':640,'height':480,'duration_ms':10000,'origin_ms':0,'rotation':0,'validation':'container_and_first_decoded_frame'}),expires_at_ms=self.fx.now[0]+60000))
        return iid
    def parent(self,actions=('barbell_overhead_press',None,'conventional_deadlift')):
        iid=self.source();parent=self.store.create(self.uid,self.auth,{'input_id':iid},s.new_id());pid=parent['id']
        self.store.begin(self.uid,self.auth,pid);step=10000//len(actions);segments=[]
        for index,action in enumerate(actions):segments.append({'segment_index':index+1,'start_ms':index*step,'end_ms':(index+1)*step if index<len(actions)-1 else 10000,
            'action_id':action,'model_version':'synthetic-recognizer','policy_version':'fixture','unassessable_ranges':[],'recognition_unassessable_ranges':[]})
        self.store.register_segments(self.uid,self.auth,pid,segments,10000)
        return self.store.snapshot(self.uid,self.auth,pid)
    def revision(self,parent,index=0,action=None,automatic=True,key=None):
        current=self.store.snapshot(self.uid,self.auth,parent['id']);segment=current['segments'][index]
        iid=parent['input_id'] if automatic else self.source()
        return self.store.queue_revision(self.uid,self.auth,segment['id'],{'action_id':action or segment['action_id'],'input_id':iid,'expected_version':segment['state_version']},key or s.new_id(),automatic)
    def complete(self,rid):
        child=self.store.child(self.uid,self.auth,rid);sid=child['session_id']
        self.fx.store.set_state(self.uid,sid,'running',s.new_id());self.fx.store.set_state(self.uid,sid,'completed',s.new_id())
        return self.store.promote(self.uid,self.auth,rid)
    def count(self):
        with self.fx.db.transaction(False) as c:return c.scalar(select(func.count()).select_from(s.analysis_sessions).where(s.analysis_sessions.c.user_id==self.uid,effective_records()))

    def test_parent_auto_requires_no_fake_action_and_concurrent_retries_consume_once(self):
        iid=self.source();key=s.new_id();payload={'input_id':iid}
        with ThreadPoolExecutor(max_workers=2) as pool:values=list(pool.map(lambda _:self.store.create(self.uid,self.auth,payload,key),range(2)))
        self.assertEqual(values[0]['id'],values[1]['id']);self.assertEqual(values[0]['mode'],'auto');self.assertIsNone(values[0]['manual_action_id'])
        with self.fx.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.video_analyses)),1)
        with self.assertRaises(ConflictError):self.store.create(self.uid,self.auth,{**payload,'mode':'manual','action_id':'hammer_curl'},key)
        other=self.fx.app.test_client();self.fx.login(other,'other_user')
        with other.session_transaction() as state:auth=state['_user_id']
        with self.assertRaises(APIError) as got:self.store.snapshot(self.fx.other,auth,values[0]['id'])
        self.assertEqual(got.exception.status,404)

    def test_atomic_replacement_keeps_history_but_counts_only_current_target_segment(self):
        parent=self.parent();first=self.complete(self.revision(parent,0));neighbor=self.complete(self.revision(parent,2));self.store.finish(self.uid,parent['id'],'completed')
        self.assertEqual(self.count(),2)
        rid=self.revision(parent,0,'dumbbell_shoulder_press',False);new=self.store.child(self.uid,self.auth,rid)
        self.assertFalse(new['included_in_statistics']);self.assertEqual(self.count(),2)
        self.fx.store.set_state(self.uid,new['session_id'],'running',s.new_id());self.fx.store.set_state(self.uid,new['session_id'],'completed',s.new_id())
        self.store.promote(self.uid,self.auth,rid);self.assertEqual(self.count(),2)
        old=self.fx.store.snapshot(self.uid,first);self.assertFalse(old['included_in_statistics']);self.assertEqual(old['superseded_by'],new['session_id'])
        self.assertTrue(self.fx.store.snapshot(self.uid,neighbor)['included_in_statistics'])
        result=self.store.snapshot(self.uid,self.auth,parent['id']);self.assertEqual(result['segments'][0]['effective_action_id'],'dumbbell_shoulder_press')
        self.assertEqual(result['segments'][1]['kind'],'unknown');self.assertEqual(len(result['segments'][0]['revisions']),2)
        self.assertEqual(self.store.promote(self.uid,self.auth,rid),new['session_id'])
        with self.fx.db.transaction() as c:
            with self.assertRaises(IntegrityError):c.execute(update(s.video_revisions).where(s.video_revisions.c.session_id==first).values(is_current=1))

    def test_failed_or_cancelled_revision_does_not_replace_good_result(self):
        parent=self.parent(('hammer_curl',));old=self.complete(self.revision(parent));self.store.finish(self.uid,parent['id'],'completed')
        rid=self.revision(parent,action='standing_dumbbell_curl',automatic=False);self.store.child(self.uid,self.auth,rid)
        self.store.fail_revision(self.uid,rid,'failed','INJECTED_FAILURE');self.assertEqual(self.count(),1)
        current=self.store.snapshot(self.uid,self.auth,parent['id'])['segments'][0]['current_revision'];self.assertEqual(current['session_id'],old)
        with self.assertRaises(ConflictError):self.store.promote(self.uid,self.auth,rid)

    def test_source_identity_stale_versions_and_incomplete_results_cannot_be_promoted(self):
        parent=self.parent(('hammer_curl',));segment=parent['segments'][0]
        with self.assertRaises(ConflictError):self.store.finish(self.uid,parent['id'],'completed')
        self.complete(self.revision(parent));self.store.finish(self.uid,parent['id'],'completed')
        latest=self.store.snapshot(self.uid,self.auth,parent['id'])['segments'][0]
        with self.assertRaises(APIError) as got:self.store.queue_revision(self.uid,self.auth,segment['id'],{'action_id':'hammer_curl','input_id':self.source('b'*64),'expected_version':latest['state_version']},s.new_id())
        self.assertEqual(got.exception.code,'SOURCE_MISMATCH')
        with self.assertRaises(ConflictError):self.store.queue_revision(self.uid,self.auth,segment['id'],{'action_id':'hammer_curl','input_id':self.source(),'expected_version':0},s.new_id())

    def test_deleted_current_record_is_not_resurrected_by_later_revision(self):
        parent=self.parent(('hammer_curl',));old=self.complete(self.revision(parent));self.store.finish(self.uid,parent['id'],'completed')
        rid=self.revision(parent,action='standing_dumbbell_curl',automatic=False);child=self.store.child(self.uid,self.auth,rid)
        old_data=self.fx.store.snapshot(self.uid,old)
        self.fx.store.delete_records(self.uid,self.auth,[old],{old:old_data['state_version']},s.new_id())
        self.fx.store.set_state(self.uid,child['session_id'],'running',s.new_id());self.fx.store.set_state(self.uid,child['session_id'],'completed',s.new_id())
        with self.assertRaises(ConflictError):self.store.promote(self.uid,self.auth,rid)
        self.assertEqual(self.count(),0);self.assertIsNone(self.store.snapshot(self.uid,self.auth,parent['id'])['segments'][0]['current_revision'])

    def test_cancelled_parent_rejects_late_promotion_and_restart_never_replays(self):
        parent=self.parent(('hammer_curl',));rid=self.revision(parent);child=self.store.child(self.uid,self.auth,rid)
        self.fx.store.set_state(self.uid,child['session_id'],'running',s.new_id());self.fx.store.set_state(self.uid,child['session_id'],'completed',s.new_id())
        current=self.store.snapshot(self.uid,self.auth,parent['id']);self.store.cancel(self.uid,self.auth,parent['id'],current['state_version'],s.new_id())
        with self.assertRaises(APIError):self.store.promote(self.uid,self.auth,rid)
        self.store.recover();after=self.store.snapshot(self.uid,self.auth,parent['id'])
        self.assertEqual(after['status'],'interrupted');self.assertEqual(after['segments'][0]['revisions'][0]['status'],'interrupted');self.assertEqual(self.count(),0)

    def test_internal_consumed_input_is_not_available_to_public_session_creation(self):
        parent=self.parent(('hammer_curl',));rid=self.revision(parent);child=self.store.child(self.uid,self.auth,rid)
        from fitness_app.catalogue import get_action
        payload={'action_id':'hammer_curl','capability_version':get_action('hammer_curl')['capability_version'],'input_id':parent['input_id'],'source':'video','mode':'free','target':None}
        with self.assertRaises(APIError):self.fx.store.create(self.uid,self.auth,payload,s.new_id())
        with self.assertRaises(APIError):self.fx.store.create(self.uid,self.auth,{**payload,'video_revision_id':rid,'included_in_statistics':True},s.new_id(),video_revision_id=rid)
        self.assertEqual(child['source'],'video');self.assertEqual(child['source_sha256'],'a'*64)

    def test_schema7_upgrade_preserves_ai_and_rolls_back_failed_new_tables(self):
        self.fx.app.extensions['ai_settings'].write(self.uid,self.auth,{'expected_version':0,'history_reference':False},s.new_id())
        self.fx.db.dispose()
        with closing(sqlite3.connect(self.fx.db.path)) as c:
            for table in reversed(s.metadata.sorted_tables):
                if s.TABLE_VERSIONS.get(table.name,1)>7:c.execute('DROP TABLE "'+table.name+'"')
            c.execute('UPDATE app_meta SET schema_version=7');c.commit()
        with patch.object(s.video_revisions,'create',side_effect=RuntimeError('migration fixture')):
            with self.assertRaises(StorageError):self.fx.db.initialize(allow_round5_migration=True)
        with closing(sqlite3.connect(self.fx.db.path)) as c:
            self.assertEqual(c.execute('SELECT schema_version FROM app_meta').fetchone()[0],7)
            self.assertEqual(c.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('video_analyses','video_segments','video_revisions')").fetchone()[0],0)
        self.fx.db.initialize(allow_round5_migration=True);self.assertEqual(self.fx.db.inspect_identity()['schema_version'],s.SCHEMA_VERSION)
        self.assertFalse(self.fx.app.extensions['ai_settings'].get(self.uid,self.auth)['history_reference'])
        with self.fx.db.transaction() as c:c.exec_driver_sql('DROP INDEX ix_video_revision_current')
        with self.assertRaises(StorageError):self.fx.db.inspect_identity()

    def test_single_revision_view_override_preserves_parent_setting_and_is_persisted(self):
        parent=self.parent(('barbell_overhead_press',));self.complete(self.revision(parent));self.store.finish(self.uid,parent['id'],'completed')
        segment=self.store.snapshot(self.uid,self.auth,parent['id'])['segments'][0]
        rid=self.store.queue_revision(self.uid,self.auth,segment['id'],{'action_id':'barbell_overhead_press','input_id':self.source(),'expected_version':segment['state_version'],'coaching_view':'front'},s.new_id())
        child=self.store.child(self.uid,self.auth,rid);self.assertEqual(child['config']['coaching_view'],'front')
        snapshot=self.store.snapshot(self.uid,self.auth,parent['id']);self.assertEqual(snapshot['coaching_view'],'auto')
        self.assertEqual(snapshot['segments'][0]['revisions'][-1]['coaching_view'],'front')


if __name__=='__main__':unittest.main()
