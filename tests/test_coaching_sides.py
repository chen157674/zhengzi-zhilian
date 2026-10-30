"""同帧分侧完成及部位高亮，只验证受影响指导链。"""
import copy
import threading
import unittest
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.coaching import coaching_observation,coaching_snapshot,committed_issues
from fitness_app.frame_pipeline import framing
from fitness_app.posture_rules import rules_for,policy_version
from fitness_app.skeleton import skeleton_snapshot
from fitness_app.database import StorageError
import test_formal_records as records
import test_formal_video_jobs as video


def dual_output(stamp=100):
    spec=rules_for('standing_dumbbell_curl')[0];events=[]
    for side,status in (('left','issue'),('right','not_observed')):
        item={'code':'curl.range','status':status,'reason':'','start_ms':0,'end_ms':stamp,
              'rule_version':policy_version(),'evidence_level':'reference','body_parts':['肘'],
              'message':'左侧屈肘幅度偏小' if status=='issue' else '本项未观察到偏差',
              'suggestion':spec['suggestion'] if status=='issue' else '', 'evidence':{'fixture':'D-SIDE-batch'}}
        events.append({'side':side,'start_ms':0,'end_ms':stamp,'algorithm_version':'D-SIDE','feature_schema_version':'D-SIDE','assessments':[item]})
    return {'events':events,'observation':{'posture':{'view':'oblique','items':[copy.deepcopy(e['assessments'][0]) for e in events]}}}


class DualEvents:
    counting_available=True
    def reset(self,reason):pass
    def process(self,frame,context):return dual_output(frame['captured_ms']) if frame['captured_ms']==100 else {'events':[],'observation':{}}


