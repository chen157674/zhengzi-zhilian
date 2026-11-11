"""姿态参考链新增检查；几何用例证明规则行为，不宣称真实识别准确率。"""
import copy
import hashlib
import io
import json
import sqlite3
from contextlib import closing
import unittest
from unittest.mock import patch

from fitness_app.auth import APIError
from fitness_app.catalogue import definitions, get_action
from fitness_app.posture_engine import PostureEngine, create_analysis_engine
from fitness_app.posture_rules import policy_version,RULES
from fitness_app import db_schema as s
from fitness_app.database import Database, StorageError, ConflictError
from test_quality_candidates import fixture
from test_barbell_quality import press
import test_formal_records as record_fixture
from test_quality_candidates import LEVELS
from test_glute_bridge import geometry as bridge_frame
from test_plank_hold import geometry as plank_frame
from test_barbell_row import geometry as row_frame
from test_barbell_quality import cycle as deadlift_cycle
from test_movement_quality import jack as jack_frames
from test_high_knees import geometry as knee_frame
from fitness_app.paths import ROOT
from fitness_app.frame_pipeline import framing


class EventSource:
    def __init__(self, event):
        self.event = {**event,'algorithm_version':'D-POSE-event','feature_schema_version':'D-POSE'}
    def reset(self, reason):
        pass
    def process(self, frame, context):
        return {'events':[copy.deepcopy(self.event)] if frame['captured_ms']==self.event['end_ms'] else [],
                'observation':{}}


def result(action='bodyweight_squat', shallow=True, view='side', missing=False):
    frames, event, _ = fixture(action, shallow)
    if missing:
        for index in (11,12):frames[10]['framing']['landmarks'][index]['valid']=False
    engine=PostureEngine(action,EventSource(event))
    outputs=[engine.process(f,{'coaching_view':view,'stream_epoch':'test'}) for f in frames]
    return engine,outputs[-1],frames


class PostureEngineTests(unittest.TestCase):
    def test_all_actions_have_reference_posture_without_fake_verification(self):
        items=definitions()
        self.assertEqual(len(items),33)
        self.assertEqual(sum(len(x['issues']) for x in items),len(RULES))
        self.assertTrue(all(i['status']=='reference' for a in items for i in a['issues']))
        for item in items:
            engine=create_analysis_engine(item['action_id'])
            self.assertIsNotNone(engine)
            self.assertEqual(engine.counting_available,item['counting_available'])
        self.assertFalse(create_analysis_engine('barbell_overhead_press').counting_available)

    def test_shallow_squat_reports_issue_advice_without_removing_count(self):
        _,produced,_=result()
        self.assertEqual(len(produced['events']),1)
        item=produced['events'][0]['assessments'][0]
        self.assertEqual(item['status'],'issue')
        self.assertEqual(item['body_parts'],['髋','膝'])
        self.assertTrue(item['suggestion'])
        self.assertGreater(item['evidence']['value'],item['evidence']['threshold'])
        self.assertEqual(item['evidence_level'],'reference')
        _,normal,_=result(shallow=False)
        self.assertEqual(normal['events'][0]['assessments'][0]['status'],'not_observed')

    def test_view_and_missing_parts_are_unknown_not_normal(self):
        for kwargs in ({'view':'front'},{'missing':True}):
            _,produced,_=result(**kwargs)
            item=produced['events'][0]['assessments'][0]
            self.assertEqual(item['status'],'unassessable')
            self.assertTrue(item['reason'])
            self.assertTrue(item['suggestion'])

    def test_curl_assessment_stays_on_event_side(self):
        _,produced,_=result('standing_dumbbell_curl',view='oblique')
        event=produced['events'][0]
        self.assertEqual(event['side'],'left')
        self.assertEqual(event['assessments'][0]['code'],'curl.range')
        self.assertEqual(event['assessments'][0]['status'],'issue')

    def test_press_requires_sustained_rack_deviation_and_keeps_counts_unavailable(self):
        engine=create_analysis_engine('barbell_overhead_press');intervals=[]
        for i in range(13):
            frame=press(.12);frame.update(frame_seq=i,captured_ms=i*100)
            produced=engine.process(frame,{'coaching_view':'front','stream_epoch':'press'})
            self.assertEqual(produced['events'],[])
            if i<7:
                self.assertFalse(any(x['status']=='issue' for x in produced.get('posture_findings',[])))
            intervals.extend(produced.get('posture_findings',[]))
        self.assertTrue(any(x['status']=='issue' and x['code']=='overhead_press.elbow' for x in intervals))
        self.assertFalse(engine.counting_available)
        engine.reset('paused')
        frame=press(.12);frame.update(frame_seq=20,captured_ms=2000)
        self.assertFalse(engine.process(frame,{'coaching_view':'front','stream_epoch':'press'}).get('intervals'))

    def test_duplicate_and_retired_streams_cannot_add_quality(self):
        engine=create_analysis_engine('barbell_overhead_press')
        for epoch in ('a','b'):
            frame=press(.12);frame.update(frame_seq=0,captured_ms=0)
            engine.process(frame,{'coaching_view':'front','stream_epoch':epoch})
        frame=press(.12);frame.update(frame_seq=100,captured_ms=10000)
        stale=engine.process(frame,{'coaching_view':'front','stream_epoch':'a'})
        self.assertEqual(stale['observation']['posture']['reason'],'stale_stream')
        self.assertFalse(stale.get('intervals'))


