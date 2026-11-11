"""复测阈值、可比性、本人来源与新采集保存；合成观察不作为姿态准确率。"""
import copy,unittest
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import update
from fitness_app import db_schema as s
from fitness_app.database import json_text,ConflictError
from fitness_app.auth import APIError
from fitness_app.retests import Retests,evaluate
from fitness_app.posture_rules import rules_for
from fitness_app.automatic_recording import AutomaticRecorder
import test_formal_records as fixtures


def comparison(unit='ms',status='not_observed',amount=2500,unknown=0):
    counted=unit=='cycles';origin={'session_id':s.new_id(),'action_id':'fixture','counting_available':counted,'unit':'reps'}
    target={**origin,'session_id':s.new_id(),'source':'camera','status':'ended_early','active_ms':amount+unknown,'posture_observations':[],'events':[]}
    evidence={'code':'fixture.rule','rule_version':'v1','side':'none','title':'工程判项','evidence':{'view':'front'}}
    item={'code':'fixture.rule','rule_version':'v1','side':'none','status':status,'evidence':{'view':'front'}}
    if counted:
        target['events']=[{'event_seq':i+1,'side':'none','assessments':[{**item,'status':status if i<amount else 'unassessable'}]} for i in range(amount+unknown)]
    else:
        target['posture_observations']=[{**item,'start_ms':0,'end_ms':amount}]
        if unknown:target['posture_observations'].append({**item,'status':'unassessable','start_ms':amount,'end_ms':amount+unknown})
    return origin,target,evidence


class EvaluationTests(unittest.TestCase):
    def test_sufficient_not_reobserved_is_a_single_reference_item_not_overall_correct(self):
        for unit,n in [('ms',2500),('cycles',3)]:
            data=evaluate(*comparison(unit,amount=n));self.assertEqual(data['state'],'not_reobserved');self.assertIsNone(data['improvement_percentage']);self.assertIsNone(data['independent_specific_error_accuracy'])
        self.assertEqual(evaluate(*comparison(status='issue'))['state'],'reobserved')
    def test_unknown_gaps_and_small_samples_cannot_become_success(self):
        for args in [('ms',1900,0),('ms',2500,1000),('cycles',2,0),('cycles',3,2)]:
            unit,n,unknown=args;self.assertEqual(evaluate(*comparison(unit,amount=n,unknown=unknown))['state'],'insufficient_evidence')
        a,b,e=comparison();b['active_ms']=10000;self.assertEqual(evaluate(a,b,e)['state'],'insufficient_evidence')
    def test_wrong_action_view_rule_side_and_active_record_do_not_claim_success(self):
        for name,value in [('action_id','other'),('source','video'),('included_in_statistics',False)]:
            a,b,e=comparison();b[name]=value;self.assertEqual(evaluate(a,b,e)['state'],'not_comparable')
        for name,value in [('rule_version','v2'),('side','left')]:
            a,b,e=comparison();b['posture_observations'][0][name]=value;self.assertNotEqual(evaluate(a,b,e)['state'],'not_reobserved')
        a,b,e=comparison();b['posture_observations'].append({**b['posture_observations'][0],'evidence':{'view':'side'}});self.assertEqual(evaluate(a,b,e)['state'],'not_comparable')
        a,b,e=comparison();b['status']='running';self.assertEqual(evaluate(a,b,e)['state'],'collecting')