class CoachingSideTests(unittest.TestCase):
    def test_other_side_nonissue_does_not_withdraw_committed_issue(self):
        output=dual_output();before=copy.deepcopy(output)
        frame={'framing':{'usable':True,'code':'VISIBLE'},'analysis':coaching_observation(output)}
        state={'status':'running','save_state':'clean','supported_items':rules_for('standing_dumbbell_curl')}
        cue=coaching_snapshot(state,frame,committed_issues(output))
        self.assertEqual(cue['state'],'needs_adjustment');self.assertEqual(cue['primary_cue']['side'],'left')
        self.assertEqual(output,before)
        frame['analysis']['posture']['items'][0]['status']='not_observed'
        self.assertIsNone(coaching_snapshot(state,frame,committed_issues(output))['primary_cue'])

    def test_realtime_both_commits_keep_left_issue_after_right_nonissue(self):
        fx=records.RecordTests();fx.setUp();manager=fx.app.extensions['session_manager']
        try:
            sid=fx.create('standing_dumbbell_curl')
            manager.claim(fx.user,fx.auth,fx.now[0]+3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
            rt=manager.live[sid];rt.data=fx.store.set_state(fx.user,sid,'running',s.new_id())
            rt.adapter=DualEvents();rt.session_origin_mono=0;rt.stream_time_offset_ms=0
            for i in range(2):
                q=framing('standing_dumbbell_curl',video.SyntheticPose().detect(None,i*100));q['ready_for_tracking']=True
                fx.mono[0]=i*.1
                manager._consume(rt,{'captured_ms':i*100,'frame_seq':i,'width':640,'height':480,'framing':q})
            response=fx.client.get('/api/v1/sessions/'+sid);self.assertEqual(response.status_code,200)
            data=response.json['data'];self.assertEqual(data['total_count'],2)
            self.assertEqual(data['coaching']['primary_cue']['side'],'left')
            self.assertEqual(data['skeleton']['highlight']['joint_indices'],[13])
            saved=fx.store.snapshot(fx.user,sid,True)
            self.assertEqual([e['side'] for e in saved['events']],['left','right'])
        finally:manager.stop();fx.tearDown()

    def test_video_both_sides_use_same_guidance_and_saved_events(self):
        fx=video.VideoTests();fx.setUp();gate=threading.Event();entered=threading.Event();fx.gates.append(gate)
        class Blocking(video.SyntheticPose):
            def detect(self,rgb,stamp):
                if stamp==200:entered.set();gate.wait(10)
                return super().detect(rgb,stamp)
        try:
            import io
            from test_formal_media import sample_mp4
            media=fx.media;info=media.upload(fx.fx.user,fx.fx.auth,io.BytesIO(sample_mp4()),s.new_id())
            iid=info['input_id'];media.threads[iid].join(5)
            action='standing_dumbbell_curl'
            created=fx.fx.post(fx.client,'/api/v1/sessions',dict(action_id=action,capability_version=get_action(action)['capability_version'],source='video',mode='free',target=None,input_id=iid))
            self.assertEqual(created.status_code,201);sid=created.json['data']['session_id']
            fx.manager.pose_factory=Blocking;fx.manager.engine_provider=lambda action:DualEvents()
            jid=fx.start(sid);self.assertTrue(entered.wait(10))
            data=fx.client.get('/api/v1/jobs/'+jid).json['data']
            self.assertEqual(data['session']['total_count'],2)
            self.assertEqual(data['coaching']['primary_cue']['side'],'left')
            self.assertEqual(data['skeleton']['highlight']['joint_indices'],[13])
            gate.set();self.assertEqual(fx.wait(jid)['status'],'completed')
        finally:gate.set();fx.tearDown()

    def test_second_side_save_failure_keeps_first_record_but_freezes_guidance(self):
        fx=records.RecordTests();fx.setUp();manager=fx.app.extensions['session_manager']
        try:
            sid=fx.create('standing_dumbbell_curl')
            manager.claim(fx.user,fx.auth,fx.now[0]+3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
            rt=manager.live[sid];rt.data=fx.store.set_state(fx.user,sid,'running',s.new_id())
            rt.adapter=DualEvents();rt.session_origin_mono=0;rt.stream_time_offset_ms=0
            commits=[]
            def fail_second(stage):
                if stage=='before_commit':
                    commits.append(stage)
                    if len(commits)==2:raise StorageError('D-SIDE-second-event')
            fx.store.fault=fail_second
            q=framing('standing_dumbbell_curl',video.SyntheticPose().detect(None,100));q['ready_for_tracking']=True
            manager._consume(rt,{'captured_ms':100,'frame_seq':1,'width':640,'height':480,'framing':q})
            data=manager.decorate_snapshot(fx.user,rt.data)
            self.assertEqual(data['coaching']['state'],'save_failed');self.assertIsNone(data['coaching']['primary_cue'])
            self.assertEqual(fx.store.snapshot(fx.user,sid)['total_count'],1)
            fx.store.fault=None
            fx.writer.recovery_command(fx.user,sid,fx.auth,s.new_id(),{'command':'retry_save','expected_version':rt.data['state_version']})
            self.assertEqual(fx.store.snapshot(fx.user,sid)['total_count'],2)
        finally:fx.store.fault=None;manager.stop();fx.tearDown()

    def test_press_affected_side_and_deadlift_limited_joint_are_not_overhighlighted(self):
        frame={'framing':framing('bodyweight_squat',video.SyntheticPose().detect(None,100))}
        cue={'code':'overhead_press.elbow','side':'none','evidence':{'affected_sides':['right']}}
        result=skeleton_snapshot(frame,{'state':'needs_adjustment','primary_cue':cue})
        self.assertEqual(result['highlight']['joint_indices'],[14,16])
        self.assertEqual(result['highlight']['affected_sides'],['right'])
        cue={'code':'deadlift.extension','side':'none','evidence':{'measurement_side':'left','limited_joints':['膝']}}
        result=skeleton_snapshot(frame,{'state':'needs_adjustment','primary_cue':cue})
        self.assertEqual(result['highlight']['joint_indices'],[25,26])
        self.assertEqual(result['highlight']['affected_sides'],[],'测量侧不是已证实的出错侧')


if __name__=='__main__':unittest.main()