class PostureRuleCoverageTests(unittest.TestCase):
    def evaluate(self,action,frames,view,side='none'):
        engine=PostureEngine(action,None)
        engine.frames.extend(frames)
        return engine._cycle({'side':side,'start_ms':frames[0]['captured_ms'],'end_ms':frames[-1]['captured_ms']},view,'user_setting')

    def test_push_up_shoulders_approach_elbow_height_at_bottom(self):
        def frames(amount):
            values=[]
            for i,level in enumerate(LEVELS):
                f=plank_frame(i)
                for side in (0,1):
                    p=f['framing']['landmarks'];offset=side*.015
                    p[11+side].update(x=.3,y=.4+.2*level*amount+offset)
                    p[13+side].update(x=.5,y=.6+offset)
                    p[15+side].update(x=.5,y=.8+offset)
                values.append(f)
            return values
        normal=self.evaluate('standard_push_up',frames(1),'side')[0]
        shallow=self.evaluate('standard_push_up',frames(.65),'side')[0]
        self.assertEqual(normal['status'],'not_observed',normal)
        self.assertEqual(shallow['status'],'issue',shallow)
        self.assertGreater(shallow['evidence']['value'],.25)

    def test_lunge_bridge_and_situp_reference_geometry(self):
        for action,view,side in (('reverse_lunge','oblique','left'),('bent_knee_sit_up','side','none')):
            normal=fixture(action,False)[0];bad=fixture(action,True)[0]
            self.assertEqual(self.evaluate(action,normal,view,side)[0]['status'],'not_observed')
            self.assertEqual(self.evaluate(action,bad,view,side)[0]['status'],'issue')
        aligned=[bridge_frame(v*.75,i) for i,v in enumerate(LEVELS)]
        low=[bridge_frame(v*.25,i) for i,v in enumerate(LEVELS)]
        self.assertEqual(self.evaluate('bodyweight_glute_bridge',aligned,'side')[0]['status'],'not_observed')
        self.assertEqual(self.evaluate('bodyweight_glute_bridge',low,'side')[0]['status'],'issue')

    def test_plank_high_low_normal_and_preserved_holding(self):
        from fitness_app.counting_resources import create_counter
        for y,expected in ((.25,{'plank.hip_high'}),(.58,{'plank.hip_low'}),(.487719298,set())):
            engine=create_analysis_engine('forearm_plank');plain=create_counter('forearm_plank')
            problems=set();held=plain_held=0;normal_seen=False
            for i in range(25):
                frame=plank_frame(i,hip_y=y);context={'coaching_view':'side','stream_epoch':'plank'}
                out=engine.process(copy.deepcopy(frame),context);baseline=plain.process(copy.deepcopy(frame),context)
                held+=sum(x['end_ms']-x['start_ms'] for x in out.get('intervals',[]) if x['kind']=='hold')
                plain_held+=sum(x['end_ms']-x['start_ms'] for x in baseline.get('intervals',[]) if x['kind']=='hold')
                for item in out.get('posture_findings',[]):
                    if item['status']=='issue':problems.add(item['code'])
                    normal_seen|=item['status']=='not_observed'
            self.assertEqual(problems,expected)
            self.assertEqual(held,plain_held)
            self.assertGreater(held,0)
            if not expected:self.assertTrue(normal_seen)

    def test_jack_arms_and_legs_assessed_separately(self):
        normal=self.evaluate('jumping_jack',jack_frames(),'front')
        self.assertTrue(all(x['status']=='not_observed' for x in normal),normal)
        short=self.evaluate('jumping_jack',jack_frames(.5,.05),'front')
        self.assertEqual({x['code'] for x in short if x['status']=='issue'},
                         {'jumping_jack.arm_range','jumping_jack.leg_range'})

    def test_high_knee_side_height_and_deadlift_joint_top(self):
        full=[knee_frame(v,seq=i) for i,v in enumerate(LEVELS)]
        low=[knee_frame(v*.3,seq=i) for i,v in enumerate(LEVELS)]
        self.assertEqual(self.evaluate('high_knees',full,'front','left')[0]['status'],'not_observed')
        self.assertEqual(self.evaluate('high_knees',low,'front','left')[0]['status'],'issue')
        self.assertEqual(self.evaluate('conventional_deadlift',deadlift_cycle(),'side')[0]['status'],'not_observed')
        self.assertEqual(self.evaluate('conventional_deadlift',deadlift_cycle(145,175),'side')[0]['status'],'issue')

    def test_row_trunk_change_not_absolute_angle_or_spine_curvature(self):
        stable=[row_frame(v,i) for i,v in enumerate(LEVELS)]
        swinging=[row_frame(v,i,sway=.12*v) for i,v in enumerate(LEVELS)]
        good=self.evaluate('barbell_bent_over_row',stable,'side')[0]
        bad=self.evaluate('barbell_bent_over_row',swinging,'side')[0]
        self.assertEqual(good['status'],'not_observed')
        self.assertEqual(bad['status'],'issue')
        self.assertGreater(bad['evidence']['trunk_change_degrees'],15)
        self.assertIn('稳定躯干',bad['suggestion'])