class RetestIntegrationTests(unittest.TestCase):
    def setUp(self):self.fx=fixtures.RecordTests();self.fx.setUp();self.service=Retests(self.fx.store)
    def tearDown(self):self.fx.app.extensions['preview_manager'].stop();self.fx.tearDown()
    def make(self,status='issue',end=2500,ref=None):
        sid=self.fx.running('barbell_overhead_press');rule=rules_for('barbell_overhead_press')[0]
        if ref:
            data=self.fx.store.snapshot(self.fx.user,sid)
            with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==sid).values(config_json=json_text({**data['config'],'retest_origin':ref})))
        item={k:rule[k] for k in ('code','rule_version','body_parts','suggestion')}
        item.update(status=status,side='none',start_ms=0,end_ms=end,message='工程复测观察',reason='required_parts_or_identity_missing' if status=='unassessable' else '',evidence_level='reference',evidence={'view':'front','value':.4})
        self.fx.store.append_posture(self.fx.user,sid,1,[item],self.fx.auth,end);self.fx.finish(sid);return sid
    def origin(self,sid):return self.service.options(self.fx.user,sid,self.fx.auth)['options'][0]['origin']

    def test_owned_source_is_validated_and_preview_keeps_auto_mode_and_reference(self):
        sid=self.make();ref=self.origin(sid);self.assertEqual(self.service.prepare(self.fx.user,self.fx.auth,ref),ref)
        manager=self.fx.app.extensions['preview_manager']
        with patch.object(manager,'start'):
            response=self.fx.post(self.fx.client,'/api/v1/previews',{'page_id':s.new_id(),'recognition_mode':'auto','save_training':True,'retest_origin':ref})
            self.assertEqual(response.status_code,201,response.json);self.assertIsNone(manager.active['action_id']);self.assertEqual(manager.active['recorder'].retest_origin,ref)
            self.assertEqual(manager.active['automatic_analysis'].focus_code,'overhead_press.elbow')
        other=self.fx.app.test_client();self.fx.login(other,'other_user')
        self.assertEqual(other.get('/api/v1/records/'+sid+'/retest-options').status_code,404)
        with other.session_transaction() as state:auth=state['_user_id']
        with self.assertRaises(APIError):self.service.prepare(self.fx.other,auth,ref)
        with self.assertRaises(ConflictError):self.service.prepare(self.fx.user,self.fx.auth,{**ref,'expected_version':ref['expected_version']+1})
        with self.assertRaises(APIError):self.service.prepare(self.fx.user,self.fx.auth,{**ref,'evidence_id':'e:'+'0'*24})

    def test_result_survives_read_reload_but_source_deletion_removes_comparison(self):
        source=self.make();target=self.make('not_observed',ref=self.origin(source));response=self.fx.client.get('/api/v1/records/'+target)
        self.assertEqual(response.status_code,200,response.json);self.assertEqual(response.json['data']['retest']['state'],'not_reobserved')
        version=response.json['data']['state_version'];self.assertEqual(self.fx.client.get('/api/v1/records/'+target).json['data']['state_version'],version)
        source_data=self.fx.store.snapshot(self.fx.user,source);self.fx.store.delete_records(self.fx.user,self.fx.auth,[source],{source:source_data['state_version']},s.new_id())
        summary=self.fx.client.get('/api/v1/records/'+target).json['data']['retest'];self.assertEqual(summary['state'],'source_unavailable');self.assertNotIn('title',summary)

    def test_unassessable_demo_or_superseded_source_is_not_offered(self):
        a=self.make('unassessable');self.assertEqual(self.service.options(self.fx.user,a)['options'],[])
        for hidden in (False,True):
            sid=self.make();data=self.fx.store.snapshot(self.fx.user,sid)
            with self.fx.db.transaction() as c:
                changes={'config_json':json_text({**data['config'],'included_in_statistics':False})} if hidden else {'source':'demo'}
                c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==sid).values(**changes))
            self.assertEqual(self.service.options(self.fx.user,sid)['options'],[])

    def test_automatic_recorder_saves_origin_and_ai_binds_both_records(self):
        source=self.make();ref=self.origin(source);recorder=AutomaticRecorder(self.fx.store,self.fx.user,self.fx.auth,s.new_id(),retest_origin=ref)
        analysis=SimpleNamespace(segment_id=s.new_id(),action='barbell_overhead_press',version='fixture',active_ms=0,coaching_view='front',basis='automatically_recognized')
        recorder.process(analysis,{}, {'captured_ms':1000});analysis.active_ms=2500
        original=self.fx.store.snapshot(self.fx.user,source,True)['posture_observations'][0]
        item={**original,'status':'not_observed','start_ms':1000,'end_ms':3500};item.pop('id',None)
        result=recorder.process(analysis,{'posture_findings':[item]}, {'captured_ms':3500});self.assertEqual(result['save_state'],'clean',result)
        recorder.close('preview_closed');target=result['saved_record_id'];data=self.fx.store.snapshot(self.fx.user,target,True)
        self.assertEqual(data['config']['retest_origin'],ref);self.assertEqual(self.service.summary(self.fx.user,data)['state'],'not_reobserved')
        builder=self.fx.app.extensions['ai_jobs'].builder
        bundle=builder.build(self.fx.user,self.fx.auth,{'context_kind':'record','context_id':target,'history_scope':'none','text':'复测结果怎样？'})
        self.assertEqual(bundle['facts']['selected_records'][0]['retest']['state'],'not_reobserved')
        self.assertEqual(bundle['read_scope']['retest_source_records'],1)
        self.assertTrue(any(d['kind']=='retest_source' and d['id']==source for d in bundle['reference']['dependencies']))
        source_data=self.fx.store.snapshot(self.fx.user,source);self.fx.store.delete_records(self.fx.user,self.fx.auth,[source],{source:source_data['state_version']},s.new_id())
        self.assertFalse(builder.current(self.fx.user,self.fx.auth,bundle['reference']))

    def test_source_deleted_before_first_save_freezes_visible_batch_without_forged_result(self):
        source=self.make();ref=self.origin(source);recorder=AutomaticRecorder(self.fx.store,self.fx.user,self.fx.auth,s.new_id(),retest_origin=ref)
        analysis=SimpleNamespace(segment_id=s.new_id(),action='barbell_overhead_press',version='fixture',active_ms=1000)
        original=self.fx.store.snapshot(self.fx.user,source,True);item=original['posture_observations'][0]
        self.fx.store.delete_records(self.fx.user,self.fx.auth,[source],{source:original['state_version']},s.new_id())
        state=recorder.process(analysis,{'posture_findings':[item]}, {'captured_ms':0})
        self.assertEqual(state['save_state'],'save_failed');self.assertIsNone(state['saved_record_id']);self.assertTrue(state['pending'])

    def test_cannot_mix_retest_sources_within_a_run_or_forge_marker_through_public_session_create(self):
        ref=self.origin(self.make());manager=self.fx.app.extensions['preview_manager']
        with patch.object(manager,'start'):
            response=self.fx.post(self.fx.client,'/api/v1/previews',{'page_id':s.new_id(),'recognition_mode':'auto','save_training':True,'retest_origin':ref})
            created=response.json['data'];manager.release(self.fx.user,created['preview_id'],{'preview_token':created['preview_token'],'stream_epoch':created['stream_epoch']},s.new_id())
            next_response=self.fx.post(self.fx.client,'/api/v1/previews',{'page_id':s.new_id(),'recognition_mode':'auto','save_training':True,'training_id':created['training_id']})
            self.assertEqual(next_response.status_code,409,next_response.json)
        from fitness_app.catalogue import get_action
        forged=self.fx.post(self.fx.client,'/api/v1/sessions',{'action_id':'barbell_overhead_press','capability_version':get_action('barbell_overhead_press')['capability_version'],'source':'camera','mode':'free','target':None,'retest_origin':ref})
        self.assertEqual(forged.status_code,400,forged.json)

    def test_retest_coaching_focus_does_not_replace_the_selected_item_with_another_fault(self):
        from fitness_app.automatic_analysis import AutomaticAnalysis
        rules=rules_for('forearm_plank');selected=rules[-1]['code']
        class Engine:
            counting_available=False
            def __init__(self,action):pass
            def reset(self,reason):pass
            def process(self,frame,context):
                items=[{**r,'status':'issue','side':'none','start_ms':0,'end_ms':1000,'message':r['title'],'evidence':{'view':'side'}} for r in rules]
                return {'posture_findings':items,'observation':{'posture':{'view':'side','items':items}}}
        pipeline=AutomaticAnalysis(Engine,focus_code=selected)
        result=pipeline.process({'captured_ms':1000,'frame_seq':1,'framing':{'usable':True,'person_count':1}},
            {'available':True,'state':'recognized','action_id':'forearm_plank','model_version':'fixture'})
        self.assertEqual(result['coaching']['primary_cue']['code'],selected)
        self.assertEqual(len(result['analysis']['posture']['items']),len(rules))


if __name__=='__main__':unittest.main()
