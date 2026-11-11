"""取景提示/机位来源/片段隔离与保存；合成几何不作为技术正确性真值。"""
import copy,unittest
from types import SimpleNamespace
from unittest.mock import patch
from fitness_app.framing_guide import guide
from fitness_app.frame_pipeline import REQUIRED
from fitness_app.automatic_analysis import AutomaticAnalysis
from fitness_app.automatic_recording import AutomaticRecorder
from fitness_app.posture_rules import rules_for
from fitness_app import db_schema as s
import test_formal_records as fixtures


def frame():
    return {'captured_ms':0,'frame_seq':0,'width':640,'height':480,
        'framing':{'usable':True,'person_count':1,'code':'VISIBLE','landmarks':[{'valid':True} for _ in range(33)]},
        'analysis':{'posture':{'view':'side','view_basis':'estimated','items':[]}}}


class FramingGuideTests(unittest.TestCase):
    def test_unknown_and_preparation_do_not_require_action_clarification_or_report_a_fault(self):
        data=guide();self.assertFalse(data['action_clarification_required']);self.assertFalse(data['guide_is_posture_error'])
        f=frame();f['recognition']={'reason':'motion_unconfirmed'};data=guide(f)
        self.assertEqual(data['state'],'waiting_for_motion');self.assertIsNone(data['action_id'])
    def test_one_visible_side_and_upper_body_scope_do_not_require_unneeded_parts(self):
        f=frame();f['framing']['landmarks']=[{'valid':False} for _ in range(33)]
        for index in REQUIRED['conventional_deadlift'][0]:f['framing']['landmarks'][index]['valid']=True
        self.assertEqual(guide(f,'conventional_deadlift')['missing_parts'],[])
        f=frame();f['framing'].update(posture_scope='upper_body');f['framing']['landmarks'][27]['valid']=False
        self.assertNotIn('踝',guide(f,'barbell_overhead_press')['missing_parts'])
    def test_missing_parts_multiple_people_and_stale_input_are_distinct(self):
        f=frame();f['framing']['usable']=False;f['framing']['landmarks'][15]['valid']=False;f['framing']['landmarks'][16]['valid']=False
        data=guide(f,'barbell_overhead_press');self.assertEqual(data['state'],'missing_parts');self.assertIn('腕',data['missing_parts'])
        f['framing']['person_count']=2;self.assertEqual(guide(f)['state'],'multiple_people')
        self.assertEqual(guide(frame(),'conventional_deadlift',fresh=False)['state'],'waiting_for_input')
    def test_view_limit_is_not_technical_error_and_user_setting_is_labeled(self):
        f=frame();f['analysis']['posture']['view']='oblique';data=guide(f,'barbell_overhead_press')
        self.assertEqual(data['state'],'view_limited');self.assertIn('正面',data['message']);self.assertFalse(data['guide_is_posture_error'])
        f['analysis']['posture'].update(view='front',view_basis='user_setting');data=guide(f,'barbell_overhead_press','front')
        self.assertEqual(data['requested_view_source'],'user_provided');self.assertEqual(data['state'],'visible')
        f['analysis']['posture']['view_basis']='view_mismatch';self.assertEqual(guide(f,'barbell_overhead_press','front')['state'],'view_mismatch')


class FramingIntegrationTests(unittest.TestCase):
    def setUp(self):self.fx=fixtures.RecordTests();self.fx.setUp()
    def tearDown(self):self.fx.app.extensions['preview_manager'].stop();self.fx.tearDown()
    def test_preview_accepts_optional_view_without_requiring_an_action_and_keeps_idempotency(self):
        manager=self.fx.app.extensions['preview_manager'];payload={'page_id':s.new_id(),'recognition_mode':'auto','coaching_view':'front'};key=s.new_id()
        with patch.object(manager,'start'):
            a=self.fx.post(self.fx.client,'/api/v1/previews',payload,key);self.assertEqual(a.status_code,201,a.json)
            self.assertIsNone(manager.active['action_id']);self.assertEqual(manager.active['automatic_analysis'].coaching_view,'front')
            b=self.fx.post(self.fx.client,'/api/v1/previews',payload,key);self.assertEqual(a.json['data']['preview_id'],b.json['data']['preview_id'])
            conflict=self.fx.post(self.fx.client,'/api/v1/previews',{**payload,'coaching_view':'side'},key);self.assertEqual(conflict.status_code,409)
            bad=self.fx.post(self.fx.client,'/api/v1/previews',{**payload,'coaching_view':[]});self.assertEqual(bad.status_code,422)
        response=self.fx.client.get('/api/v1/framing-guide?action_id=barbell_overhead_press');self.assertEqual(response.status_code,200);self.assertTrue(response.json['data']['items'])
    def test_confirmed_view_and_exercise_basis_are_persisted_with_camera_record(self):
        recorder=AutomaticRecorder(self.fx.store,self.fx.user,self.fx.auth,s.new_id())
        analysis=SimpleNamespace(segment_id=s.new_id(),action='barbell_overhead_press',version='manual-selection',active_ms=0,coaching_view='front',basis='user_selected')
        recorder.process(analysis,{}, {'captured_ms':0});rule=rules_for(analysis.action)[0];analysis.active_ms=1000
        item={'code':rule['code'],'status':'unassessable','side':'none','reason':'rack_phase_unconfirmed','start_ms':0,'end_ms':1000,
            'rule_version':rule['rule_version'],'evidence_level':'reference','body_parts':rule['body_parts'],'message':'工程夹具','suggestion':rule['suggestion'],'evidence':{}}
        result=recorder.process(analysis,{'posture_findings':[item]}, {'captured_ms':1000});self.assertEqual(result['save_state'],'clean',result)
        data=self.fx.store.snapshot(self.fx.user,result['saved_record_id']);self.assertEqual(data['config']['coaching_view'],'front');self.assertEqual(data['config']['exercise_basis'],'user_selected')
        recorder.close('preview_closed')
    def test_router_passes_optional_view_to_engine(self):
        class Engine:
            counting_available=False
            def __init__(self,action):self.action=action;self.context=None
            def process(self,f,context):self.context=context;return {}
            def reset(self,reason):pass
        router=AutomaticAnalysis(Engine,coaching_view='front');f=frame()
        router.process(f,{'available':True,'state':'recognized','action_id':'barbell_overhead_press','model_version':'fixture'})
        self.assertEqual(router.engine.context['coaching_view'],'front')


if __name__=='__main__':unittest.main()