class PostureStorageTests(unittest.TestCase):
    def setUp(self):
        self.fx=record_fixture.RecordTests()
        self.fx.setUp()
    def tearDown(self):
        self.fx.tearDown()

    def test_reference_findings_roundtrip_and_cross_account_isolation(self):
        fx=self.fx;sid=fx.running();_,produced,_=result()
        event=produced['events'][0]
        payload={**event,'event_seq':1,'set_index':1,'active_ms':event['end_ms']}
        fx.store.append_event(fx.user,sid,payload)
        fx.store.append_event(fx.user,sid,payload)
        response=fx.client.get('/api/v1/records/'+sid+'/posture')
        self.assertEqual(response.status_code,200,response.json)
        data=response.json['data']
        self.assertEqual(data['count'],1)
        self.assertEqual(data['issue_count'],1)
        self.assertEqual(data['validation_status'],'not_independently_validated')
        self.assertTrue(data['findings'][0]['suggestion'])
        other=fx.app.test_client();fx.login(other,'Other_User')
        self.assertEqual(other.get('/api/v1/records/'+sid+'/posture').status_code,404)

    def test_wrong_rule_version_and_unmarked_reference_cannot_persist(self):
        fx=self.fx;sid=fx.running();_,produced,_=result()
        event={**produced['events'][0],'event_seq':1,'set_index':1}
        wrong=copy.deepcopy(event);wrong['assessments'][0]['rule_version']='made-up'
        with self.assertRaises(APIError):
            fx.store.append_event(fx.user,sid,wrong)
        wrong=copy.deepcopy(event);wrong['assessments'][0].pop('evidence_level')
        with self.assertRaises(APIError):
            fx.store.append_event(fx.user,sid,wrong)
        self.assertEqual(fx.store.snapshot(fx.user,sid)['total_count'],0)

    def test_posture_only_observations_do_not_create_repetitions_or_holds(self):
        fx=self.fx;sid=fx.running('barbell_overhead_press')
        engine=create_analysis_engine('barbell_overhead_press')
        for i in range(12):
            frame=press(.12);frame.update(frame_seq=i,captured_ms=i*100)
            produced=engine.process(frame,{'coaching_view':'front','stream_epoch':'press'})
            if produced.get('posture_findings'):fx.writer.submit(fx.user,sid,fx.auth,'posture',{'set_index':1,'findings':produced['posture_findings'],'active_ms':i*100})
        data=fx.client.get('/api/v1/records/'+sid+'/posture').json['data']
        self.assertIsNone(data['count'])
        self.assertEqual(data['issue_count'],1)
        snapshot=fx.store.snapshot(fx.user,sid)
        self.assertEqual((snapshot['total_count'],snapshot['hold_ms']),(0,0))
        self.assertEqual(snapshot['completed_sets'],0)

    def test_rules_route_and_invalid_view(self):
        fx=self.fx
        data=fx.client.get('/api/v1/posture/rules').json['data']
        self.assertFalse(data['muscle_force_assessment'])
        self.assertEqual(len(data['actions']),33)
        for view in ([],None,'camera-guessed'):
            response=fx.post(fx.client,'/api/v1/sessions',{'action_id':'bodyweight_squat',
                'capability_version':get_action('bodyweight_squat')['capability_version'],
                'source':'camera','mode':'free','target':None,'coaching_view':view})
            self.assertEqual(response.status_code,422,response.json)

    def test_live_observation_evolves_without_duplicate_or_wrong_normal(self):
        fx=self.fx;sid=fx.running('barbell_overhead_press')
        engine=create_analysis_engine('barbell_overhead_press')
        for i in range(15):
            frame=press(.12 if i<10 else 0);frame.update(frame_seq=i,captured_ms=i*100)
            produced=engine.process(frame,{'coaching_view':'front','stream_epoch':'press'})
            if produced.get('posture_findings'):
                payload={'set_index':1,'findings':produced['posture_findings'],'active_ms':i*100}
                fx.writer.submit(fx.user,sid,fx.auth,'posture',payload)
                fx.writer.submit(fx.user,sid,fx.auth,'posture',payload)
        saved=fx.store.snapshot(fx.user,sid,True)['posture_observations']
        self.assertEqual(len(saved),2)
        self.assertEqual([x['status'] for x in saved],['issue','not_observed'])
        self.assertEqual(saved[0]['end_ms'],900)
        self.assertEqual(saved[1]['start_ms'],1000)
        self.assertEqual(saved[0]['evidence']['affected_sides'],['left','right'])
        older={**saved[1],'end_ms':1200}
        with self.assertRaises(ConflictError):
            fx.store.append_posture(fx.user,sid,1,[older])

    def test_posture_save_failure_freezes_and_commit_receipt_recovers(self):
        fx=self.fx;sid=fx.running('barbell_overhead_press')
        engine=create_analysis_engine('barbell_overhead_press')
        for i in range(10):
            frame=press(.12);frame.update(frame_seq=i,captured_ms=i*100)
            produced=engine.process(frame,{'coaching_view':'front','stream_epoch':'press'})
        payload={'set_index':1,'findings':produced['posture_findings'],'active_ms':900}
        def fail_before(stage):
            if stage=='before_commit':raise StorageError('D-FAULT posture commit')
        fx.store.fault=fail_before
        failed=fx.writer.submit(fx.user,sid,fx.auth,'posture',payload)
        self.assertEqual(failed['save_state'],'save_failed')
        self.assertEqual(fx.store.snapshot(fx.user,sid,True)['posture_observations'],[])
        fx.store.fault=None
        recovered=fx.writer.recovery_command(fx.user,sid,fx.auth,s.new_id(),{'command':'retry_save','expected_version':failed['state_version']})
        self.assertEqual(recovered['save_state'],'clean')
        self.assertEqual(len(fx.store.snapshot(fx.user,sid,True)['posture_observations']),1)
        fx.writer.release(fx.user,sid)  # 模拟显式继续成功后解除冻结。
        frame=press(.12);frame.update(frame_seq=10,captured_ms=1000)
        next_result=engine.process(frame,{'coaching_view':'front','stream_epoch':'press'})
        def fail_after(stage):
            if stage=='after_commit':raise StorageError('D-FAULT lost acknowledgment')
        fx.store.fault=fail_after
        saved=fx.writer.submit(fx.user,sid,fx.auth,'posture',{'set_index':1,'findings':next_result['posture_findings'],'active_ms':1000})
        self.assertEqual(saved['save_state'],'clean')
        self.assertEqual(fx.store.snapshot(fx.user,sid,True)['posture_observations'][0]['end_ms'],1000)
        fx.store.fault=None

    def _legacy(self):
        from legacy_schema_support import legacy_shape
        legacy_shape(self.fx.db,1)

    def test_schema_one_migrates_additively_and_preserves_existing_count(self):
        fx=self.fx;sid=fx.running()
        fx.store.append_event(fx.user,sid,fx.event())
        self._legacy()
        database=Database(fx.db.path)
        try:
            self.assertEqual(database.initialize(allow_round5_migration=True)['schema_version'],s.SCHEMA_VERSION)
            self.assertEqual(fx.store.snapshot(fx.user,sid,True)['total_count'],1)
            self.assertEqual(fx.store.snapshot(fx.user,sid,True)['posture_observations'],[])
        finally:
            database.dispose()

    def test_failed_migration_rolls_back_without_replacing_old_database(self):
        fx=self.fx;sid=fx.running();self._legacy()
        database=Database(fx.db.path);original=s.posture_observations.create
        def failed(connection):
            original(connection)
            raise RuntimeError('D-FAULT between table creation and version change')
        try:
            with patch.object(s.posture_observations,'create',side_effect=failed):
                with self.assertRaises(StorageError):
                    database.initialize(allow_round5_migration=True)
            self.assertTrue(fx.db.path.exists())
            with closing(sqlite3.connect(str(fx.db.path))) as connection, connection:
                self.assertEqual(connection.execute('SELECT schema_version FROM app_meta').fetchone()[0],1)
                self.assertEqual(connection.execute("SELECT count(*) FROM sqlite_master WHERE name='posture_observations'").fetchone()[0],0)
                self.assertEqual(connection.execute('SELECT count(*) FROM analysis_sessions WHERE id=?',(sid,)).fetchone()[0],1)
        finally:
            database.dispose()


