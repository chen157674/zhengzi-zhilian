"""自动分类后分析路由；受控分类不作为12动作真实识别成绩。"""
import copy
import unittest
from unittest.mock import patch

from fitness_app.automatic_analysis import AutomaticAnalysis
from fitness_app.automatic_recognition import ActionRecognizer,TARGET_ACTIONS
from fitness_app.posture_engine import create_analysis_engine
from fitness_app.frame_pipeline import analysis_framing
from fitness_app.db_schema import new_id
from test_automatic_recognition import moving,Predicted
import test_formal_records as records
import test_formal_preview as preview


def recognized(action='conventional_deadlift',**values):
    return {'available':True,'state':'recognized','action_id':action,'model_version':'fixture',**values}


def frame(i=0):
    f=moving(i);f['framing'].update(usable=True,code='VISIBLE');return f


class Engine:
    counting_available=True
    def __init__(self,action):self.action=action;self.calls=0;self.resets=[]
    def reset(self,reason):self.resets.append(reason)
    def process(self,f,ctx):
        self.calls+=1
        return {'events':[{'side':'none','assessments':[{
            'code':'deadlift.extension','status':'issue','message':'顶端屈曲','suggestion':'自然站直',
            'body_parts':['hip'],'start_ms':f['captured_ms'],'end_ms':f['captured_ms'],
            'rule_version':'fixture','evidence':{}}]}],'observation':{}}


