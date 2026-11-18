"""新前端接线与自动保存/概览的增量检查；隔离数据，不重跑模型训练。"""
import copy,json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import update,select
import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.automatic_recording import AutomaticRecorder
from fitness_app.database import StorageError
from fitness_app.posture_rules import rules_for,describe
from fitness_app.training_overview import aggregate_records,interval_totals

def assessment(state,code='squat.range',start=0,end=1000):
    rule=next(x for x in rules_for('bodyweight_squat') if x['code']==code)
    reason='cycle_evidence_missing' if state=='unassessable' else ''
    message,suggestion=describe(code,state,reason)
    return dict(code=code,status=state,reason=reason,start_ms=start,end_ms=end,rule_version=rule['rule_version'],
                evidence_level='reference',body_parts=rule['body_parts'],message=message,suggestion=suggestion,evidence={})

class RedesignTests(unittest.TestCase):
    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp()
    def tearDown(self):self.fx.tearDown()
    def test_new_page_routes_render_without_old_navigation_or_private_leak(self):
        for path in ['/','/account','/settings','/account/sessions','/records','/records?view=list','/realtime','/video','/account/password']:
            with self.subTest(path=path):
                response=self.fx.client.get(path)
                self.assertEqual(response.status_code,200)
                self.assertIn('正姿智练',response.get_data(as_text=True))
                self.assertNotIn('管理工具',response.get_data(as_text=True))
        anonymous=self.fx.app.test_client()
        self.assertEqual(anonymous.get('/account').status_code,302)
        self.assertEqual(anonymous.get('/settings').status_code,302)
        self.assertNotIn('href="/admin/users"',self.fx.client.get('/settings').get_data(as_text=True))
        self.assertEqual(self.fx.client.get('/admin/users').status_code,403)
        for path in ['/login','/register']:
            response=anonymous.get(path)
            self.assertEqual(response.status_code,200)
            self.assertIn('class="auth-backdrop"',response.get_data(as_text=True))

    def test_overview_denominator_ignores_unknown_and_other_users(self):
        fx=self.fx;sid=fx.running()
        for i,state in enumerate(['issue','not_observed','unassessable'],1):
            fx.store.append_event(fx.user,sid,{**fx.event(i),'assessments':[assessment(state,start=(i-1)*1000,end=i*1000)]})
        fx.finish(sid)
        response=fx.client.get('/api/v1/account/training-overview')
        self.assertEqual(response.status_code,200,response.json)
        d=response.json['data'];self.assertEqual((d['issue'],d['evaluated'],d['unknown'],d['ratio']),(1,2,1,.5))
        other=fx.app.test_client();fx.login(other,'Other_User')
        self.assertEqual(other.get('/api/v1/account/training-overview').json['data']['record_count'],0)
        self.assertEqual(fx.client.get('/api/v1/account/training-overview?code=deadlift.extension').status_code,422)

    def test_overview_deletion_invalidates_real_query(self):
        fx=self.fx;sid=fx.running();fx.store.append_event(fx.user,sid,fx.event());fx.finish(sid)
        self.assertEqual(fx.client.get('/api/v1/account/training-overview').json['data']['unknown'],1)
        snap=fx.store.snapshot(fx.user,sid)
        fx.post(fx.client,'/api/v1/records/delete',{'ids':[sid],'expected_versions':{sid:snap['state_version']}})
        self.assertEqual(fx.client.get('/api/v1/account/training-overview').json['data']['record_count'],0)

    def recorder(self):
        fx=self.fx
        return AutomaticRecorder(fx.store,fx.user,fx.auth,s.new_id()),SimpleNamespace(segment_id=s.new_id(),action='bodyweight_squat',version='fixture',active_ms=0)
    def test_automatic_segments_save_and_survive_duplicate_retry(self):
        fx=self.fx;rec,a=self.recorder()
        rec.process(a,{}, {'captured_ms':100})
        a.active_ms=1000;event=fx.event();event.update(start_ms=100,end_ms=1100,assessments=[assessment('issue',start=100,end=1100)])
        out={'events':[event]}
        rec.process(a,out,{'captured_ms':1100})
        self.assertFalse(rec.failed,rec.message)
        rec.close('action_or_model_changed')
        sid=rec.record_ids[0];snap=fx.store.snapshot(fx.user,sid,True)
        self.assertEqual((snap['total_count'],snap['events'][0]['start_ms'],snap['events'][0]['end_ms']),(1,0,1000))
        self.assertEqual(snap['config']['automatic_run_id'],rec.run_id)
        self.assertEqual(snap['status'],'ended_early')
        a.segment_id=s.new_id();a.active_ms=0
        rec.process(a,{}, {'captured_ms':2000})
        rec.close('preview_closed')
        self.assertEqual(len(rec.record_ids),1)

    def test_automatic_partial_failure_retries_exact_batch_without_double_count(self):
        fx=self.fx;rec,a=self.recorder();rec.process(a,{}, {'captured_ms':0})
        a.active_ms=1000;event=fx.event()
        original=fx.store.append_event
        def committed_then_fail(*args,**kwargs):
            original(*args,**kwargs);raise StorageError('injected receipt loss')
        with patch.object(fx.store,'append_event',side_effect=committed_then_fail):
            rec.process(a,{'events':[event]},{'captured_ms':1000})
        self.assertTrue(rec.failed)
        result=rec.recover(fx.auth)
        self.assertEqual(result['save_state'],'clean')
        self.assertEqual(fx.store.snapshot(fx.user,rec.record_ids[0])['total_count'],1)

    def test_manual_photo_page_never_implicitly_opens_camera(self):
        content=self.fx.client.get('/realtime').get_data(as_text=True)
        self.assertIn('id="live-toggle"',content)
        self.assertIn('id="live-selection"',content)
        self.assertNotIn('src="/static/covers/',content)
        self.assertIn('hidden',content)

    def test_preview_recording_release_persists_while_preview_only_stays_ephemeral(self):
        from fitness_app.automatic_analysis import AutomaticAnalysis
        from test_formal_preview import pose
        fx=self.fx;manager=fx.app.extensions['preview_manager']
        with patch.object(manager,'start'):
            created=fx.post(fx.client,'/api/v1/previews',{'page_id':s.new_id(),'recognition_mode':'manual','action_id':'bodyweight_squat','capability_version':__import__('fitness_app.catalogue',fromlist=['get_action']).get_action('bodyweight_squat')['capability_version'],'save_training':True}).json['data']
        item=manager.active;rec=item['recorder'];a=SimpleNamespace(segment_id=s.new_id(),action='bodyweight_squat',version='manual-selection',active_ms=0)
        rec.process(a,{}, {'captured_ms':0});a.active_ms=1000;rec.process(a,{'events':[fx.event()]},{'captured_ms':1000})
        response=fx.client.delete('/api/v1/previews/'+created['preview_id'],json={'preview_token':created['preview_token'],'stream_epoch':created['stream_epoch']},
            headers={'X-CSRFToken':fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token'],'Idempotency-Key':s.new_id()})
        self.assertEqual(response.status_code,200,response.json)
        sid=response.json['data']['training']['saved_record_ids'][0]
        self.assertEqual(fx.store.snapshot(fx.user,sid)['status'],'ended_early')
        self.assertEqual(fx.store.snapshot(fx.user,sid)['config']['recognition_mode'],'manual')
        self.assertIsNone(fx.client.get('/api/v1/previews/'+created['preview_id']).json['data']['result'])
        manager.stop()

    def cached_barbell(self,selected):
        import numpy as np,hashlib
        from fitness_app.paths import ROOT
        from fitness_app.automatic_analysis import AutomaticAnalysis
        from fitness_app.frame_pipeline import analysis_framing
        from fitness_app.pose_input import input_policy_snapshot,ContentPoseService
        report=json.loads((ROOT/'data/verification/step-19/barbell-posture-review.json').read_text(encoding='utf8'))['current_input_recheck']
        self.assertEqual(report['input_policy'],input_policy_snapshot(ContentPoseService))
        actions={'press':'barbell_overhead_press','row':'barbell_bent_over_row','deadlift':'conventional_deadlift'}
        # 使用已固定的真实Full姿态，动作类型受控；只验新增保存/指导路径，不再跑模型或自称分类准确率。
        for name,case in report['cases'].items():
            if name!=selected:continue
            action=next((value for key,value in actions.items() if key in name),None)
            if action is None:continue
            self.assertEqual(hashlib.sha256((ROOT/case['path']).read_bytes()).hexdigest(),case['source_sha256'])
            recorder=AutomaticRecorder(self.fx.store,self.fx.user,self.fx.auth,s.new_id());pipeline=AutomaticAnalysis(recorder=recorder)
            with np.load(ROOT/case['pose_cache'],allow_pickle=False) as archive:
                meta=json.loads(str(archive['metadata']))
                stamps=archive['timestamps_ms'];points=archive['points'];counts=archive['person_counts']
                for i,t in enumerate(stamps):
                    width,height=map(int,archive['sizes'][i])
                    raw=[dict(zip(('x','y','z','visibility','presence'),map(float,p))) for p in points[i]]
                    frame={'frame_seq':i,'captured_ms':int(t),'width':width,'height':height,
                        'framing':analysis_framing(action,{'poses':[raw] if counts[i]==1 else []})}
                    pipeline.process(frame,{'available':True,'state':'recognized','action_id':action,'model_version':'fixed-real-cache-validation'})
                    self.assertFalse(recorder.failed,(getattr(recorder,'error_code',''),getattr(recorder,'error_detail',''),action,i))
            pipeline.reset('preview_closed')
            self.assertFalse(recorder.failed,getattr(recorder,'error_type',''))
            self.assertGreater(len(recorder.record_ids),0,action)
            for sid in recorder.record_ids:
                result=self.fx.store.snapshot(self.fx.user,sid,True)
                self.assertIn(result['status'],('ended_early','interrupted'))
                self.assertEqual(result['action_id'],action)
            print('new automatic persistence / cached real',action,'frames',len(stamps),'records',len(recorder.record_ids))

    def test_cached_real_press_persistence(self):self.cached_barbell('press')
    def test_cached_real_row_persistence(self):self.cached_barbell('row')
    def test_cached_real_deadlift_persistence(self):self.cached_barbell('deadlift_current_input')

    def test_failed_automatic_save_survives_refresh_and_is_owner_scoped(self):
        fx=self.fx;manager=fx.app.extensions['preview_manager']
        from fitness_app.catalogue import get_action
        with patch.object(manager,'start'):
            created=fx.post(fx.client,'/api/v1/previews',{'page_id':s.new_id(),'recognition_mode':'manual','action_id':'bodyweight_squat','capability_version':get_action('bodyweight_squat')['capability_version'],'save_training':True}).json['data']
        rec=manager.active['recorder'];a=SimpleNamespace(segment_id=s.new_id(),action='bodyweight_squat',version='manual-selection',active_ms=0)
        rec.process(a,{}, {'captured_ms':0});a.active_ms=1000
        with patch.object(fx.store,'append_event',side_effect=StorageError('injected')):
            rec.process(a,{'events':[fx.event()]},{'captured_ms':1000})
        sid=rec.record_ids[0]
        from fitness_app.auth import APIError
        with self.assertRaises(APIError) as rejected:
            manager.submit(fx.user,created['preview_id'],created['preview_token'],{'stream_epoch':created['stream_epoch'],'frame_seq':1,'captured_ms':1000,'width':32,'height':24},b'not-decoded-while-frozen')
        self.assertEqual(rejected.exception.code,'SAVE_FAILED')
        with self.assertRaises(APIError):manager.stop()
        self.assertEqual(fx.client.get('/api/v1/records/'+sid).json['data']['save_state'],'save_failed')
        self.assertEqual(len(fx.client.get('/api/v1/automatic-training/pending').json['data']['pending']),1)
        other=fx.app.test_client();fx.login(other,'Other_User')
        self.assertEqual(other.get('/api/v1/automatic-training/pending').json['data']['pending'],[])
        self.assertEqual(fx.post(other,'/api/v1/previews/'+created['preview_id']+'/save-recovery',{'owner_recovery':True}).status_code,404)
        fresh=fx.app.test_client();fx.login(fresh,'Record_User')
        recovered=fx.post(fresh,'/api/v1/previews/'+created['preview_id']+'/save-recovery',{'owner_recovery':True})
        self.assertEqual(recovered.json['data']['save_state'],'clean',recovered.json)
        self.assertEqual(fx.store.snapshot(fx.user,sid)['total_count'],1)
        self.assertEqual(fx.store.snapshot(fx.user,sid)['status'],'interrupted')
        self.assertEqual(fx.post(fresh,'/api/v1/previews/'+created['preview_id']+'/save-recovery',{'owner_recovery':True}).json['data']['save_state'],'clean')
        self.assertIsNone(manager.active)

    def test_html_not_found_preserves_api_error_contract(self):
        response=self.fx.client.get('/unknown-page')
        self.assertEqual(response.status_code,404);self.assertIn('返回首页',response.get_data(as_text=True))
        response=self.fx.client.get('/records/'+s.new_id())
        self.assertEqual(response.status_code,404);self.assertIn('返回首页',response.get_data(as_text=True))
        response=self.fx.client.get('/api/unknown')
        self.assertEqual(response.status_code,404);self.assertEqual(response.json['error']['code'],'NOT_FOUND')

    def test_pause_resume_keeps_run_identity_and_other_user_cannot_attach(self):
        from fitness_app.catalogue import get_action
        fx=self.fx;manager=fx.app.extensions['preview_manager']
        config={'page_id':s.new_id(),'recognition_mode':'manual','action_id':'bodyweight_squat','capability_version':get_action('bodyweight_squat')['capability_version'],'save_training':True}
        with patch.object(manager,'start'):
            first=fx.post(fx.client,'/api/v1/previews',config).json['data']
            rec=manager.active['recorder'];a=SimpleNamespace(segment_id=s.new_id(),action='bodyweight_squat',version='manual-selection',active_ms=0)
            rec.process(a,{}, {'captured_ms':0});a.active_ms=1000;rec.process(a,{'events':[fx.event()]},{'captured_ms':1000})
            manager.release(fx.user,first['preview_id'],{'preview_token':first['preview_token'],'stream_epoch':first['stream_epoch']},s.new_id())
            stranger=fx.app.test_client();fx.login(stranger,'Other_User')
            self.assertEqual(fx.post(stranger,'/api/v1/previews',{**config,'training_id':first['training_id']}).status_code,404)
            second=fx.post(fx.client,'/api/v1/previews',{**config,'training_id':first['training_id']}).json['data']
            self.assertEqual(first['training_id'],second['training_id'])
            self.assertEqual(manager.active['recorder'].record_ids,rec.record_ids)
            manager.release(fx.user,second['preview_id'],{'preview_token':second['preview_token'],'stream_epoch':second['stream_epoch']},s.new_id())
        manager.stop()

class AggregationTests(unittest.TestCase):
    def test_static_overlapping_observations_are_time_union(self):
        totals=interval_totals([dict(start_ms=0,end_ms=1000,status='issue'),dict(start_ms=500,end_ms=1500,status='issue'),
            dict(start_ms=1000,end_ms=2000,status='not_observed'),dict(start_ms=2000,end_ms=3000,status='unassessable')])
        self.assertEqual(totals,{'issue':1500,'evaluated':2000,'unknown':1000})
    def test_observation_only_issue_is_not_reported_as_zero_error(self):
        rows=[dict(id='a',action_id='barbell_overhead_press',started_at_ms=1,status='ended_early',definition_json=json.dumps({'issues':[{'code':'overhead_press.elbow','rule_version':'v1'}]}))]
        obs={'a':[dict(code='overhead_press.elbow',side='none',rule_version='v1',data_json=json.dumps({'status':'issue'}))]}
        result=aggregate_records(rows,{},obs,'overhead_press.elbow','none')
        self.assertIsNone(result['ratio']);self.assertEqual(result['issue_record_count'],1)
        self.assertEqual(result['unmeasured_records'],1)

    def test_rule_versions_and_sides_are_not_mixed(self):
        rows=[dict(id='a',action_id='standing_dumbbell_curl',started_at_ms=1,status='completed',definition_json=json.dumps({'issues':[{'code':'curl.range','rule_version':'v1'}]})),
              dict(id='b',action_id='standing_dumbbell_curl',started_at_ms=2,status='completed',definition_json=json.dumps({'issues':[{'code':'curl.range','rule_version':'v2'}]}))]
        def e(side,status,version):return dict(event_seq=1,side=side,assessment_json=json.dumps([{'code':'curl.range','status':status,'rule_version':version}]))
        data=aggregate_records(rows,{'a':[e('left','issue','v1')],'b':[e('right','issue','v2'),e('left','not_observed','v2')]},{},'curl.range','left')
        self.assertEqual((data['ratio'],data['rule_version'],data['excluded_version_records']),(0,'v2',1))
        self.assertEqual(len(data['trend']),1)

if __name__=='__main__':unittest.main()