class PosturePipelineTests(unittest.TestCase):
    def setUp(self):
        self.fx=record_fixture.RecordTests();self.fx.setUp()
        self.manager=self.fx.app.extensions['session_manager']
        self.jobs=self.fx.app.extensions['video_jobs']
        self.media=self.fx.app.extensions['media_inputs']

    def tearDown(self):
        for name in ('video_jobs','session_manager','preview_manager','media_inputs'):
            self.fx.app.extensions[name].stop()
        self.fx.tearDown()

    def create(self,action,source='camera',input_id=None):
        config={'action_id':action,'capability_version':get_action(action)['capability_version'],
                'source':source,'mode':'free','target':None,
                'coaching_view':'front' if action=='barbell_overhead_press' else 'side'}
        if input_id:config['input_id']=input_id
        response=self.fx.post(self.fx.client,'/api/v1/sessions',config)
        self.assertEqual(response.status_code,201,response.json)
        return response.json['data']['session_id']

    def runtime(self):
        sid=self.create('barbell_overhead_press');fx=self.fx
        self.manager.claim(fx.user,fx.auth,fx.now[0]+3600000,sid,s.new_id(),
                           {'page_id':s.new_id(),'takeover':False,'expected_version':0})
        rt=self.manager.live[sid]
        rt.data=fx.store.set_state(fx.user,sid,'running',s.new_id())
        rt.adapter=self.manager.engine_provider('barbell_overhead_press')
        rt.session_origin_mono=0
        return sid,rt

    def consume_press(self,rt,seq):
        frame=press(.12);frame.update(frame_seq=seq,captured_ms=seq*100)
        quality=framing('barbell_overhead_press',{'poses':[frame['framing']['landmarks']]})
        quality['ready_for_tracking']=quality['usable']
        self.fx.mono[0]=seq*.1
        self.manager._consume(rt,{**frame,'framing':quality})

    def test_realtime_reference_feedback_saved_before_publishing_without_fake_counts(self):
        sid,rt=self.runtime()
        for i in range(12):self.consume_press(rt,i)
        data=self.manager.decorate_snapshot(self.fx.user,rt.data)
        self.assertFalse(data['counting_available'])
        self.assertEqual(data['total_count'],0)
        self.assertEqual(len(data['feedback']),1,data)
        item=data['feedback'][0]
        self.assertTrue(item['message']);self.assertTrue(item['suggestion'])
        self.assertGreater(item['evidence']['value'],item['evidence']['threshold'])
        response=self.fx.client.get('/api/v1/records/'+sid)
        self.assertEqual(len(response.json['data']['posture_observations']),1)
        self.assertEqual(response.json['data']['posture']['issue_count'],1)

    def test_realtime_failed_posture_commit_clears_feedback_and_freezes_analysis(self):
        sid,rt=self.runtime()
        for i in range(9):self.consume_press(rt,i)
        self.assertTrue(rt.feedback)
        def fault(stage):
            if stage=='before_commit':raise StorageError('D-FAULT live posture save')
        self.fx.store.fault=fault
        self.consume_press(rt,9)
        self.assertTrue(rt.frozen)
        self.assertFalse(self.manager.decorate_snapshot(self.fx.user,rt.data)['feedback'])
        self.assertLess(self.fx.store.snapshot(self.fx.user,sid,True)['posture_observations'][0]['end_ms'],900)
        self.fx.store.fault=None
        self.fx.writer.recovery_command(self.fx.user,sid,self.fx.auth,s.new_id(),
            {'command':'retry_save','expected_version':rt.data['state_version']})

    def test_actual_video_full_model_persists_posture_and_history_without_touching_source(self):
        source=ROOT/'data/datasets/mydeadlift/Gerakan Benar/GB_S02_R29.mp4'
        source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
        with source.open('rb') as stream:uploaded=self.media.upload(self.fx.user,self.fx.auth,stream,s.new_id())
        iid=uploaded['input_id'];self.media.threads[iid].join(10)
        self.assertEqual(self.media.snapshot(self.fx.user,iid)['status'],'ready')
        sid=self.create('conventional_deadlift','video',iid)
        response=self.fx.post(self.fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0})
        self.assertEqual(response.status_code,202,response.json)
        jid=response.json['data']['job_id'];self.jobs.runs[jid].thread.join(45)
        self.assertFalse(self.jobs.runs[jid].thread.is_alive())
        job=self.jobs.snapshot(self.fx.user,jid)
        self.assertEqual(job['status'],'completed',job)
        record=self.fx.store.snapshot(self.fx.user,sid,True)
        report=self.fx.client.get('/api/v1/records/'+sid+'/posture').json['data']
        self.assertEqual(record['total_count'],1)
        self.assertEqual(len(report['findings']),1)
        item=report['findings'][0]
        self.assertEqual(item['code'],'deadlift.extension')
        self.assertEqual(item['rule_version'],policy_version())
        self.assertEqual(item['evidence_level'],'reference')
        self.assertTrue(item['message'])
        self.assertEqual(report['time_base'],'source_ms')
        self.assertFalse(self.media._path(iid).exists())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),source_hash)
        evidence={'checks':'passed','scope':'实际上传、解码、Full 模型、姿态规则、保存及历史 API；单片段接线检查，不是独立纠错准确率',
                  'source_path':source.relative_to(ROOT).as_posix(),'source_sha256':source_hash,
                  'frames':self.jobs.runs[jid].frames,'count':record['total_count'],'findings':report['findings'],
                  'input_policy':record['input_policy'],'schema_version':s.SCHEMA_VERSION,
                  'source_preserved':True,'owned_upload_removed':True}
        (ROOT/'data/verification/step-19/backend-posture.json').write_text(
            json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

    def test_video_posture_only_uses_reference_engine_and_reports_count_unavailable(self):
        from test_formal_media import sample_mp4
        class RackPose:
            def __init__(self,**unused):pass
            def detect(self,rgb,stamp):
                return {'poses':[press(.12)['framing']['landmarks']],'model':'D-POSE-rack'}
            def close(self):pass
        self.jobs.pose_factory=RackPose
        uploaded=self.media.upload(self.fx.user,self.fx.auth,io.BytesIO(sample_mp4(frames=20)),s.new_id())
        iid=uploaded['input_id'];self.media.threads[iid].join(5)
        sid=self.create('barbell_overhead_press','video',iid)
        response=self.fx.post(self.fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0})
        self.assertEqual(response.status_code,202,response.json)
        jid=response.json['data']['job_id'];self.jobs.runs[jid].thread.join(10)
        self.assertFalse(self.jobs.runs[jid].thread.is_alive())
        job=self.jobs.snapshot(self.fx.user,jid)
        self.assertEqual(job['status'],'completed',job)
        self.assertFalse(job['counting_available'])
        report=self.fx.client.get('/api/v1/records/'+sid+'/posture').json['data']
        self.assertIsNone(report['count'])
        self.assertEqual(report['issue_count'],1,report)
        self.assertEqual(report['findings'][0]['code'],'overhead_press.elbow')
        self.assertEqual(job['session']['total_count'],0)
        self.assertFalse(self.media._path(iid).exists())


if __name__=='__main__':
    unittest.main()
