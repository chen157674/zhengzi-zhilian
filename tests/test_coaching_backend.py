"""面向用户的状态、主要提示、身体连线规则和提交边界。"""
import copy
import threading
import unittest
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.coaching import coaching_snapshot
from fitness_app.posture_engine import PostureEngine
from fitness_app.posture_rules import rules_for,policy_version
from fitness_app.database import StorageError
from fitness_app.frame_pipeline import framing
from test_plank_hold import geometry
from test_posture_backend import EventSource
import test_formal_records as shared
import test_formal_video_jobs as videos


class CoachingLogicTests(unittest.TestCase):
    def state(self,**changes):return {'status':'running','save_state':'clean','action_id':'standard_push_up','supported_items':rules_for('standard_push_up'),**changes}
    def frame(self,code='VISIBLE',items=None):
        return {'frame_seq':1,'captured_ms':100,'framing':{'usable':code=='VISIBLE','code':code},
                'analysis':{'posture':{'view':'side','items':items or []},'sides':{'none':{'phase':'moving'}}}}

    def test_input_control_and_save_states_never_claim_overall_correctness(self):
        for code,expected in [('NO_PERSON','no_person'),('TARGET_AMBIGUOUS','multiple_people'),('MISSING_PARTS','positioning')]:
            result=coaching_snapshot(self.state(),self.frame(code));self.assertEqual(result['state'],expected);self.assertIsNone(result['overall_correctness'])
        for status,expected in [('paused','paused'),('resting','resting'),('countdown','preparing'),('completed','ended'),('failed','interrupted')]:
            self.assertEqual(coaching_snapshot(self.state(status=status),self.frame())['state'],expected)
        self.assertEqual(coaching_snapshot(self.state(save_state='save_failed'),self.frame())['state'],'save_failed')
        self.assertEqual(coaching_snapshot(self.state(),self.frame(),frame_fresh=False)['state'],'waiting_for_input')

    def test_primary_alignment_cue_is_stable_prioritized_and_expires(self):
        rules={r['code']:r for r in rules_for('standard_push_up')}
        def issue(code):
            r=rules[code];return {'prompt_id':code,'message':r['title'],'suggestion':r['suggestion'],'side':'none',
                                  'start_ms':10,'end_ms':200,'rule_version':r['rule_version'],'evidence_level':'reference'}
        feedback=[issue('push_up.range'),issue('push_up.hip_low')]
        result=coaching_snapshot(self.state(),self.frame(),feedback)
        self.assertEqual(result['state'],'needs_adjustment');self.assertEqual(result['primary_cue']['code'],'push_up.hip_low')
        self.assertEqual(result['other_cue_count'],1)
        changed=copy.deepcopy(feedback);changed[1]['end_ms']=300
        self.assertEqual(result['primary_cue']['cue_id'],coaching_snapshot(self.state(),self.frame(),changed)['primary_cue']['cue_id'])
        self.assertIsNone(coaching_snapshot(self.state(),self.frame(),feedback,2501)['primary_cue'])
        wrong_view=self.frame();wrong_view['analysis']['posture']['view']='front'
        self.assertEqual(coaching_snapshot(self.state(),wrong_view,feedback)['state'],'positioning')

    def test_unassessable_cycle_has_reason_and_motion_is_not_an_error(self):
        result=coaching_snapshot(self.state(),self.frame())
        self.assertEqual(result['state'],'moving');self.assertIsNone(result['primary_cue'])
        item={'status':'unassessable','message':'周期证据不足','suggestion':'请完整返回起始姿势'}
        result=coaching_snapshot(self.state(),self.frame(items=[item]))
        self.assertEqual(result['state'],'evidence_insufficient');self.assertEqual(result['instruction'],item['suggestion'])

    def test_current_non_issue_evidence_withdraws_only_matching_old_cue(self):
        feedback=[{'code':'push_up.hip_high','message':'上一段髋部偏高','suggestion':'调整髋位','side':'none','start_ms':0,'end_ms':100}]
        for status,expected in [('unassessable','evidence_insufficient'),('not_observed','observing')]:
            with self.subTest(status=status):
                frame=self.frame(items=[{'code':'push_up.hip_high','status':status}])
                result=coaching_snapshot(self.state(),frame,feedback,100)
                self.assertEqual(result['state'],expected);self.assertIsNone(result['primary_cue'])
        # 中间帧尚无本周期结果时允许短暂保留；其他项目拒判不能撤掉本项已保存问题。
        self.assertEqual(coaching_snapshot(self.state(),self.frame(),feedback,100)['state'],'needs_adjustment')
        frame=self.frame(items=[{'code':'push_up.range','status':'unassessable'}])
        self.assertEqual(coaching_snapshot(self.state(),frame,feedback,100)['state'],'needs_adjustment')

    def test_pushup_body_line_reports_sustained_high_low_and_rejects_spike(self):
        for y,expected in ((.25,{'push_up.hip_high'}),(.58,{'push_up.hip_low'}),(.487719298,set())):
            frames=[geometry(i,hip_y=y) for i in range(12)]
            engine=PostureEngine('standard_push_up',None);engine.frames.extend(frames)
            items=engine._cycle({'side':'none','start_ms':0,'end_ms':1100},'side','user_setting')
            self.assertEqual({x['code'] for x in items if x['code'].startswith('push_up.hip_') and x['status']=='issue'},expected)
        frames=[geometry(i,hip_y=.25 if i==5 else .487719298) for i in range(12)]
        engine=PostureEngine('standard_push_up',None);engine.frames.extend(frames)
        self.assertFalse(any(x['status']=='issue' for x in engine._cycle({'side':'none','start_ms':0,'end_ms':1100},'side','user_setting') if x['code'].startswith('push_up.hip_')))