class AutomaticAnalysisTests(unittest.TestCase):
    def test_motion_unconfirmed_closes_previous_analysis_without_reusing_error_or_count(self):
        from unittest.mock import Mock
        recorder=Mock();recorder.process.return_value=None;recorder.snapshot.return_value=None
        router=AutomaticAnalysis(Engine,recorder=recorder);before=router.process(frame(0),recognized());engine=router.engine
        self.assertIsNotNone(before['coaching']['primary_cue'])
        unknown=router.process(frame(1),{'available':True,'state':'searching','action_id':None,'model_version':'fixture-motion',
            'reason':'motion_unconfirmed','message':'尚未确认动作变化'})
        self.assertIsNone(unknown['coaching']['primary_cue']);self.assertIsNone(unknown['automatic_analysis']['action_id'])
        self.assertIsNone(unknown['automatic_analysis']['reference_completed_cycles']);self.assertIsNone(router.engine)
        self.assertIn('motion_unconfirmed',engine.resets);recorder.close.assert_called_with('motion_unconfirmed')

    def test_old_recognition_packet_cannot_publish_previous_confirmation(self):
        recognizer=ActionRecognizer();recognizer.model=Predicted();recognizer.reason=None
        for i in range(29):recognizer.process(frame(i))
        self.assertEqual(recognizer.confirmed,'conventional_deadlift')
        stale=recognizer.process(frame(10))
        self.assertIsNone(stale['action_id']);self.assertIsNone(stale['score'])
        self.assertEqual(stale['reason'],'stale_frame')
        self.assertEqual(recognizer.confirmed,'conventional_deadlift')

    def test_waiting_states_distinguish_no_person_multiple_target_and_stale_input(self):
        router=AutomaticAnalysis()
        for count,expected in ((0,'no_person'),(2,'multiple_people')):
            f=frame(count);f['framing']['person_count']=count
            result=router.process(f,recognized(state='searching',action_id=None,reason='target_unavailable'))
            self.assertEqual(result['coaching']['state'],expected)
        result=router.waiting(frame(3),recognized(),'target_changed')
        self.assertIn('目标发生变化',result['coaching']['message'])
        self.assertEqual(router.waiting(frame(3),recognized(),'stale_frame')['coaching']['state'],'waiting_for_input')

    def test_withdrawn_issue_does_not_reappear_on_next_frame_without_assessment(self):
        router=AutomaticAnalysis(Engine)
        self.assertIsNotNone(router.process(frame(0),recognized())['coaching']['primary_cue'])
        normal={'events':[],'observation':{'posture':{'items':[{'code':'deadlift.extension','status':'not_observed','side':'none'}]}}}
        with patch.object(router.engine,'process',return_value=normal):
            self.assertIsNone(router.process(frame(1),recognized())['coaching']['primary_cue'])
        with patch.object(router.engine,'process',return_value={'events':[],'observation':{}}):
            self.assertIsNone(router.process(frame(2),recognized())['coaching']['primary_cue'])

    def test_all_twelve_route_to_matching_real_engine_without_unconfirmed_counts(self):
        self.assertEqual(len(TARGET_ACTIONS),12)
        for action in TARGET_ACTIONS:
            with self.subTest(action=action):
                router=AutomaticAnalysis();f=frame()
                f['framing']=analysis_framing(action,{'poses':[f['framing']['landmarks']]})
                # 检查动作路由本身；不根据这组共用几何声称真实动作正确。
                f['framing']['usable']=True
                result=router.process(f,recognized(action))
                self.assertEqual(router.engine.action,action)
                self.assertEqual(result['automatic_analysis']['action_id'],action)
                self.assertEqual(result['coaching']['exercise_basis'],'automatically_recognized')
                if not router.engine.counting_available:
                    self.assertIsNone(result['automatic_analysis']['reference_completed_cycles'])

    def test_switch_uncertainty_loss_and_model_change_never_reuse_old_feedback(self):
        router=AutomaticAnalysis(Engine)
        a=router.process(frame(0),recognized());engine=router.engine
        self.assertEqual(a['coaching']['state'],'needs_adjustment')
        self.assertEqual(a['coaching']['primary_cue']['evidence']['variant_confirmation'],'automatically_inferred')
        self.assertEqual(a['automatic_analysis']['reference_completed_cycles'],1)
        unknown=router.process(frame(1),recognized(state='confirming',action_id=None))
        self.assertIsNone(unknown['coaching']['primary_cue']);self.assertTrue(engine.resets)
        b=router.process(frame(2),recognized('bodyweight_squat'))
        self.assertNotEqual(a['automatic_analysis']['segment_id'],b['automatic_analysis']['segment_id'])
        self.assertIsNone(b['coaching']['primary_cue'])
        f=frame(3);f['framing']['usable']=False
        self.assertIsNone(router.process(f,recognized('bodyweight_squat'))['automatic_analysis']['action_id'])
        c=router.process(frame(4),recognized());old=router.engine
        d=router.process(frame(5),recognized(model_version='fixture-new'))
        self.assertNotEqual(c['automatic_analysis']['segment_id'],d['automatic_analysis']['segment_id'])
        self.assertEqual(old.resets,['action_or_model_changed'])
        self.assertIsNone(router.process(frame(5),recognized())['coaching']['primary_cue'])
        self.assertIsNone(router.engine)

    def test_missing_target_and_long_gap_reset_but_no_count_carryover(self):
        router=AutomaticAnalysis(Engine)
        initial=router.process(frame(0),recognized());old=router.engine
        late=router.process(frame(10),recognized())
        self.assertNotEqual(initial['automatic_analysis']['segment_id'],late['automatic_analysis']['segment_id'])
        self.assertEqual(late['automatic_analysis']['reference_completed_cycles'],1)
        self.assertEqual(old.resets,['source_gap'])
        f=frame(11);f['framing'].update(person_count=2,code='TARGET_AMBIGUOUS')
        result=router.process(f,recognized())
        self.assertIsNone(result['coaching']['primary_cue']);self.assertIsNone(router.engine)
        with patch.object(router,'engine_provider') as factory:
            router.process(frame(12),recognized(available=False))
            factory.assert_not_called()

    def test_recognizer_rejects_identity_jump_and_rewarms(self):
        recognizer=ActionRecognizer();recognizer.model=Predicted();recognizer.reason=None
        for i in range(29):result=recognizer.process(frame(i))
        self.assertEqual(result['action_id'],'conventional_deadlift')
        f=frame(29)
        for index in (11,12,23,24):f['framing']['landmarks'][index]['x']*=.01
        result=recognizer.process(f)
        self.assertIsNone(result['action_id']);self.assertEqual(result['reason'],'target_changed')
        self.assertEqual(len(recognizer.rows),0)
        self.assertIsNone(recognizer.process(frame(30))['action_id'])

    def test_http_auto_analysis_expiry_and_release_have_no_saved_preview_record(self):
        fx=records.RecordTests();fx.setUp();manager=fx.app.extensions['preview_manager']
        try:
            with patch.object(manager,'start'):
                data=fx.post(fx.client,'/api/v1/previews',{'page_id':new_id()}).json['data']
                pid=data['preview_id'];item=manager.active
                item['automatic_analysis']=AutomaticAnalysis(Engine)
                with patch.object(item['recognizer'],'process',return_value=recognized()):
                    meta={'stream_epoch':data['stream_epoch'],'frame_seq':0,'captured_ms':0,'width':32,'height':24}
                    manager.submit(fx.user,pid,data['preview_token'],meta,preview.jpeg())
                    manager._publish(*manager._next(),{'poses':[frame()['framing']['landmarks']]},1)
                result=fx.client.get('/api/v1/previews/'+pid).json['data']['result']
                self.assertEqual(result['automatic_analysis']['action_id'],'conventional_deadlift')
                self.assertEqual(result['coaching']['exercise_basis'],'automatically_recognized')
                self.assertEqual(result['skeleton']['highlight']['evidence_time_base'],'source_ms')
                fx.mono[0]+=1.1
                stale=fx.client.get('/api/v1/previews/'+pid).json['data']['result']
                self.assertEqual(stale['automatic_analysis']['reason'],'stale_frame')
                self.assertIsNone(stale['coaching']['primary_cue']);self.assertEqual(stale['skeleton']['joints'],[])
                self.assertEqual(fx.client.get('/api/v1/records').json['data']['total'],0)
                manager.release(fx.user,pid,{'preview_token':data['preview_token'],'stream_epoch':data['stream_epoch']},new_id())
                self.assertIsNone(manager.history[pid]['automatic_analysis'])
        finally:
            for key in ('video_jobs','session_manager','preview_manager','media_inputs'):fx.app.extensions[key].stop()
            fx.tearDown()


if __name__=='__main__':unittest.main()