class CoachingRealtimeTests(unittest.TestCase):
    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp();self.manager=self.fx.app.extensions['session_manager']
    def tearDown(self):self.manager.stop();self.fx.tearDown()
    def runtime(self):
        fx=self.fx;sid=fx.create('standard_push_up')
        self.manager.claim(fx.user,fx.auth,fx.now[0]+3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
        rt=self.manager.live[sid];rt.data=fx.store.set_state(fx.user,sid,'running',s.new_id());rt.session_origin_mono=0;rt.stream_time_offset_ms=0
        rt.adapter=PostureEngine('standard_push_up',EventSource({'side':'none','start_ms':0,'end_ms':1100}))
        return sid,rt
    def consume(self,rt):
        for i in range(12):
            frame=geometry(i,hip_y=.25);quality=framing('standard_push_up',{'poses':[frame['framing']['landmarks']]});quality['ready_for_tracking']=quality['usable']
            self.fx.mono[0]=i*.1;self.manager._consume(rt,{**frame,'framing':quality})

    def test_committed_coaching_and_stale_or_missing_input_do_not_reuse_old_cue(self):
        sid,rt=self.runtime();self.consume(rt);fx=self.fx
        data=self.manager.decorate_snapshot(fx.user,rt.data)
        self.assertEqual(data['total_count'],1);self.assertEqual(data['coaching']['state'],'needs_adjustment')
        self.assertEqual(data['coaching']['primary_cue']['code'],'push_up.hip_high')
        fx.mono[0]+=3;self.assertEqual(self.manager.decorate_snapshot(fx.user,rt.data)['coaching']['state'],'waiting_for_input')
        self.manager._consume(rt,{'frame_seq':20,'captured_ms':2000,'framing':{'code':'NO_PERSON','usable':False,'landmarks':[],'person_count':0}})
        self.assertEqual(self.manager.decorate_snapshot(fx.user,rt.data)['coaching']['state'],'no_person')
        fx.mono[0]+=2;self.assertEqual(self.manager.decorate_snapshot(fx.user,rt.data)['coaching']['state'],'waiting_for_input')

    def test_failed_event_commit_never_publishes_a_correction(self):
        sid,rt=self.runtime();fx=self.fx
        def fault(stage):
            if stage=='before_commit':raise StorageError('D-FAULT coaching save')
        fx.store.fault=fault;self.consume(rt)
        data=self.manager.decorate_snapshot(fx.user,rt.data)
        self.assertEqual(data['coaching']['state'],'save_failed');self.assertIsNone(data['coaching']['primary_cue'])
        self.assertEqual(fx.store.snapshot(fx.user,sid)['total_count'],0)
        fx.store.fault=None
        fx.writer.recovery_command(fx.user,sid,fx.auth,s.new_id(),{'command':'retry_save','expected_version':rt.data['state_version']})

    def test_source_gap_withdraws_old_live_cue_without_erasing_saved_event(self):
        sid,rt=self.runtime();self.consume(rt);fx=self.fx
        self.assertEqual(self.manager.decorate_snapshot(fx.user,rt.data)['coaching']['state'],'needs_adjustment')
        frame=geometry(20,hip_y=.25)
        quality=framing('standard_push_up',{'poses':[frame['framing']['landmarks']]});quality['ready_for_tracking']=quality['usable']
        fx.mono[0]+=.1;self.manager._consume(rt,{**frame,'framing':quality})
        current=self.manager.decorate_snapshot(fx.user,rt.data)
        self.assertNotEqual(current['coaching']['state'],'needs_adjustment');self.assertEqual(current['feedback'],[])
        self.assertEqual(current['total_count'],1)
        self.assertTrue(any(i['status']=='issue' for i in fx.store.snapshot(fx.user,sid,include_details=True)['events'][0]['assessments']))


class CoachingVideoTests(unittest.TestCase):
    def _discontinuity_check(self,kind):
        fx=videos.VideoTests();fx.setUp();gate=threading.Event();entered=threading.Event();fx.gates.append(gate)
        class Blocking(videos.SyntheticPose):
            def detect(self,rgb,stamp):
                if stamp==(1000 if kind=='gap' else 300):entered.set();gate.wait(10)
                result=super().detect(rgb,stamp)
                if kind=='identity' and stamp==200:
                    # 仍完整可见但躯干投影缩至原来30%，触发原目标连续性门槛。
                    for p in result['poses'][0]:p['x']=.5+(p['x']-.5)*.3;p['y']=.5+(p['y']-.5)*.3
                return result
        class Once(videos.Events):
            def process(self,frame,context):
                if frame['frame_seq']!=1:return {}
                stamp=frame['captured_ms'];rule=rules_for('bodyweight_squat')[0]
                item={'code':'squat.range','status':'issue','reason':'','start_ms':0,'end_ms':stamp,
                      'rule_version':policy_version(),'evidence_level':'reference','body_parts':rule['body_parts'],
                      'message':rule['title'],'suggestion':rule['suggestion'],'evidence':{'scope':'D-POSE discontinuity boundary'}}
                return {'events':[{'side':'none','start_ms':0,'end_ms':stamp,'algorithm_version':'D-EVENT-video',
                                  'feature_schema_version':'D-POSE-video','assessments':[item]}]}
        try:
            fx.manager.pose_factory=Blocking;fx.manager.engine_provider=lambda action:Once()
            if kind=='gap':
                original=fx.manager.frames
                def gap_frames(path,info):
                    iterator=original(path,info)
                    try:
                        for stamp,rgb in iterator:yield (stamp+700 if stamp>=200 else stamp),rgb
                    finally:iterator.close()
                fx.manager.frames=gap_frames
            sid=fx.video();jid=fx.start(sid);self.assertTrue(entered.wait(10))
            data=fx.manager.snapshot(fx.fx.user,jid)
            self.assertIsNone(data['coaching']['primary_cue']);self.assertEqual(data['session']['total_count'],1)
            self.assertEqual(data['coaching']['state'],'multiple_people' if kind=='identity' else 'waiting_for_cycle')
            saved=fx.fx.store.snapshot(fx.fx.user,sid,include_details=True)
            self.assertTrue(any(i['status']=='issue' for i in saved['events'][0]['assessments']))
        finally:gate.set();fx.tearDown()

    def test_rejected_video_identity_has_matching_state_and_hides_old_cue(self):self._discontinuity_check('identity')

    def test_video_source_gap_withdraws_old_cue_but_keeps_saved_issue(self):self._discontinuity_check('gap')

    def test_live_video_coaching_is_committed_and_failed_save_hides_previous_cue(self):
        fx=videos.VideoTests();fx.setUp();gate=threading.Event();entered=threading.Event();fx.gates.append(gate)
        class Blocking(videos.SyntheticPose):
            def detect(self,rgb,stamp):
                if stamp==200:entered.set();gate.wait(10)
                return super().detect(rgb,stamp)
        class QualityEvents(videos.Events):
            def process(self,frame,context):
                out=super().process(frame,context)
                for e in out.get('events',[]):
                    rule=rules_for('bodyweight_squat')[0]
                    e['assessments']=[{'code':'squat.range','status':'issue','reason':'','start_ms':e['start_ms'],'end_ms':e['end_ms'],
                        'rule_version':policy_version(),'evidence_level':'reference','body_parts':rule['body_parts'],
                        'message':rule['title'],'suggestion':rule['suggestion'],'evidence':{'scope':'D-POSE controlled save boundary'}}]
                return out
        try:
            fx.manager.pose_factory=Blocking;fx.manager.engine_provider=lambda action:QualityEvents()
            sid=fx.video();jid=fx.start(sid);self.assertTrue(entered.wait(10))
            before=fx.manager.snapshot(fx.fx.user,jid)
            self.assertEqual(before['coaching']['state'],'needs_adjustment')
            self.assertEqual(before['session']['total_count'],1)
            def fault(stage):
                if stage=='before_commit':raise StorageError('D-FAULT video coaching')
            fx.fx.store.fault=fault;gate.set();done=fx.wait(jid)
            self.assertEqual(done['coaching']['state'],'save_failed');self.assertIsNone(done['coaching']['primary_cue'])
            fx.fx.store.fault=None
            response=fx.fx.post(fx.client,'/api/v1/sessions/'+sid+'/commands',{'command':'retry_save','expected_version':done['session']['state_version']})
            self.assertEqual(response.status_code,200,response.json)
        finally:gate.set();fx.tearDown()


if __name__=='__main__':unittest.main()
